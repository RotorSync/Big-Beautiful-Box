#!/usr/bin/env python3
"""Offline unit tests for the {"cmd":...} -> dashboard-line translator.
Mirrors rotorsync_bumble.command_write_handler. No network, no dashboard.
Run:  python3 rotorlink/tests/test_translate.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from rotorlink import command_translator as t

fails = []


def eq(name, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + f"{name}: {got!r}" + ("" if ok else f" != {want!r}"))
    if not ok:
        fails.append(name)


# Simple verbs
eq("pump_stop", t.translate({"cmd": "pump_stop"}), "PS")
eq("confirm_fill", t.translate({"cmd": "confirm_fill"}), "TU")
eq("reset_flow", t.translate({"cmd": "reset_flow"}), "RESET")
eq("flow_reset alias", t.translate({"cmd": "flow_reset"}), "RESET")
eq("ov", t.translate({"cmd": "ov"}), "OV")
eq("override_press alias", t.translate({"cmd": "override_press"}), "OV")
eq("reboot_box", t.translate({"cmd": "reboot_box"}), "REBOOT")
eq("shutdown_box", t.translate({"cmd": "shutdown_box"}), "SHUTDOWN")
eq("accept_pending_curve", t.translate({"cmd": "accept_pending_curve"}), "ACCEPT_PENDING_CURVE")

# Mode
eq("set_mode mix", t.translate({"cmd": "set_mode", "mode": "mix"}), "MIX")
eq("set_mode fill", t.translate({"cmd": "set_mode", "mode": "fill"}), "FILL")
eq("set_mode bogus", t.translate({"cmd": "set_mode", "mode": "wat"}), None)

# Adjust
eq("adjust +1", t.translate({"cmd": "adjust", "delta": 1}), "+1")
eq("adjust -10", t.translate({"cmd": "adjust", "delta": -10}), "-10")
eq("adjust str coerce", t.translate({"cmd": "adjust", "delta": "10"}), "+10")
eq("adjust invalid", t.translate({"cmd": "adjust", "delta": 5}), None)

# Set target (+ aliases, formatting, clamping, invalid)
eq("set_target", t.translate({"cmd": "set_target", "gallons": 12.5}), "SET_REQUESTED_GALLONS:12.500")
eq("set_target target alias", t.translate({"cmd": "set_target", "target": 3}), "SET_REQUESTED_GALLONS:3.000")
eq("set_gallons alias", t.translate({"cmd": "set_gallons", "value": 7.25}), "SET_REQUESTED_GALLONS:7.250")
eq("set_target clamp hi", t.translate({"cmd": "set_target", "gallons": 99999}), "SET_REQUESTED_GALLONS:2140.000")
eq("set_target clamp lo", t.translate({"cmd": "set_target", "gallons": -5}), "SET_REQUESTED_GALLONS:0.000")
eq("set_target invalid", t.translate({"cmd": "set_target", "gallons": "abc"}), None)

# Override (bool / int / string coercion)
eq("set_override true", t.translate({"cmd": "set_override", "enabled": True}), "OV:1")
eq("set_override false", t.translate({"cmd": "set_override", "enabled": False}), "OV:0")
eq("set_override int 1", t.translate({"cmd": "set_override", "enabled": 1}), "OV:1")
eq("set_override str off", t.translate({"cmd": "set_override", "enabled": "off"}), "OV:0")
eq("set_override invalid", t.translate({"cmd": "set_override", "enabled": "maybe"}), None)

# Cursor
eq("cursor_move", t.translate({"cmd": "cursor_move", "dx": 5, "dy": -3}), 'MOUSE:{"dx":5,"dy":-3,"action":"move"}')
eq("cursor_move clamp", t.translate({"cmd": "cursor_move", "dx": 999, "dy": 0}), 'MOUSE:{"dx":250,"dy":0,"action":"move"}')
eq("cursor_move zero -> None", t.translate({"cmd": "cursor_move", "dx": 0, "dy": 0}), None)
eq("cursor_scroll", t.translate({"cmd": "cursor_scroll", "steps": 3}), 'MOUSE:{"steps":3,"action":"scroll"}')
eq("cursor_scroll zero -> None", t.translate({"cmd": "cursor_scroll", "steps": 0}), None)
eq("cursor_click", t.translate({"cmd": "cursor_click", "button": 2}), 'MOUSE:{"button":2,"action":"click"}')
eq("cursor_click default btn", t.translate({"cmd": "cursor_click"}), 'MOUSE:{"button":1,"action":"click"}')
eq("cursor_key enter", t.translate({"cmd": "cursor_key", "key": "enter"}), 'MOUSE:{"key":"enter","action":"key"}')
eq("cursor_key invalid -> None", t.translate({"cmd": "cursor_key", "key": "q"}), None)

# Batch mix
eq("set_batchmix", t.translate({"cmd": "set_batchmix", "data": {"a": 1}}), 'BATCHMIX:{"a":1}')
eq("set_batchmix no data -> None", t.translate({"cmd": "set_batchmix"}), None)

# Non-commands / junk
eq("client_hello -> None", t.translate({"cmd": "client_hello"}), None)
eq("unknown -> None", t.translate({"cmd": "frobnicate"}), None)
eq("missing cmd -> None", t.translate({"gallons": 5}), None)
eq("not a dict -> None", t.translate("nope"), None)

print("\nTRANSLATE:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
