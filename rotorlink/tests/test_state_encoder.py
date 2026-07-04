#!/usr/bin/env python3
"""Offline tests for the compact state encoder (mirror of bumble
_encode_ble_state_payload). Run: python3 rotorlink/tests/test_state_encoder.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from rotorlink import state_encoder as se

fails = []


def eq(name, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + f"{name}" + ("" if ok else f": {got!r} != {want!r}"))
    if not ok:
        fails.append(name)


# A representative idle snapshot (matches the live trailer): all bools at default
# so they're omitted; fm_ok/sb_ok default True so omitted; curves compacted.
idle = {
    "version": "V2.20", "requested_gal": 91.388, "actual_gal": 0.0, "flow_gpm": 0.0,
    "mode": "mix", "override": False, "thumbs_visible": False, "fill_pending": False,
    "can_confirm_fill": False, "colors_green": False, "pump_stop_latched": False,
    "relay_slowdown_alarm": False, "flow_meter_connected": True, "switch_box_connected": True,
    "current_curve": "Factory", "pending_curve": "Pending +0.75 gal",
}
eq("idle compact", se.encode_ble_state(idle, client_count=1), {
    "ver": "V2.20", "req": 91.388, "act": 0.0, "flow": 0.0, "mode": "mix",
    "bc": 1, "cc": "Factory", "pc": "+0.75",
})

# Active snapshot: non-default bools appear; sensors disconnected (fm_ok/sb_ok
# non-default False appear); bc reflects client count.
active = dict(idle)
active.update({
    "actual_gal": 40.0, "flow_gpm": 12.5, "override": True, "thumbs_visible": True,
    "fill_pending": True, "can_confirm_fill": True, "colors_green": True,
    "pump_stop_latched": True, "relay_slowdown_alarm": True,
    "flow_meter_connected": False, "switch_box_connected": False,
    "current_curve": "no pending", "pending_curve": None,
})
eq("active compact", se.encode_ble_state(active, client_count=3), {
    "ver": "V2.20", "req": 91.388, "act": 40.0, "flow": 12.5, "mode": "mix", "bc": 3,
    "ov": True, "thumb": True, "pend": True, "confirm": True, "green": True,
    "latch": True, "rs": True, "fm_ok": False, "sb_ok": False,
    # cc 'no pending' -> dropped; pc None -> dropped
})

positive_drift = dict(idle)
positive_drift.update({
    "actual_gal": 4.25,
    "flow_gpm": 7.5,
    "pump_stop_latched": True,
    "positive_drift_fault": True,
    "positive_drift_gal": 4.25,
    "positive_drift_flow_gpm": 7.5,
    "flow_meter_fault_reason": "FLOW METER DRIFT +4.2 GAL - GALLON RESET REQUIRED",
})
eq("positive drift fault compact", se.encode_ble_state(positive_drift, client_count=1), {
    "ver": "V2.20", "req": 91.388, "act": 4.25, "flow": 7.5, "mode": "mix",
    "bc": 1, "latch": True, "cc": "Factory", "pc": "+0.75",
    "pdf": True, "pdg": 4.25, "pfg": 7.5,
    "ff": True, "fc": "positive_drift",
    "fmr": "FLOW METER DRIFT +4.2 GAL - GALLON RESET REQUIRED",
})

negative_flow = dict(idle)
negative_flow.update({
    "actual_gal": 99.152,
    "flow_gpm": -10.45,
    "pump_stop_latched": True,
    "negative_flow_fault": True,
    "negative_flow_gpm": -10.45,
    "flow_meter_fault_reason": "NEGATIVE FLOW METER -10.4 GPM FOR 5S - GALLON RESET REQUIRED",
})
eq("negative flow fault compact", se.encode_ble_state(negative_flow, client_count=1), {
    "ver": "V2.20", "req": 91.388, "act": 99.152, "flow": -10.45, "mode": "mix",
    "bc": 1, "latch": True, "cc": "Factory", "pc": "+0.75",
    "nff": True, "nfg": -10.45,
    "ff": True, "fc": "negative_flow",
    "fmr": "NEGATIVE FLOW METER -10.4 GPM FOR 5S - GALLON RESET REQUIRED",
})

# bc floor
eq("bc floor at 1", se.encode_ble_state(idle, client_count=0)["bc"], 1)

# live telemetry rounding
eq("live telemetry", se.encode_live_telemetry(1.23456, 2.34567, 3.456, True),
   {"req": 1.235, "act": 2.346, "flow": 3.46, "rs": True})

print("\nSTATE ENCODER:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
