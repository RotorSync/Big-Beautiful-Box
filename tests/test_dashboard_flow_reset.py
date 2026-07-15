import ast
import io
import math
from pathlib import Path

import pytest

import config


DASHBOARD_PATH = Path(__file__).resolve().parents[1] / "dashboard.py"


class FakeGPIO:
    HIGH = 1
    LOW = 0

    def __init__(self):
        self.outputs = []

    def output(self, pin, value):
        self.outputs.append((pin, value))


class FakeRoot:
    def __init__(self):
        self.callbacks = []

    def after(self, delay_ms, callback):
        self.callbacks.append((delay_ms, callback))

    def update_idletasks(self):
        pass

    def winfo_width(self):
        return 1920

    def winfo_height(self):
        return 1080

    def winfo_rootx(self):
        return 0

    def winfo_rooty(self):
        return 0


class FakeTime:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, duration):
        self.sleeps.append(duration)


class FakeCanvas:
    def __init__(self):
        self.deleted = []
        self.rectangles = []
        self.texts = []
        self.raised = []

    def delete(self, tag):
        self.deleted.append(tag)

    def create_rectangle(self, *args, **kwargs):
        self.rectangles.append((args, kwargs))

    def create_text(self, *args, **kwargs):
        self.texts.append((args, kwargs))

    def tag_raise(self, tag):
        self.raised.append(tag)


class FakeSafetyWindow:
    def __init__(self):
        self.geometry_value = ""
        self.topmost = False
        self.visible = False

    def winfo_exists(self):
        return True

    def overrideredirect(self, _enabled):
        pass

    def configure(self, **_kwargs):
        pass

    def geometry(self, value):
        self.geometry_value = value

    def deiconify(self):
        self.visible = True

    def lift(self):
        pass

    def attributes(self, name, value):
        if name == "-topmost":
            self.topmost = bool(value)

    def withdraw(self):
        self.visible = False


class FakeSafetyLabel:
    def __init__(self, _window, **_kwargs):
        self.options = {}

    def pack(self, **_kwargs):
        pass

    def configure(self, **kwargs):
        self.options.update(kwargs)


class FakeTk:
    def __init__(self):
        self.windows = []
        self.labels = []

    def Toplevel(self, _root):
        window = FakeSafetyWindow()
        self.windows.append(window)
        return window

    def Label(self, window, **kwargs):
        label = FakeSafetyLabel(window, **kwargs)
        self.labels.append(label)
        return label


def _reset_namespace(flow_gpm=0.0):
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(DASHBOARD_PATH))
    wanted = {
        "reset_is_blocked_by_flow",
        "_mark_expected_totalizer_reset",
        "_activate_physical_reset_safety",
        "clear_physical_reset_safety",
        "detect_totalizer_reset",
        "_pulse_flow_reset_gpio",
        "force_flow_reset",
        "handle_box_pump_stop_button",
        "pulse_flow_reset",
        "schedule_flow_reset",
        "physical_reset_safety_message",
        "draw_physical_reset_safety_banner",
        "_show_physical_reset_safety_window",
        "_hide_physical_reset_safety_window",
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

    gpio = FakeGPIO()
    root = FakeRoot()
    fake_time = FakeTime()
    canvas = FakeCanvas()
    fake_tk = FakeTk()
    clears = []
    pump_stops = []
    serial_log = []
    flow_log = []
    ns = {
        "config": config,
        "math": math,
        "GPIO": gpio,
        "GPIO_AVAILABLE": True,
        "SIM_MODE": False,
        "root": root,
        "tk": fake_tk,
        "canvas": canvas,
        "time": fake_time,
        "open": lambda *args, **kwargs: io.StringIO(),
        "print": lambda *args, **kwargs: None,
        "log_serial_debug": serial_log.append,
        "log_flow_control": flow_log.append,
        "start_pump_stop_thread": pump_stops.append,
        "clear_auto_shutoff_state": clears.append,
        "_canvas_width": lambda: 1920,
        "_canvas_height": lambda: 1080,
        "last_flow_rate": flow_gpm / config.LITERS_PER_SEC_TO_GPM,
        "last_totalizer_liters": 0.0,
        "previous_totalizer_liters": 0.0,
        "flow_reset_scheduled": False,
        "flow_reset_cycle_id": None,
        "flow_cycle_counter": 7,
        "physical_reset_safety_active": False,
        "physical_reset_gallons_at_event": 0.0,
        "physical_reset_flow_gpm_at_event": 0.0,
        "physical_reset_safety_window": None,
        "physical_reset_safety_label": None,
        "expected_totalizer_reset_until": 0.0,
        "expected_totalizer_reset_from_gallons": 0.0,
    }
    exec(compile(module, str(DASHBOARD_PATH), "exec"), ns)
    ns["_gpio"] = gpio
    ns["_root"] = root
    ns["_time"] = fake_time
    ns["_canvas"] = canvas
    ns["_fake_tk"] = fake_tk
    ns["_clears"] = clears
    ns["_pump_stops"] = pump_stops
    ns["_serial_log"] = serial_log
    ns["_flow_log"] = flow_log
    return ns


def _liters_for_gallons(gallons):
    return gallons / config.LITERS_TO_GALLONS


def test_meter_reader_passes_same_frame_flow_into_reset_detector():
    tree = ast.parse(DASHBOARD_PATH.read_text(encoding="utf-8"))
    reader = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "read_flow_meter"
    )
    calls = [
        node
        for node in ast.walk(reader)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "detect_totalizer_reset"
    ]

    assert len(calls) == 1
    assert len(calls[0].args) == 2
    assert isinstance(calls[0].args[1], ast.Name)
    assert calls[0].args[1].id == "flow_rate_l_per_s"


