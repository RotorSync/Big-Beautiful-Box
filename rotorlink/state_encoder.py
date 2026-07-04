"""
State encoder — compacts the raw dashboard STATE_JSON snapshot into the SAME
compact BLE/iOS payload the app already decodes over the BLE STATE characteristic
(a faithful mirror of rotorsync_bumble._encode_ble_state_payload). Emitting this
shape lets the iPad decode WiFi state with its existing `RaspberryPiLiveState`
model — no app-side model change, no extra decode path.

Two fields bumble computes from its own BLE connection state are not derivable
from the dashboard snapshot:
  * `bc` (controller/connection count) — we pass in the WiFi client count;
  * `pilot` / `prio` (pilot connected / pilot-priority active) — BLE-only, so
    they are omitted over WiFi (they default to false on the app, a safe value).
If pilot state is ever needed for WiFi clients, bumble would have to publish it
somewhere RotorLink can read.
"""

import re
import math


def _put_if_present(target, key, value):
    if value is not None:
        target[key] = value


def _put_bool_if_non_default(target, key, value, default):
    if value is None:
        return
    bool_value = bool(value)
    if bool_value != default:
        target[key] = bool_value


def _compact_curve_value(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    if "no pending" in lowered or lowered in ("none", "--"):
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if match:
        return match.group(0)
    return text[:24]


def _compact_fault_reason(value, limit=96):
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    if not text:
        return None
    return text[:limit]


def _float_if_finite(value, digits):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _flow_fault_summary_from_state(state):
    reason = _compact_fault_reason(state.get("flow_meter_fault_reason"))
    if state.get("negative_totalizer_fault"):
        return True, "negative_totalizer", reason or "Negative flow meter totalizer"
    if state.get("negative_flow_fault"):
        return True, "negative_flow", reason or "Negative flow meter"
    if state.get("positive_drift_fault"):
        return True, "positive_drift", reason or "Positive flow meter drift"
    if state.get("flow_fault_active"):
        return True, state.get("flow_fault_code") or "flow_meter", reason
    if reason:
        return True, state.get("flow_fault_code") or "flow_meter", reason
    return False, None, None


def encode_ble_state(state: dict, client_count: int = 1) -> dict:
    """Return the compact state dict (matches the BLE STATE payload shape)."""
    compact = {
        "ver": state.get("version"),
        "req": state.get("requested_gal"),
        "act": state.get("actual_gal"),
        "flow": state.get("flow_gpm"),
        "mode": state.get("mode"),
        "bc": max(1, int(client_count)),
    }
    _put_bool_if_non_default(compact, "ov", state.get("override"), False)
    _put_bool_if_non_default(compact, "thumb", state.get("thumbs_visible"), False)
    _put_bool_if_non_default(compact, "pend", state.get("fill_pending"), False)
    _put_bool_if_non_default(compact, "confirm", state.get("can_confirm_fill"), False)
    _put_bool_if_non_default(compact, "green", state.get("colors_green"), False)
    _put_bool_if_non_default(compact, "latch", state.get("pump_stop_latched"), False)
    _put_bool_if_non_default(compact, "rs", state.get("relay_slowdown_alarm"), False)
    _put_bool_if_non_default(compact, "fm_ok", state.get("flow_meter_connected"), True)
    _put_bool_if_non_default(compact, "sb_ok", state.get("switch_box_connected"), True)
    # pilot / prio: BLE-only, intentionally omitted over WiFi (default false).
    _put_if_present(compact, "cc", _compact_curve_value(state.get("current_curve")))
    _put_if_present(compact, "pc", _compact_curve_value(state.get("pending_curve")))

    negative_totalizer_fault = bool(state.get("negative_totalizer_fault"))
    negative_flow_fault = bool(state.get("negative_flow_fault"))
    positive_drift_fault = bool(state.get("positive_drift_fault"))
    _put_bool_if_non_default(compact, "ntf", negative_totalizer_fault, False)
    _put_bool_if_non_default(compact, "nff", negative_flow_fault, False)
    _put_bool_if_non_default(compact, "pdf", positive_drift_fault, False)
    if negative_totalizer_fault:
        _put_if_present(compact, "ntg", _float_if_finite(state.get("negative_totalizer_gal"), 3))
    if negative_flow_fault:
        _put_if_present(compact, "nfg", _float_if_finite(state.get("negative_flow_gpm"), 2))
    if positive_drift_fault:
        _put_if_present(compact, "pdg", _float_if_finite(state.get("positive_drift_gal"), 3))
        _put_if_present(compact, "pfg", _float_if_finite(state.get("positive_drift_flow_gpm"), 2))

    flow_fault_active, flow_fault_code, flow_fault_reason = _flow_fault_summary_from_state(state)
    _put_bool_if_non_default(compact, "ff", flow_fault_active, False)
    _put_if_present(compact, "fc", flow_fault_code if flow_fault_active else None)
    _put_if_present(compact, "fmr", flow_fault_reason if flow_fault_active else None)
    return compact


def encode_live_telemetry(requested, actual, flow, relay_slowdown_alarm=False) -> dict:
    return {
        "req": round(float(requested), 3),
        "act": round(float(actual), 3),
        "flow": round(float(flow), 2),
        "rs": bool(relay_slowdown_alarm),
    }


def encode_bms(state: dict):
    """Battery payload matching the BLE BMS characteristic ({voltage, soc}), from
    the dashboard's bms_voltage/bms_soc. Returns None when no BMS is reporting."""
    voltage = state.get("bms_voltage")
    soc = state.get("bms_soc")
    if voltage is None and soc is None:
        return None
    out = {}
    if voltage is not None:
        out["voltage"] = voltage
    if soc is not None:
        out["soc"] = soc
    return out


def encode_mopeka(state: dict, index: int):
    """Tank payload matching the BLE MOPEKA characteristic, from the dashboard's
    per-tank gallons + quality. The mm/inch fields the BLE sensor path provides
    aren't in the dashboard snapshot, so they're omitted (the app decodes them as
    nil). index 1 = front tank, 2 = back tank. Returns None when mopeka is off."""
    if not state.get("mopeka_enabled"):
        return None
    if index == 1:
        gallons = state.get("front_tank_gal")
        quality = state.get("front_tank_quality")
    else:
        gallons = state.get("back_tank_gal")
        quality = state.get("back_tank_quality")
    if gallons is None and quality is None:
        return None
    out = {}
    if gallons is not None:
        out["gallons"] = gallons
    if quality is not None:
        out["quality"] = quality
    return out
