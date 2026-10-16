#!/usr/bin/env python3
"""MFP CAN telemetry decoder (host side, pure Python, no python-can needed).

AUTHORITY: mfp-beta-fw (current beta firmware), not the alpha docs.
  - IDs:      head/components/mfp_can/include/mfp_can.h  (MFP_CAN_ID_*)
  - Payloads: head/components/mfp_core/src/mfp_wire.c    (mfp_wire_level/...)
  - Status:   head/components/mfp_core/include/mfp/mfp_status.h (MFP_ST_*)
  - HALL_FAULT: head/components/mfp_hallmon/include/mfp_hallmon.h, DESIGN 12.1
29-bit proprietary-B IDs 0x18FFnnSA, 250 kbit/s, DLC 8, little-endian
scaled integers. Bench SA = 0x10.

CONTRACT (mfp_can.h): LEVEL is FULLY CORRECTED on the probe. ATTITUDE is
telemetry only; never re-apply tilt from it.
"""
import struct

ID_LEVEL = 0x18FF1000
ID_FAST = 0x18FF1100
ID_ATTITUDE = 0x18FF1200
ID_HEALTH_A = 0x18FF1300
ID_HEALTH_B = 0x18FF1400
ID_HALL_FAULT = 0x18FF1500

NAMES = {
    ID_LEVEL: "LEVEL", ID_FAST: "FAST", ID_ATTITUDE: "ATTITUDE",
    ID_HEALTH_A: "HEALTH_A", ID_HEALTH_B: "HEALTH_B", ID_HALL_FAULT: "HALL_FAULT",
}

NO_U16 = 0xFFFF      # MFP_WIRE_NO_VALUE_U16 -> NaN
NO_I16 = -32768      # MFP_WIRE_NO_VALUE_I16 -> "not measurable"

STATUS_BITS = {      # LEVEL byte 6 carries status & 0xFF
    0: "OK", 1: "FLOAT_STUCK", 2: "FIELD_ANOMALY", 3: "SENSOR_FAULT",
    4: "TILT_EXCESS", 5: "UNCALIBRATED", 6: "SG_DEFAULT", 7: "NO_ATTITUDE",
}

HALL_CODES = {
    0x00: "NONE", 0x01: "RAIL_HI", 0x02: "RAIL_LO", 0x03: "RANGE",
    0x04: "STUCK", 0x05: "NOISE", 0x06: "INCONSISTENT", 0x07: "MISSING",
    0x10: "STRIP_LOST", 0x11: "STRIP_CRC", 0x12: "STRIP_STALE",
}


def _u16(v, scale):
    return None if v == NO_U16 else round(v * scale, 6)


def _i16(v, scale):
    return None if v == NO_I16 else round(v * scale, 6)


def split_id(arb_id):
    """Return (pgn_base, sa) for an extended ID, or (None, None)."""
    base = arb_id & 0x1FFFFF00
    return (base, arb_id & 0xFF) if base in NAMES else (None, None)


def status_text(st):
    return "|".join(n for b, n in STATUS_BITS.items() if st & (1 << b)) or "NONE"


def decode(arb_id, data):
    """Decode one frame. Returns (name, sa, dict) or None if not an MFP frame.
    Short frames (DLC != 8) return a dict with 'error'."""
    base, sa = split_id(arb_id)
    if base is None:
        return None
    name = NAMES[base]
    data = bytes(data)
    if len(data) != 8:
        return name, sa, {"error": f"dlc={len(data)}"}
    if base == ID_LEVEL:
        vol, lvl, flow, st, seq = struct.unpack("<HHhBB", data)
        return name, sa, {
            "level_vol_gal": _u16(vol, 0.1), "level_in": _u16(lvl, 0.001),
            "level_flow_gpm": _i16(flow, 0.1), "status": st,
            "status_text": status_text(st), "seq": seq,
        }
    if base == ID_FAST:
        vol, z, slosh, _rsv = struct.unpack("<HHHH", data)
        return name, sa, {
            "fast_vol_gal": _u16(vol, 0.1), "z_null_in_raw": _u16(z, 0.001),
            "slosh_amp_in": _u16(slosh, 0.001),
        }
    if base == ID_ATTITUDE:
        p, r, t, vib = struct.unpack("<hhhH", data)
        return name, sa, {
            "pitch_deg": _i16(p, 0.01), "roll_deg": _i16(r, 0.01),
            "mfp_temp_c": _i16(t, 0.1), "vib_energy": _u16(vib, 0.01),
        }
    if base == ID_HEALTH_A:
        return name, sa, {"sensor_fault_map": data[0], "health_seq": data[7]}
    if base == ID_HEALTH_B:
        amp, res, up, rc, fw, tec, rec = struct.unpack("<BBHBBBB", data)
        return name, sa, {
            "amplitude_pct": amp, "fit_residual_pct": res, "uptime_min": up,
            "reset_count": rc, "fw_rev": fw, "tec": tec, "rec": rec,
        }
    # HALL_FAULT
    strip, sensor, code, flags, count, n_active, detail = struct.unpack("<BBBBHBB", data)
    return name, sa, {
        "hall_strip": strip, "hall_sensor": sensor, "hall_code": code,
        "hall_code_text": HALL_CODES.get(code, f"0x{code:02X}"),
        "hall_active": bool(flags & 1), "hall_test": bool(flags & 2),
        "hall_excluded": bool(flags & 4), "hall_count": count,
        "hall_n_active": n_active, "hall_detail": detail,
    }


# ---- encoders (mirror mfp_wire.c) used only by the simulator/tests ----
def _enc_u16(v, scale):
    if v is None or v != v:
        return NO_U16
    return int(min(max(v / scale, 0.0), 65534.0) + 0.5)


def _enc_i16(v, scale):
    if v is None or v != v:
        return NO_I16
    s = min(max(v / scale, -32767.0), 32767.0)
    return int(s + 0.5) if s >= 0 else int(s - 0.5)


def encode_level(vol_gal, level_in, flow_gpm, status, seq):
    return struct.pack("<HHhBB", _enc_u16(vol_gal, 0.1), _enc_u16(level_in, 0.001),
                       _enc_i16(flow_gpm, 0.1), status & 0xFF, seq & 0xFF)


def encode_fast(vol_gal, z_null_in, slosh_in):
    return struct.pack("<HHHH", _enc_u16(vol_gal, 0.1), _enc_u16(z_null_in, 0.001),
                       _enc_u16(slosh_in, 0.001), 0)


def encode_attitude(pitch, roll, temp_c, vib):
    return struct.pack("<hhhH", _enc_i16(pitch, 0.01), _enc_i16(roll, 0.01),
                       _enc_i16(temp_c, 0.1), _enc_u16(vib, 0.01))