@pytest.mark.parametrize("flow_gpm", [0.02, 1.0, 80.0])
def test_forced_reset_is_blocked_during_forward_flow(flow_gpm):
    ns = _reset_namespace(flow_gpm)
    ns["flow_reset_scheduled"] = True
    ns["flow_reset_cycle_id"] = ns["flow_cycle_counter"]

    assert ns["force_flow_reset"]("test reset") is False

    assert ns["_gpio"].outputs == []
    assert ns["_clears"] == []
    assert ns["flow_reset_scheduled"] is False
    assert ns["flow_reset_cycle_id"] is None


@pytest.mark.parametrize("invalid_flow", [math.nan, math.inf, -math.inf])
def test_forced_reset_is_blocked_when_flow_reading_is_not_finite(invalid_flow):
    ns = _reset_namespace(0.0)
    ns["last_flow_rate"] = invalid_flow

    assert ns["force_flow_reset"]("test reset") is False

    assert ns["_gpio"].outputs == []
    assert ns["_clears"] == []


@pytest.mark.parametrize("flow_gpm", [0.0, -0.02, -1.0, -80.0])
def test_forced_reset_pulses_once_when_flow_is_zero_or_reverse(flow_gpm):
    ns = _reset_namespace(flow_gpm)

    assert ns["force_flow_reset"]("test reset") is True

    assert ns["_gpio"].outputs == [
        (config.FLOW_RESET_PIN, ns["_gpio"].HIGH),
        (config.FLOW_RESET_PIN, ns["_gpio"].LOW),
    ]
    assert ns["_time"].sleeps == [config.FLOW_RESET_DURATION]
    assert ns["_clears"] == ["test reset"]


@pytest.mark.parametrize("flow_gpm", [0.02, 1.0])
def test_reset_is_not_scheduled_during_forward_flow(flow_gpm):
    ns = _reset_namespace(flow_gpm)

    ns["schedule_flow_reset"]()

    assert ns["_root"].callbacks == []
    assert ns["flow_reset_scheduled"] is False


@pytest.mark.parametrize("flow_gpm", [0.02, 1.0])
def test_scheduled_reset_is_blocked_if_forward_flow_starts_before_pulse(flow_gpm):
    ns = _reset_namespace(0.0)
    ns["schedule_flow_reset"]()
    assert len(ns["_root"].callbacks) == 1

    ns["last_flow_rate"] = flow_gpm / config.LITERS_PER_SEC_TO_GPM
    _, callback = ns["_root"].callbacks.pop()
    callback()

    assert ns["_gpio"].outputs == []
    assert ns["flow_reset_scheduled"] is False
    assert ns["flow_reset_cycle_id"] is None


@pytest.mark.parametrize("flow_gpm", [0.0, -0.02, -1.0])
def test_scheduled_reset_pulses_once_if_flow_is_zero_or_reverse(flow_gpm):
    ns = _reset_namespace(flow_gpm)
    ns["schedule_flow_reset"]()

    delay_ms, callback = ns["_root"].callbacks.pop()
    callback()

    assert delay_ms == int(config.FLOW_RESET_DELAY * 1000)
    assert ns["_gpio"].outputs == [
        (config.FLOW_RESET_PIN, ns["_gpio"].HIGH),
        (config.FLOW_RESET_PIN, ns["_gpio"].LOW),
    ]


@pytest.mark.parametrize("flow_gpm", [-0.02, -1.0])
def test_scheduled_reset_still_pulses_if_reverse_flow_starts_before_pulse(flow_gpm):
    ns = _reset_namespace(0.0)
    ns["schedule_flow_reset"]()

    ns["last_flow_rate"] = flow_gpm / config.LITERS_PER_SEC_TO_GPM
    _, callback = ns["_root"].callbacks.pop()
    callback()

    assert ns["_gpio"].outputs == [
        (config.FLOW_RESET_PIN, ns["_gpio"].HIGH),
        (config.FLOW_RESET_PIN, ns["_gpio"].LOW),
    ]


