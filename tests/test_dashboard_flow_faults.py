import ast
import threading
from pathlib import Path

import config
from src.flow_safety import (
    negative_flow_status,
    negative_totalizer_status,
    positive_drift_status,
)


DASHBOARD_PATH = Path(__file__).resolve().parents[1] / "dashboard.py"


class FakeTime:
    def __init__(self, now=1000.0):
        self.now = now

    def time(self):
        return self.now


class FakeCanvas:
    def __init__(self):
        self.calls = []

    def delete(self, tag):
        self.calls.append(("delete", tag))

    def create_text(self, *args, **kwargs):
        self.calls.append(("create_text", args, kwargs))


def _dashboard_fault_namespace():
    """Load only dashboard fault functions without starting the Tk app."""
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(DASHBOARD_PATH))
    wanted = {
        "set_pump_stop_fault_hold",
        "set_negative_totalizer_relay_hold",
        "set_positive_drift_relay_hold",
        "_clear_positive_drift_pump_hold_if_owned",
        "_reset_positive_drift_monitor",
        "update_positive_drift_fault",
        "update_negative_flow_fault",
        "update_negative_totalizer_fault",
        "update_flow_meter_fault_hold",
        "_flow_meter_fault_summary",
        "_build_dashboard_state_snapshot",
        "update_flow_rate_display",
    }
    module = ast.Module(
        body=[
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)

    fake_time = FakeTime()
    relay_events = []
    flow_events = []
    pulses = []
    canvas = FakeCanvas()

    def set_output(active, reason):
        relay_events.append((bool(active), reason))
        return True

    ns = {
        "config": config,
        "time": fake_time,
        "threading": threading,
        "negative_flow_status": negative_flow_status,
        "negative_totalizer_status": negative_totalizer_status,
        "positive_drift_status": positive_drift_status,
        "pump_stop_relay_lock": threading.Lock(),
        "pump_stop_pulse_count": 0,
        "pump_stop_fault_hold_active": False,
        "pump_stop_fault_hold_reason": "",
        "negative_totalizer_fault_active": False,
        "negative_totalizer_fault_reason": "",
        "negative_totalizer_relay_hold_active": False,
        "last_negative_totalizer_gallons": 0.0,
        "negative_flow_fault_active": False,
        "negative_flow_fault_reason": "",
        "negative_flow_started_at": 0.0,
        "last_negative_flow_gpm": 0.0,
        "positive_drift_fault_active": False,
        "positive_drift_fault_reason": "",
        "positive_drift_relay_hold_active": False,
        "positive_drift_low_flow_started_at": 0.0,
        "positive_drift_baseline_liters": 0.0,
        "positive_drift_gallons": 0.0,
        "positive_drift_flow_gpm": 0.0,
        "override_mode": False,
        "connection_error": False,
        "error_message": "",
        "last_successful_read_time": fake_time.now,
        "flow_meter_reconnect_fresh_reads": 0,
        "flow_meter_reconnect_started_at": 0.0,
        "flow_meter_reconnect_last_status_check": 0.0,
        "flow_meter_reconnect_status_ok": False,
        "flow_meter_reconnect_fault_reason": "",
        "iol_power_cycle_in_progress": False,
        "last_totalizer_liters": 0.0,
        "last_flow_rate": 0.0,
        "requested_gallons": 45.0,
        "current_mode": "fill",
        "pending_fill_gallons": 0.0,
        "pending_fill_requested": 0.0,
        "pending_fill_flow_gpm": 0.0,
        "pending_fill_shutoff_type": "",
        "pending_fill_temp_f": None,
        "thumbs_up_visible": False,
        "colors_are_green": False,
        "auto_shutoff_latched": False,
        "relay_slowdown_alarm_active": False,
        "serial_connected": True,
        "heartbeat_disconnected": False,
        "bms_soc": None,
        "bms_voltage": None,
        "daily_total": 0.0,
        "mopeka1_gallons": 0.0,
        "mopeka2_gallons": 0.0,
        "mopeka1_quality": 0,
        "mopeka2_quality": 0,
        "mopeka_enabled": False,
        "mopeka_connected": False,
        "last_loads_gallons": [],
        "VERSION": "TEST",
        "last_flow_rate_text": None,
        "last_flow_rate_mode": None,
        "last_flow_rate_color": None,
        "canvas": canvas,
        "_canvas_width": lambda: 1920,
        "_canvas_height": lambda: 1080,
        "_set_pump_stop_output": set_output,
        "log_flow_control": lambda message: flow_events.append(message),
        "start_pump_stop_thread": lambda duration: pulses.append(duration),
        "_read_iol_status_ok": lambda: (True, None),
        "flow_curve_status_text": lambda: "factory",
        "flow_curve_proposal_status_text": lambda: "none",
    }
    exec(compile(module, str(DASHBOARD_PATH), "exec"), ns)
    ns["_fake_time"] = fake_time
    ns["_relay_events"] = relay_events
    ns["_flow_events"] = flow_events
    ns["_pulses"] = pulses
    ns["_canvas"] = canvas
    return ns


def _liters_for_gallons(gallons):
    return gallons / config.LITERS_TO_GALLONS


def _lps_for_gpm(gpm):
    return gpm / config.LITERS_PER_SEC_TO_GPM


def test_relay_holds_do_not_release_each_other():
    ns = _dashboard_fault_namespace()

    ns["set_negative_totalizer_relay_hold"](True, "negative")
    ns["set_positive_drift_relay_hold"](True, "positive")
    ns["set_negative_totalizer_relay_hold"](False, "negative clear")
    ns["set_positive_drift_relay_hold"](False, "positive clear")

    assert ns["negative_totalizer_relay_hold_active"] is False
    assert ns["positive_drift_relay_hold_active"] is False
    assert ns["_relay_events"] == [
        (True, "negative totalizer hold: negative"),
        (True, "positive drift hold: positive"),
        (False, "positive drift hold cleared: positive clear"),
    ]


def test_positive_drift_timer_resets_when_flow_reaches_fifteen_gpm():
    ns = _dashboard_fault_namespace()

    ns["_fake_time"].now = 100.0
    ns["update_positive_drift_fault"](0.0, _lps_for_gpm(14.0))

    ns["_fake_time"].now = 105.0
    ns["update_positive_drift_fault"](_liters_for_gallons(2.0), _lps_for_gpm(15.0))

    ns["_fake_time"].now = 106.0
    ns["update_positive_drift_fault"](_liters_for_gallons(2.2), _lps_for_gpm(14.0))

    ns["_fake_time"].now = 115.9
    ns["update_positive_drift_fault"](_liters_for_gallons(6.0), _lps_for_gpm(14.0))

    assert ns["positive_drift_fault_active"] is False
    assert ns["positive_drift_low_flow_started_at"] == 106.0
    assert round(ns["positive_drift_baseline_liters"] * config.LITERS_TO_GALLONS, 1) == 2.2
    assert ns["_relay_events"] == []


def test_positive_drift_override_suppresses_relay_hold():
    ns = _dashboard_fault_namespace()
    ns["override_mode"] = True
    ns["_fake_time"].now = 200.0

    ns["update_positive_drift_fault"](0.0, _lps_for_gpm(14.0))
    ns["_fake_time"].now = 211.0
    ns["update_positive_drift_fault"](_liters_for_gallons(4.0), _lps_for_gpm(14.0))
    ns["update_flow_meter_fault_hold"](flow_meter_disconnected=False)

    assert ns["positive_drift_fault_active"] is True
    assert ns["positive_drift_relay_hold_active"] is False
    assert ns["pump_stop_fault_hold_active"] is False
    assert ns["_relay_events"] == []


def test_negative_fault_ignores_override_and_holds_relay():
    ns = _dashboard_fault_namespace()
    ns["override_mode"] = True

    ns["update_negative_totalizer_fault"](_liters_for_gallons(-2.0))
    ns["update_flow_meter_fault_hold"](flow_meter_disconnected=False)

    assert ns["negative_totalizer_fault_active"] is True
    assert ns["negative_totalizer_relay_hold_active"] is True
    assert ns["pump_stop_fault_hold_active"] is True
    assert ns["_relay_events"] == [
        (True, "negative totalizer hold: NEGATIVE FLOW METER -2.0 GAL - RESET REQUIRED")
    ]


def test_gallon_reset_clears_negative_flow_latch():
    ns = _dashboard_fault_namespace()

    ns["_fake_time"].now = 300.0
    ns["update_negative_flow_fault"](_liters_for_gallons(-2.0), _lps_for_gpm(-1.0))
    ns["_fake_time"].now = 306.0
    ns["update_negative_flow_fault"](_liters_for_gallons(-2.0), _lps_for_gpm(-1.0))

    assert ns["negative_flow_fault_active"] is True
    assert ns["negative_totalizer_relay_hold_active"] is True

    ns["update_negative_flow_fault"](0.0, _lps_for_gpm(-1.0))

    assert ns["negative_flow_fault_active"] is False
    assert ns["negative_totalizer_relay_hold_active"] is False
    assert ns["pump_stop_fault_hold_active"] is False
    assert ns["_relay_events"][-1] == (
        False,
        "negative totalizer hold cleared: NEGATIVE FLOW METER -1.0 GPM FOR 5S - GALLON RESET REQUIRED",
    )


def test_state_snapshot_reports_flow_fault_fields():
    ns = _dashboard_fault_namespace()
    ns["last_totalizer_liters"] = _liters_for_gallons(-2.0)
    ns["last_flow_rate"] = _lps_for_gpm(-1.25)
    ns["negative_flow_fault_active"] = True
    ns["negative_flow_fault_reason"] = "NEGATIVE FLOW METER -1.3 GPM FOR 5S - GALLON RESET REQUIRED"
    ns["last_negative_flow_gpm"] = -1.25

    snapshot = ns["_build_dashboard_state_snapshot"]()

    assert snapshot["actual_gal"] == -2.0
    assert snapshot["flow_gpm"] == -1.25
    assert snapshot["negative_flow_fault"] is True
    assert snapshot["negative_flow_gpm"] == -1.25
    assert snapshot["flow_fault_active"] is True
    assert snapshot["flow_fault_code"] == "negative_flow"
    assert snapshot["flow_meter_fault_reason"] == ns["negative_flow_fault_reason"]


def test_negative_flow_footer_draws_signed_red_text():
    ns = _dashboard_fault_namespace()
    ns["_fake_time"].now = 400.0

    ns["update_flow_rate_display"](-2.5, alert=True)

    text_calls = [call for call in ns["_canvas"].calls if call[0] == "create_text"]
    assert text_calls
    _, _args, kwargs = text_calls[-1]
    assert kwargs["text"] == "Flow:\n-2.5 GPM"
    assert kwargs["fill"] == "red"
