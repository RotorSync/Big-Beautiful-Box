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

import json
import re


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
    return compact


def encode_live_telemetry(requested, actual, flow, relay_slowdown_alarm=False) -> dict:
    return {
        "req": round(float(requested), 3),
        "act": round(float(actual), 3),
        "flow": round(float(flow), 2),
        "rs": bool(relay_slowdown_alarm),
    }
