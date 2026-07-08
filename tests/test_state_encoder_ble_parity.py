"""Cross-encoder parity: the WiFi state encoder (rotorlink.state_encoder) must
mirror the BLE state payload (rotorsync_bumble._encode_ble_state_payload)
field-for-field for the same input state.

This locks the codebase's #1 landmine — a field added to one transport but not
the other silently breaks that feature over the missing link (the WiFi encoder
even documents itself as "a faithful mirror"). Until now that mirror was only
maintained by two independent tests that happened to expect the same keys; this
asserts the invariant directly.

Divergent-by-design keys are excluded: bc (per-transport client/controller
count) and pilot/prio (BLE-only, intentionally omitted over WiFi)."""
import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rotorlink import state_encoder as se
from test_maintenance_auth import install_bumble_stubs

TRANSPORT_ONLY_KEYS = {"bc", "pilot", "prio"}


@pytest.fixture
def bumble(monkeypatch):
    monkeypatch.setenv("BBB_MAINTENANCE_SECRET", "unit-test-secret")
    install_bumble_stubs(monkeypatch)
    sys.modules.pop("rotorsync_bumble", None)
    module = importlib.import_module("rotorsync_bumble")
    yield module
    sys.modules.pop("rotorsync_bumble", None)


def _strip(payload):
    return {k: v for k, v in payload.items() if k not in TRANSPORT_ONLY_KEYS}


def _idle_state():
    return {
        "version": "V2.31", "requested_gal": 91.388, "actual_gal": 0.0, "flow_gpm": 0.0,
        "mode": "mix", "override": False, "thumbs_visible": False, "fill_pending": False,
        "can_confirm_fill": False, "colors_green": False, "pump_stop_latched": False,
        "relay_slowdown_alarm": False, "flow_meter_connected": True,
        "switch_box_connected": True, "current_curve": "Factory",
        "pending_curve": "Pending +0.75 gal",
    }


def _faulted_state():
    state = _idle_state()
    state.update({
        "actual_gal": 40.0, "flow_gpm": 12.5, "override": True, "thumbs_visible": True,
        "fill_pending": True, "can_confirm_fill": True, "colors_green": True,
        "pump_stop_latched": True, "relay_slowdown_alarm": True,
        "flow_meter_connected": False, "switch_box_connected": False,
        # all three fault kinds active with their granular values
        "negative_totalizer_fault": True, "negative_totalizer_gal": -2.345,
        "negative_flow_fault": True, "negative_flow_gpm": -1.25,
        "positive_drift_fault": True, "positive_drift_gal": 3.5,
        "positive_drift_flow_gpm": 8.0,
        "flow_meter_fault_reason": "Totalizer ran backwards 2.3 gal",
    })
    return state


def _calibrating_state():
    state = _idle_state()
    state["calibration"] = {
        "mode": "offset", "phase": "review", "tank": "back", "step_index": 2,
        "points_total": 4, "target_gallons": 75.0, "settle_remaining": 42,
        "points_recorded": 3, "actual_gallons": 50.2,
        "reading": {"mm": 1204.5, "in": 47.42, "gal": 118.3, "q": 3, "ex": 46.9},
        "offset_result": None, "error": None,
    }
    return state


@pytest.mark.parametrize("name,state", [
    ("idle", _idle_state()),
    ("faulted", _faulted_state()),
    ("calibrating", _calibrating_state()),
])
def test_wifi_encoder_mirrors_ble_state(bumble, name, state):
    ble = json.loads(bumble._encode_ble_state_payload(state))
    wifi = se.encode_ble_state(state, client_count=1)
    b, w = _strip(ble), _strip(wifi)
    assert w == b, (
        f"WiFi/BLE state encoders diverged for '{name}': "
        f"only-BLE={set(b) - set(w)}, only-WiFi={set(w) - set(b)}, "
        f"value-diffs={ {k: (b.get(k), w.get(k)) for k in set(b) & set(w) if b[k] != w[k]} }"
    )
