"""
Command translator — maps the app's compact `{"cmd": ...}` command dicts to
dashboard command lines (the `:9999` line protocol).

This is a faithful mirror of `rotorsync_bumble.command_write_handler` so the iPad
app sends the SAME `{"cmd": ...}` commands over WiFi (RotorLink) as it already
does over BLE — write a feature once, it works on both transports. We replicate
(rather than import) bumble's logic so the safety-critical BLE server is never
touched.

`translate(cmd)` returns the dashboard line string, or None if the command is
unknown/invalid/handled elsewhere (e.g. client_hello). Keep this in sync with
bumble's handler when commands are added there.
"""

import json
import math
from typing import Optional

from src.batchmix_payload import batchmix_validation_error


def _bounded_int(value, minimum, maximum, default=0) -> int:
    try:
        value = int(value)
    except Exception:
        return default
    return max(minimum, min(maximum, value))


def _bounded_float(value, minimum, maximum) -> Optional[float]:
    try:
        value = float(value)
    except Exception:
        return None
    if not math.isfinite(value):
        return None
    return max(minimum, min(maximum, value))


def _coerce_bool(value) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off"):
            return False
    return None


def _cursor_line(action: str, **payload) -> str:
    # Mirror bumble._enqueue_cursor_command: kwargs first, then action.
    payload["action"] = action
    return "MOUSE:" + json.dumps(payload, separators=(",", ":"))


def translate(cmd: dict) -> Optional[str]:
    """Map an app `{"cmd": ...}` dict to a dashboard command line, or None."""
    if not isinstance(cmd, dict):
        return None
    command = str(cmd.get("cmd", "")).strip().lower()
    if not command:
        return None

    # client_hello is its own RotorLink message type, not a dashboard command.
    if command in ("client_hello", "hello"):
        return None

    if command == "pump_stop":
        return "PS"
    if command == "confirm_fill":
        return "TU"
    if command in ("reset_flow", "flow_reset"):
        return "RESET"

    # Remote tank-calibration wizard (mirrors bumble's command channel).
    if command == "cal_start":
        params = cmd.get("params") if isinstance(cmd.get("params"), dict) else {}
        return "CAL_START:" + json.dumps(params, separators=(",", ":"))

    if command == "cal_confirm":
        return "CAL_CONFIRM"

    if command == "cal_cancel":
        return "CAL_CANCEL"

    if command == "cal_adjust":
        try:
            return f"CAL_ADJUST:{int(cmd.get('delta'))}"
        except (TypeError, ValueError):
            return None
    if command in ("ov", "override_press", "switch_ov"):
        return "OV"
    if command in ("update_box", "run_update"):
        return "RUN_UPDATE"
    if command in ("reboot_box", "restart_box", "reboot_system"):
        return "REBOOT"
    if command in ("shutdown_box", "poweroff_box", "shutdown_system"):
        return "SHUTDOWN"
    if command in ("accept_pending_curve", "apply_pending_curve"):
        return "ACCEPT_PENDING_CURVE"

    if command in ("cursor_move", "trackpad_move"):
        dx = _bounded_int(cmd.get("dx"), -250, 250)
        dy = _bounded_int(cmd.get("dy"), -250, 250)
        return _cursor_line("move", dx=dx, dy=dy) if (dx or dy) else None
    if command in ("cursor_scroll", "trackpad_scroll"):
        steps = _bounded_int(cmd.get("steps"), -8, 8)
        return _cursor_line("scroll", steps=steps) if steps else None
    if command in ("cursor_click", "trackpad_click"):
        button = _bounded_int(cmd.get("button"), 1, 3, default=1)
        return _cursor_line("click", button=button)
    if command in ("cursor_key", "trackpad_key"):
        key = str(cmd.get("key", "")).strip().lower()
        if key in ("esc", "escape", "enter", "return", "alt_f4"):
            return _cursor_line("key", key=key)
        return None

    if command == "set_mode":
        mode = str(cmd.get("mode", "")).strip().lower()
        if mode == "mix":
            return "MIX"
        if mode == "fill":
            return "FILL"
        return None

    if command == "adjust":
        allowed = {1: "+1", -1: "-1", 10: "+10", -10: "-10"}
        try:
            delta = int(cmd.get("delta"))
        except Exception:
            delta = None
        return allowed.get(delta)

    if command in ("set_target", "set_requested_gallons", "set_gallons"):
        gallons = _bounded_float(
            cmd.get("gallons", cmd.get("target", cmd.get("value"))), 0.0, 2140.0
        )
        if gallons is None:
            return None
        return f"SET_REQUESTED_GALLONS:{gallons:.3f}"

    if command == "set_override":
        desired = _coerce_bool(cmd.get("enabled"))
        if desired is None:
            return None
        return "OV:1" if desired else "OV:0"

    if command in ("set_batchmix", "batch_mix"):
        data = cmd.get("data")
        # Validate exactly like the BLE path (rotorsync_bumble) before forwarding
        # — an invalid BatchMix must be REJECTED here, not silently trusted by
        # the dashboard. Returning None makes the server reply command_result
        # ok=false so the app surfaces the rejection.
        if not isinstance(data, dict) or batchmix_validation_error(data):
            return None
        return "BATCHMIX:" + json.dumps(data, separators=(",", ":"))

    return None