def test_unexpected_physical_reset_during_forward_flow_stops_pump_and_keeps_gallons():
    ns = _reset_namespace()
    ns["previous_totalizer_liters"] = _liters_for_gallons(75.0)

    ns["detect_totalizer_reset"](
        _liters_for_gallons(0.1),
        80.0 / config.LITERS_PER_SEC_TO_GPM,
    )

    assert ns["physical_reset_safety_active"] is True
    assert ns["physical_reset_gallons_at_event"] == pytest.approx(75.0)
    assert ns["physical_reset_flow_gpm_at_event"] == pytest.approx(80.0)
    assert ns["_pump_stops"] == [config.PUMP_STOP_DURATION]
    assert ns["flow_cycle_counter"] == 8


@pytest.mark.parametrize("flow_gpm", [0.0, -0.02, -80.0])
def test_physical_reset_at_zero_or_reverse_flow_does_not_enter_safety(flow_gpm):
    ns = _reset_namespace()
    ns["previous_totalizer_liters"] = _liters_for_gallons(75.0)

    ns["detect_totalizer_reset"](
        _liters_for_gallons(0.1),
        flow_gpm / config.LITERS_PER_SEC_TO_GPM,
    )

    assert ns["physical_reset_safety_active"] is False
    assert ns["_pump_stops"] == []


def test_small_counter_change_during_forward_flow_is_not_treated_as_reset():
    ns = _reset_namespace()
    ns["previous_totalizer_liters"] = _liters_for_gallons(75.0)

    ns["detect_totalizer_reset"](
        _liters_for_gallons(74.8),
        80.0 / config.LITERS_PER_SEC_TO_GPM,
    )

    assert ns["physical_reset_safety_active"] is False
    assert ns["_pump_stops"] == []
    assert ns["flow_cycle_counter"] == 7


def test_expected_box_reset_is_not_misclassified_if_flow_starts_before_observation():
    ns = _reset_namespace(0.0)
    ns["last_totalizer_liters"] = _liters_for_gallons(75.0)
    ns["previous_totalizer_liters"] = _liters_for_gallons(75.0)
    assert ns["force_flow_reset"]("test reset") is True

    ns["detect_totalizer_reset"](
        _liters_for_gallons(0.1),
        80.0 / config.LITERS_PER_SEC_TO_GPM,
    )

    assert ns["physical_reset_safety_active"] is False
    assert ns["_pump_stops"] == []
    assert ns["expected_totalizer_reset_until"] == 0.0


def test_expired_expected_reset_window_does_not_hide_later_physical_reset():
    ns = _reset_namespace(0.0)
    ns["last_totalizer_liters"] = _liters_for_gallons(75.0)
    assert ns["force_flow_reset"]("test reset") is True
    ns["_time"].now += 3.0
    ns["previous_totalizer_liters"] = _liters_for_gallons(75.0)

    ns["detect_totalizer_reset"](
        _liters_for_gallons(0.1),
        80.0 / config.LITERS_PER_SEC_TO_GPM,
    )

    assert ns["physical_reset_safety_active"] is True
    assert ns["_pump_stops"] == [config.PUMP_STOP_DURATION]


def test_failed_box_reset_pulse_clears_expected_reset_suppression():
    ns = _reset_namespace(0.0)
    ns["last_totalizer_liters"] = _liters_for_gallons(75.0)

    def fail_output(_pin, _value):
        raise OSError("GPIO failed")

    ns["GPIO"].output = fail_output

    assert ns["force_flow_reset"]("test reset") is False
    assert ns["expected_totalizer_reset_until"] == 0.0
    assert ns["expected_totalizer_reset_from_gallons"] == 0.0


def test_resetting_an_already_zero_counter_does_not_hide_next_physical_reset():
    ns = _reset_namespace(0.0)
    assert ns["last_totalizer_liters"] == 0.0
    assert ns["force_flow_reset"]("test reset") is True
    assert ns["expected_totalizer_reset_until"] == 0.0

    ns["previous_totalizer_liters"] = _liters_for_gallons(75.0)
    ns["detect_totalizer_reset"](
        _liters_for_gallons(0.1),
        80.0 / config.LITERS_PER_SEC_TO_GPM,
    )

    assert ns["physical_reset_safety_active"] is True
    assert ns["_pump_stops"] == [config.PUMP_STOP_DURATION]


