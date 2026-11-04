"""swbox r2 CAN consumer (replaces the V1.9 /dev/ttyAMA0 text link).

Source of truth: swbox-fw docs/swbox.dbc + README (PROTO_VER 2), 250 kbit/s,
29-bit IDs, swbox source address 0x20. Frames are translated into the same
text tokens the old serial link sent ('OV', 'PS', 'TU', 'FILL', 'MIX', '+1',
'-1', '+10', '-10', 'OK', 'BOOT') so dashboard.handle_switchbox_line() keeps
one dispatcher for both transports. No pump logic lives here: PS is passed
on exactly like the serial 'PS' line, and the host's only reply is PS_ACK.

Raw SocketCAN through the socket module (no python-can dependency).
"""
import socket
import struct
import time

SWBOX_SA = 0x20
HOST_SA = 0xF9                      # tool SA (any is accepted, DBC example uses 0xF9)

ID_PS = 0x04FF3320
ID_STATUS = 0x18FF3020
ID_EVENT = 0x18FF3120
ID_ENC = 0x18FF3220
ID_BOOT = 0x18FF3420
ID_CMD_BASE = 0x18EF0000


def cmd_req_id(tool=HOST_SA):
    return ID_CMD_BASE | (SWBOX_SA << 8) | tool


def cmd_rep_id(tool=HOST_SA):
    return ID_CMD_BASE | (tool << 8) | SWBOX_SA


OP_PS_ACK = 0x05
OP_GET_STATE = 0x03

SRC_OVR, SRC_PS, SRC_FILL_MIX, SRC_TU, SRC_ENC_SW, SRC_RSVD5, SRC_CANREC = range(7)
EV_RELEASE, EV_PRESS, EV_STUCK, EV_STUCK_CLEARED = range(4)

HEARTBEAT_TIMEOUT_S = 3.0           # DBC: STATUS 1 Hz, receiver timeout 3 s
ENC_MAX_STEP = 1000                 # |delta| sanity cap (counts) between two frames
PS_ACK_RETRY_S = 0.5

# struct can_frame: u32 can_id, u8 len, 3 pad, 8 data
CAN_FRAME_FMT = "=IB3x8s"
CAN_FRAME_SIZE = struct.calcsize(CAN_FRAME_FMT)
CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CAN_EFF_MASK = 0x1FFFFFFF


def pack_frame(can_id, data):
    data = bytes(data)
    return struct.pack(CAN_FRAME_FMT, (can_id & CAN_EFF_MASK) | CAN_EFF_FLAG, len(data), data.ljust(8, b"\0"))


def unpack_frame(raw):
    can_id, dlc, data = struct.unpack(CAN_FRAME_FMT, raw[:CAN_FRAME_SIZE])
    if can_id & (CAN_RTR_FLAG | CAN_ERR_FLAG) or not can_id & CAN_EFF_FLAG:
        return None, b""
    return can_id & CAN_EFF_MASK, data[:min(dlc, 8)]


# ---- decoders (bit layout per swbox.dbc, little-endian) ----------------------

def decode_status(d):
    d = bytes(d).ljust(8, b"\0")
    return {
        "btn_ovr": d[0] & 1, "btn_ps": (d[0] >> 1) & 1, "fill_mix": (d[0] >> 2) & 1,
        "btn_tu": (d[0] >> 3) & 1, "enc_sw": (d[0] >> 4) & 1,
        "inputs_valid": d[1] & 1, "can_fault": (d[1] >> 3) & 1, "maint": (d[1] >> 4) & 1,
        "ps_latched": (d[1] >> 5) & 1, "can_flags": d[2] & 0x0F, "stuck": (d[2] >> 4) & 0x07,
        "enc_count": struct.unpack_from("<h", d, 3)[0], "seq": d[5], "ps_seq": d[6],
    }


def decode_event(d):
    d = bytes(d).ljust(8, b"\0")
    return {"source": d[0], "state": d[1], "seq": d[2], "t_ms": struct.unpack_from("<I", d, 3)[0],
            "long": d[7] & 1, "enc_moved": (d[7] >> 1) & 1}


def decode_ps(d):
    d = bytes(d).ljust(8, b"\0")
    return {"state": d[0], "ps_seq": d[1], "copy": d[2], "latched": d[3] & 1, "long": (d[3] >> 1) & 1,
            "t_ms": struct.unpack_from("<I", d, 4)[0]}


def decode_enc(d):
    d = bytes(d).ljust(8, b"\0")
    return {"count": struct.unpack_from("<h", d, 0)[0], "last_step": struct.unpack_from("<b", d, 2)[0],
            "modifier": d[3], "seq": d[4]}


def decode_boot(d):
    d = bytes(d).ljust(8, b"\0")
    return {"proto": d[0], "board_rev": d[1], "fw": (d[2], d[3], d[4]), "reset_reason": d[5]}


def int16_delta(new, old):
    return ((new - old + 0x8000) & 0xFFFF) - 0x8000


def encoder_tokens(delta, modifier):
    """Split a count delta into the V1.9 '+1'/'+10' tokens. The count already
    has x10 applied while the modifier is held, so 10-steps go out as '+10'."""
    sign = "+" if delta > 0 else "-"
    n = abs(delta)
    out = []
    if modifier:
        out += [f"{sign}10"] * (n // 10)
        n %= 10
    out += [f"{sign}1"] * n
    return out


class SwitchboxCanConsumer:
    """Stateful frame -> token translator.

    dispatch(token): called with the V1.9 token (same as one serial line).
    send(can_id, data): transmit a frame (PS_ACK). May be None.
    log(msg): diagnostics.
    """

    def __init__(self, dispatch, send=None, log=None, clock=time.monotonic):
        self.dispatch = dispatch
        self.send = send
        self.log = log or (lambda m: None)
        self.clock = clock
        self.reset()

    def reset(self):
        self.enc_count = None
        self.fill_mix = None
        self.ps_handled = set()        # (ps_seq, state) already dispatched/handled
        self.last_ps_seq = None        # ps_seq of the last press we dispatched
        self.ps_ack_seq = None         # seq still waiting for latched=0
        self.ps_ack_sent_at = 0.0
        self.event_seq = None
        self.status_seen = False
        self.last_status_at = None
        self.boot = None

    # -- helpers --
    def _ack_ps(self, seq, force=False):
        now = self.clock()
        if self.send is None:
            return
        if not force and self.ps_ack_seq == seq and now - self.ps_ack_sent_at < PS_ACK_RETRY_S:
            return
        self.ps_ack_seq, self.ps_ack_sent_at = seq, now
        try:
            self.send(cmd_req_id(), bytes([OP_PS_ACK, seq]))
            self.log(f"CAN: PS_ACK sent (ps_seq={seq})")
        except OSError as e:
            self.log(f"CAN: PS_ACK send failed: {e}")

    def _ps_press(self, seq, source):
        if (seq, EV_PRESS) in self.ps_handled:
            return
        self.ps_handled.add((seq, EV_PRESS))
        if len(self.ps_handled) > 64:
            self.ps_handled = {k for k in self.ps_handled if k[0] == seq}
        self.last_ps_seq = seq
        self.log(f"CAN: PS press (ps_seq={seq}, via {source})")
        self.dispatch("PS")
        self._ack_ps(seq, force=True)

    def _fill_mix(self, value):
        if value != self.fill_mix:
            self.fill_mix = value
            self.dispatch("MIX" if value else "FILL")

    def _encoder(self, count, modifier):
        if self.enc_count is None:
            self.enc_count = count
            return
        delta = int16_delta(count, self.enc_count)
        self.enc_count = count
        if delta == 0:
            return
        if abs(delta) > ENC_MAX_STEP:
            self.log(f"CAN: encoder jump {delta} ignored (rebased)")
            return
        for tok in encoder_tokens(delta, modifier):
            self.dispatch(tok)

    def heartbeat_ok(self, now=None):
        now = self.clock() if now is None else now
        return self.last_status_at is not None and now - self.last_status_at <= HEARTBEAT_TIMEOUT_S

    # -- frame entry point --
    def handle(self, can_id, data):
        if can_id == ID_PS:
            p = decode_ps(data)
            if p["state"] == 1 and not p["long"]:
                self._ps_press(p["ps_seq"], "PS frame")
            # Release and the long-press moment carry no V1.9 action.
            return
        if can_id == ID_STATUS:
            s = decode_status(data)
            self.last_status_at = self.clock()
            self.dispatch("OK")
            if not s["inputs_valid"]:
                return
            first = not self.status_seen
            self.status_seen = True
            self._fill_mix(s["fill_mix"])
            self._encoder(s["enc_count"], s["enc_sw"])
            if s["ps_latched"]:
                if first and self.last_ps_seq is None:
                    # Latched before this host saw it (host restart). The old
                    # serial link would have lost this press; do not replay a
                    # pump stop, just record the seq so later presses compare.
                    self.last_ps_seq = s["ps_seq"]
                    self.ps_handled.add((s["ps_seq"], EV_PRESS))
                    self.log(f"CAN: PS latched at startup (ps_seq={s['ps_seq']}); not replayed")
                    self._ack_ps(s["ps_seq"], force=True)
                elif (s["ps_seq"], EV_PRESS) not in self.ps_handled:
                    # All 3 PS copies were lost; the latch still carries the press.
                    self._ps_press(s["ps_seq"], "STATUS latch")
                else:
                    self._ack_ps(s["ps_seq"])      # resend until latch clears
            elif self.ps_ack_seq is not None and s["ps_seq"] == self.ps_ack_seq:
                self.ps_ack_seq = None
            return
        if can_id == ID_EVENT:
            e = decode_event(data)
            if self.event_seq is not None and e["seq"] == self.event_seq:
                return                                  # duplicate
            self.event_seq = e["seq"]
            if e["source"] == SRC_FILL_MIX:
                self._fill_mix(1 if e["state"] == EV_PRESS else 0)
            elif e["state"] == EV_PRESS and not e["long"]:
                if e["source"] == SRC_OVR:
                    self.dispatch("OV")
                elif e["source"] == SRC_TU:
                    self.dispatch("TU")
                # SRC_PS handled via the priority PS frame; ENC_SW is the x10 modifier only.
            elif e["state"] in (EV_STUCK, EV_STUCK_CLEARED):
                self.log(f"CAN: input {e['source']} {'STUCK' if e['state'] == EV_STUCK else 'stuck cleared'}")
            elif e["source"] == SRC_CANREC:
                self.log("CAN: switch box CAN recovered")
            return
        if can_id == ID_ENC:
            e = decode_enc(data)
            self._encoder(e["count"], e["modifier"])
            return
        if can_id == ID_BOOT:
            self.boot = decode_boot(data)
            # New power cycle: counters restart; take the next values as baseline.
            self.enc_count = None
            self.fill_mix = None
            self.event_seq = None
            self.status_seen = False
            self.ps_handled.clear()
            self.last_ps_seq = None
            self.ps_ack_seq = None
            self.log(f"CAN: switch box boot {self.boot}")
            self.dispatch("BOOT")
            return


def open_can_socket(ifname="can0", timeout=1.0):
    """Raw SocketCAN socket filtered to frames from the swbox (SA 0x20)."""
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    # filter: extended frames whose low byte (SA) is 0x20
    s.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER,
                 struct.pack("=II", SWBOX_SA | CAN_EFF_FLAG, 0xFF | CAN_EFF_FLAG | CAN_RTR_FLAG))
    s.settimeout(timeout)
    s.bind((ifname,))
    return s