def test_repeated_unacknowledged_reset_preserves_first_gallons_and_does_not_repulse():
    ns = _reset_namespace()
    ns["previous_totalizer_liters"] = _liters_for_gallons(75.0)
    forward_flow = 80.0 / config.LITERS_PER_SEC_TO_GPM
    ns["detect_totalizer_reset"](_liters_for_gallons(0.1), forward_flow)

    ns["previous_totalizer_liters"] = _liters_for_gallons(20.0)
    ns["detect_totalizer_reset"](_liters_for_gallons(0.1), forward_flow)

    assert ns["physical_reset_gallons_at_event"] == pytest.approx(75.0)
    assert ns["_pump_stops"] == [config.PUMP_STOP_DURATION]


def test_box_pump_stop_button_immediately_clears_safety_and_reasserts_stop():
    ns = _reset_namespace()
    ns["previous_totalizer_liters"] = _liters_for_gallons(75.0)
    ns["detect_totalizer_reset"](
        _liters_for_gallons(0.1),
        80.0 / config.LITERS_PER_SEC_TO_GPM,
    )
    ns["last_flow_rate"] = 80.0 / config.LITERS_PER_SEC_TO_GPM

    assert ns["handle_box_pump_stop_button"]("serial pump stop") is True

    assert ns["physical_reset_safety_active"] is False
    assert ns["_gpio"].outputs == []
    assert ns["_pump_stops"] == [
        config.PUMP_STOP_DURATION,
        config.PUMP_STOP_DURATION,
    ]


def test_reset_button_cannot_clear_a_physical_reset_incident():
    ns = _reset_namespace()
    ns["physical_reset_safety_active"] = True
    ns["last_flow_rate"] = 80.0 / config.LITERS_PER_SEC_TO_GPM

    assert ns["force_flow_reset"]("serial reset") is False

    assert ns["physical_reset_safety_active"] is True
    assert ns["_gpio"].outputs == []


def test_serial_pump_stop_acknowledges_before_any_mode_routing():
    tree = ast.parse(DASHBOARD_PATH.read_text(encoding="utf-8"))
    listener = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "serial_listener"
    )
    acknowledgement = next(
        node
        for node in ast.walk(listener)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "line == 'PS' and physical_reset_safety_active"
    )
    first_mode_route = next(
        node
        for node in ast.walk(listener)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "exit_confirm_window"
    )
    calls = [
        node
        for node in ast.walk(acknowledgement)
        if isinstance(node, ast.Call)
    ]

    assert any(
        isinstance(call.func, ast.Name)
        and call.func.id == "handle_box_pump_stop_button"
        for call in calls
    )
    assert not any(
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "root"
        and call.func.attr == "after"
        for call in calls
    )
    assert acknowledgement.lineno < first_mode_route.lineno


def test_socket_pump_stop_cannot_acknowledge_local_physical_reset_safety():
    tree = ast.parse(DASHBOARD_PATH.read_text(encoding="utf-8"))
    listener = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "socket_command_listener"
    )
    pump_stop_route = next(
        node
        for node in ast.walk(listener)
        if isinstance(node, ast.If) and ast.unparse(node.test) == "line == 'PS'"
    )
    called_names = {
        call.func.id
        for call in ast.walk(pump_stop_route)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }

    assert "start_pump_stop_thread" in called_names
    assert "handle_box_pump_stop_button" not in called_names
    assert "clear_physical_reset_safety" not in called_names


def test_physical_reset_banner_tells_operator_gallons_and_how_to_clear():
    ns = _reset_namespace()
    ns["physical_reset_safety_active"] = True
    ns["physical_reset_gallons_at_event"] = 75.0

    ns["draw_physical_reset_safety_banner"]()

    assert len(ns["_canvas"].rectangles) == 1
    assert ns["_canvas"].rectangles[0][0][1] >= 1080 * 0.80
    text = ns["_canvas"].texts[0][1]["text"]
    assert "METER HAD 75.0 GAL WHEN RESET WAS PRESSED" in text
    assert "DO NOT RESET THE METER WHILE FLOWING" in text
    assert "PRESS PUMP STOP TO ACKNOWLEDGE" in text


def test_physical_reset_warning_window_stays_above_fullscreen_workflows():
    ns = _reset_namespace()
    ns["physical_reset_safety_active"] = True
    ns["physical_reset_gallons_at_event"] = 75.0

    ns["_show_physical_reset_safety_window"]()

    window = ns["_fake_tk"].windows[0]
    label = ns["_fake_tk"].labels[0]
    assert window.visible is True
    assert window.topmost is True
    assert window.geometry_value.startswith("1880x150+")
    assert "METER HAD 75.0 GAL" in label.options["text"]

    ns["_hide_physical_reset_safety_window"]()
    assert window.visible is False
