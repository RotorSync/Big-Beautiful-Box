import asyncio
import importlib
import json
import math
import sys
import types

import pytest

from rotorlink import config_handler, state_encoder
from tests.test_maintenance_auth import install_bumble_stubs


@pytest.fixture(autouse=True)
def isolated_sensor_service_transaction_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(
        config_handler,
        "_SENSOR_SERVICE_TRANSACTION_MARKER",
        tmp_path / "trailer-selection-in-progress",
    )


@pytest.fixture
def bumble_module(monkeypatch):
    monkeypatch.setenv("BBB_MAINTENANCE_SECRET", "unit-test-secret")
    install_bumble_stubs(monkeypatch)
    sys.modules.pop("rotorsync_bumble", None)
    module = importlib.import_module("rotorsync_bumble")
    yield module
    sys.modules.pop("rotorsync_bumble", None)


def test_ble_sensor_characteristics_are_unavailable_before_first_observation(
    bumble_module,
    monkeypatch,
):
    monkeypatch.setattr(bumble_module, "mark_gatt_client_seen", lambda _connection: None)
    bumble_module.sensor_data = {
        "bms": {"soc": 0, "voltage": 0, "last_update": 0},
        "mopeka1": {"gallons": 0, "quality": 0, "last_update": 0},
        "mopeka2": {},
    }

    assert json.loads(bumble_module.make_read_handler("bms")(None)) is None
    assert json.loads(bumble_module.make_read_handler("mopeka1")(None)) is None
    assert json.loads(bumble_module.make_read_handler("mopeka2")(None)) is None


def test_ble_sensor_characteristic_preserves_real_zero_and_source_timestamp(
    bumble_module,
    monkeypatch,
):
    monkeypatch.setattr(bumble_module, "mark_gatt_client_seen", lambda _connection: None)
    bumble_module.sensor_data["mopeka1"] = {
        "gallons": 0.0,
        "quality": 3,
        "level_mm": 41.2,
        "last_update": 1720000000.25,
    }

    payload = json.loads(bumble_module.make_read_handler("mopeka1")(None))

    assert payload["gallons"] == 0.0
    assert payload["quality"] == 3
    assert payload["last_update"] == 1720000000.25


def test_partial_mopeka_emits_only_additive_observed_sensor_command(bumble_module):
    bumble_module.sensor_data = {
        "bms": {},
        "mopeka1": {
            "gallons": 0.0,
            "quality": 3,
            "level_mm": 41.2,
            "level_in": 1.62,
            "last_update": 1720000000.25,
        },
        "mopeka2": {},
    }

    commands = bumble_module._mopeka_dashboard_commands()

    assert commands == [
        "MOPEKA_SENSOR:1|0.000|3|1720000000.250000|41.200|1.6200"
    ]
    assert not any(command.startswith("MOPEKA:") for command in commands)
    assert not any(command.startswith("MOPEKA_RAW:") for command in commands)


def test_two_observed_mopeka_sensors_retain_independent_timestamps(bumble_module):
    bumble_module.sensor_data = {
        "bms": {},
        "mopeka1": {
            "gallons": 100.4,
            "quality": 3,
            "level_mm": 400.0,
            "level_in": 15.75,
            "last_update": 1720000000.25,
        },
        "mopeka2": {
            "gallons": 90.2,
            "quality": 2,
            "level_mm": 380.0,
            "level_in": 14.96,
            "last_update": 1720000015.5,
        },
    }

    commands = bumble_module._mopeka_dashboard_commands()

    assert (
        "MOPEKA:100.400|90.200|3|2|1720000000.250000|1720000015.500000"
        in commands
    )
    assert not any(command.startswith("MOPEKA_SENSOR:") for command in commands)
    assert len(commands) == 2


def test_dashboard_commands_require_source_observation_timestamp(bumble_module):
    bumble_module.sensor_data["bms"] = {"soc": 86, "voltage": 13.4}
    bumble_module.sensor_data["mopeka1"] = {"gallons": 50.0, "quality": 3}

    assert bumble_module._bms_dashboard_command() is None
    assert bumble_module._mopeka_dashboard_commands() == []


def test_bms_dashboard_command_carries_source_observation_timestamp(bumble_module):
    bumble_module.sensor_data["bms"] = {
        "soc": 86,
        "voltage": 13.4,
        "last_update": 1720000000.25,
    }

    assert (
        bumble_module._bms_dashboard_command()
        == "BMS:86|13.400|1720000000.250000"
    )


def test_dashboard_sensor_commands_carry_scanner_identity(bumble_module):
    identity = ("2", "FRONT-2", "BACK-2")
    bumble_module.sensor_data = {
        "bms": {
            "soc": 86,
            "voltage": 13.4,
            "last_update": 1720000000.25,
        },
        "mopeka1": {
            "gallons": 12.5,
            "quality": 3,
            "level_mm": 41.2,
            "level_in": 1.62,
            "last_update": 1720000000.25,
        },
        "mopeka2": {},
    }

    assert bumble_module._mopeka_dashboard_commands(identity=identity) == [
        'MOPEKA_SENSOR:1|12.500|3|1720000000.250000|41.200|1.6200|'
        '["2","FRONT-2","BACK-2"]'
    ]
    assert bumble_module._bms_dashboard_command(identity=identity) == (
        'BMS:86|13.400|1720000000.250000|["2","FRONT-2","BACK-2"]'
    )


def test_ble_select_trailer_resets_caches_before_confirming_new_identity(
    bumble_module,
    monkeypatch,
):
    persisted = {
        "box_mode": "fleet",
        "assigned_trailer": 1,
        "trailer": 1,
        "front_id": "OLD-FRONT",
        "back_id": "OLD-BACK",
    }
    sensor_rows = [
        {
            "Man": "Test",
            "Trailer": "2",
            "Tank": "Front",
            "Mopeka ID": "NEW-FRONT",
            "Height Offset": "0",
        }
    ]
    commands = []
    restarts = []
    responses = []

    monkeypatch.setattr(bumble_module, "_box_mode_uses_trailer_list", lambda *_: True)
    monkeypatch.setattr(bumble_module, "load_sensor_csv", lambda: sensor_rows)
    monkeypatch.setattr(bumble_module, "load_config", lambda: dict(persisted))

    def save_config(value):
        persisted.clear()
        persisted.update(value)

    monkeypatch.setattr(bumble_module, "save_config", save_config)
    monkeypatch.setattr(bumble_module, "mopeka_reload", lambda: None)
    monkeypatch.setattr(
        bumble_module,
        "send_dashboard_command",
        lambda command: commands.append(command) or "OK",
    )
    monkeypatch.setattr(
        bumble_module,
        "_set_config_response_obj",
        lambda response: responses.append(response),
    )
    monkeypatch.setattr(
        bumble_module,
        "_schedule_identity_restart",
        lambda reason: restarts.append(reason),
    )
    bumble_module.sensor_data = {
        "bms": {"soc": 80, "voltage": 13.2, "last_update": 100.0},
        "mopeka1": {"gallons": 10.0, "quality": 3, "last_update": 100.0},
        "mopeka2": {"gallons": 20.0, "quality": 3, "last_update": 100.0},
    }
    bumble_module.MOPEKA1_MAC_SUFFIX = "OLD-FRONT"
    bumble_module.MOPEKA2_MAC_SUFFIX = "OLD-BACK"

    bumble_module._cmd_select_trailer({"trailer": 2}, request_id="select-2")

    assert commands == [
        "RESET_TRAILER_SENSOR_TELEMETRY",
        "TRAILER_SENSOR_IDENTITY_CHANGED",
    ]
    assert bumble_module.sensor_data == {
        "bms": {},
        "mopeka1": {},
        "mopeka2": {},
    }
    assert bumble_module.MOPEKA1_MAC_SUFFIX == "NEW-FRONT"
    assert bumble_module.MOPEKA2_MAC_SUFFIX == ""
    assert persisted["assigned_trailer"] == 2
    assert responses[-1]["ok"] is True
    assert len(restarts) == 1


def test_same_confirmed_identity_preserves_last_known_sensor_cache(
    bumble_module,
    monkeypatch,
):
    persisted = {
        "box_mode": "fleet",
        "assigned_trailer": 2,
        "trailer": 2,
        "front_id": "FRONT-2",
        "back_id": "BACK-2",
    }
    rows = [
        {"Man": "Test", "Trailer": "2", "Tank": "Front", "Mopeka ID": "FRONT-2"},
        {"Man": "", "Trailer": "2", "Tank": "Back", "Mopeka ID": "BACK-2"},
    ]
    original_cache = {
        "bms": {"soc": 80, "voltage": 13.2, "last_update": 100.0},
        "mopeka1": {"gallons": 10.0, "quality": 3, "last_update": 100.0},
        "mopeka2": {"gallons": 20.0, "quality": 3, "last_update": 100.0},
    }
    bumble_module.sensor_data = original_cache
    monkeypatch.setattr(bumble_module, "load_sensor_csv", lambda: rows)
    monkeypatch.setattr(bumble_module, "load_config", lambda: dict(persisted))
    monkeypatch.setattr(bumble_module, "save_config", lambda value: None)
    monkeypatch.setattr(bumble_module, "mopeka_reload", lambda: None)
    monkeypatch.setattr(
        bumble_module,
        "send_dashboard_command",
        lambda _command: pytest.fail("same identity must not clear telemetry"),
    )

    bumble_module.apply_trailer(2, reset_sensor_cache=True)

    assert bumble_module.sensor_data is original_cache


def test_identity_reset_is_ordered_after_queued_sensor_delivery(
    bumble_module,
    monkeypatch,
):
    events = []
    bumble_module.sensor_data["mopeka1"] = {
        "gallons": 12.0,
        "quality": 3,
        "last_update": 100.0,
    }
    monkeypatch.setattr(
        bumble_module,
        "send_dashboard_command",
        lambda command: events.append(command) or "OK",
    )

    bumble_module.submit_dashboard_io(events.append, "OLD_SENSOR_WRITE")
    bumble_module._reset_trailer_sensor_telemetry_for_identity_change()

    assert events == ["OLD_SENSOR_WRITE", "RESET_TRAILER_SENSOR_TELEMETRY"]
    assert bumble_module.sensor_data["mopeka1"] == {}


def test_wifi_trailer_selections_are_serialized_across_clients(monkeypatch):
    handler = config_handler.ConfigHandler(None)
    active = 0
    max_active = 0
    events = []

    async def select(cmd, request_id):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        events.append(("start", cmd["trailer"]))
        await asyncio.sleep(0.01)
        events.append(("end", cmd["trailer"]))
        active -= 1
        return {
            "ok": True,
            "op": "SELECT_TRAILER",
            "request_id": request_id,
        }

    monkeypatch.setattr(handler, "_select_trailer", select)

    async def select_concurrently():
        return await asyncio.gather(
            handler.handle({
                "op": "SELECT_TRAILER",
                "trailer": 1,
                "request_id": "one",
            }),
            handler.handle({
                "op": "SELECT_TRAILER",
                "trailer": 2,
                "request_id": "two",
            }),
        )

    responses = asyncio.run(select_concurrently())

    assert max_active == 1
    assert events == [
        ("start", 1),
        ("end", 1),
        ("start", 2),
        ("end", 2),
    ]
    assert [response["request_id"] for response in responses] == ["one", "two"]


def test_wifi_select_stops_old_scanner_resets_then_starts_new_identity(monkeypatch):
    previous = {
        "box_mode": "fleet",
        "assigned_trailer": 1,
        "trailer": 1,
        "front_id": "FRONT-1",
        "back_id": "BACK-1",
    }
    calls = []
    offloaded = []

    class Dashboard:
        async def send_command(self, command):
            calls.append(("dashboard", command))
            return "OK"

    monkeypatch.setattr(config_handler, "_box_mode_uses_trailer_list", lambda: True)
    monkeypatch.setattr(
        config_handler,
        "_candidate_trailer_sensor_identity",
        lambda _trailer: ("2", "FRONT-2", "BACK-2"),
    )
    monkeypatch.setattr(config_handler, "_load_config", lambda: dict(previous))
    monkeypatch.setattr(
        config_handler,
        "_apply_trailer",
        lambda trailer: calls.append(("apply", trailer)) or {"trailer": trailer},
    )
    monkeypatch.setattr(
        config_handler,
        "_set_sensor_service_state",
        lambda action, service: calls.append(("service", action, service)),
    )

    async def run_off_loop(fn, *args):
        offloaded.append(fn.__name__)
        return fn(*args)

    monkeypatch.setattr(config_handler.asyncio, "to_thread", run_off_loop)

    response = asyncio.run(
        config_handler.ConfigHandler(Dashboard()).handle(
            {"op": "SELECT_TRAILER", "trailer": 2, "request_id": "wifi-2"}
        )
    )

    assert response["ok"] is True
    assert offloaded == [
        "_mark_sensor_service_transaction",
        "_stop_sensor_services",
        "_start_sensor_services",
        "_clear_sensor_service_transaction",
    ]
    assert calls == [
        ("service", "stop", "rotorsync_watchdog.service"),
        ("service", "stop", "rotorsync.service"),
        ("dashboard", "RESET_TRAILER_SENSOR_TELEMETRY"),
        ("apply", 2),
        ("dashboard", "TRAILER_SENSOR_IDENTITY_CHANGED"),
        ("service", "start", "rotorsync.service"),
        ("service", "start", "rotorsync_watchdog.service"),
    ]
    assert config_handler.sensor_service_transaction_pending() is False


def test_wifi_select_reset_failure_restarts_old_scanner_without_applying(monkeypatch):
    previous = {
        "box_mode": "fleet",
        "assigned_trailer": 1,
        "trailer": 1,
        "front_id": "FRONT-1",
        "back_id": "BACK-1",
    }
    calls = []

    class Dashboard:
        async def send_command(self, command):
            calls.append(("dashboard", command))
            return None

    monkeypatch.setattr(config_handler, "_box_mode_uses_trailer_list", lambda: True)
    monkeypatch.setattr(
        config_handler,
        "_candidate_trailer_sensor_identity",
        lambda _trailer: ("2", "FRONT-2", "BACK-2"),
    )
    monkeypatch.setattr(config_handler, "_load_config", lambda: dict(previous))
    monkeypatch.setattr(
        config_handler,
        "_apply_trailer",
        lambda trailer: pytest.fail(f"must not apply trailer {trailer}"),
    )
    monkeypatch.setattr(
        config_handler,
        "_set_sensor_service_state",
        lambda action, service: calls.append(("service", action, service)),
    )

    response = asyncio.run(
        config_handler.ConfigHandler(Dashboard()).handle(
            {"op": "SELECT_TRAILER", "trailer": 2, "request_id": "wifi-2"}
        )
    )

    assert response["ok"] is False
    assert calls == [
        ("service", "stop", "rotorsync_watchdog.service"),
        ("service", "stop", "rotorsync.service"),
        ("dashboard", "RESET_TRAILER_SENSOR_TELEMETRY"),
        ("service", "stop", "rotorsync_watchdog.service"),
        ("service", "stop", "rotorsync.service"),
        ("service", "start", "rotorsync.service"),
        ("service", "start", "rotorsync_watchdog.service"),
    ]


def test_wifi_select_same_identity_does_not_restart_sensor_services(monkeypatch):
    previous = {
        "box_mode": "fleet",
        "assigned_trailer": 2,
        "trailer": 2,
        "front_id": "FRONT-2",
        "back_id": "BACK-2",
    }
    calls = []

    monkeypatch.setattr(config_handler, "_box_mode_uses_trailer_list", lambda: True)
    monkeypatch.setattr(
        config_handler,
        "_candidate_trailer_sensor_identity",
        lambda _trailer: ("2", "FRONT-2", "BACK-2"),
    )
    monkeypatch.setattr(config_handler, "_load_config", lambda: dict(previous))
    monkeypatch.setattr(
        config_handler,
        "_apply_trailer",
        lambda trailer: calls.append(("apply", trailer)) or {"trailer": trailer},
    )
    monkeypatch.setattr(
        config_handler,
        "_set_sensor_service_state",
        lambda action, service: pytest.fail(
            f"same identity must not {action} {service}"
        ),
    )

    response = asyncio.run(
        config_handler.ConfigHandler(None).handle(
            {"op": "SELECT_TRAILER", "trailer": 2, "request_id": "wifi-2"}
        )
    )

    assert response["ok"] is True
    assert calls == [("apply", 2)]


def test_wifi_select_partial_stop_failure_recovers_both_services(monkeypatch):
    previous = {
        "box_mode": "fleet",
        "assigned_trailer": 1,
        "trailer": 1,
        "front_id": "FRONT-1",
        "back_id": "BACK-1",
    }
    calls = []
    failed_once = False

    class Dashboard:
        async def send_command(self, command):
            calls.append(("dashboard", command))
            return "OK"

    def set_service_state(action, service):
        nonlocal failed_once
        calls.append(("service", action, service))
        if action == "stop" and service == "rotorsync.service" and not failed_once:
            failed_once = True
            raise RuntimeError("scanner stop failed")

    monkeypatch.setattr(config_handler, "_box_mode_uses_trailer_list", lambda: True)
    monkeypatch.setattr(
        config_handler,
        "_candidate_trailer_sensor_identity",
        lambda _trailer: ("2", "FRONT-2", "BACK-2"),
    )
    monkeypatch.setattr(config_handler, "_load_config", lambda: dict(previous))
    monkeypatch.setattr(
        config_handler,
        "_apply_trailer",
        lambda trailer: pytest.fail(f"must not apply trailer {trailer}"),
    )
    monkeypatch.setattr(
        config_handler,
        "_set_sensor_service_state",
        set_service_state,
    )

    response = asyncio.run(
        config_handler.ConfigHandler(Dashboard()).handle(
            {"op": "SELECT_TRAILER", "trailer": 2, "request_id": "wifi-2"}
        )
    )

    assert response["ok"] is False
    assert "scanner stop failed" in response["error"]
    assert calls == [
        ("service", "stop", "rotorsync_watchdog.service"),
        ("service", "stop", "rotorsync.service"),
        ("service", "stop", "rotorsync_watchdog.service"),
        ("service", "stop", "rotorsync.service"),
        ("service", "start", "rotorsync.service"),
        ("service", "start", "rotorsync_watchdog.service"),
    ]


def test_wifi_select_partial_start_failure_rolls_back_and_cleanly_recovers(
    monkeypatch,
):
    previous = {
        "box_mode": "fleet",
        "assigned_trailer": 1,
        "trailer": 1,
        "front_id": "FRONT-1",
        "back_id": "BACK-1",
    }
    calls = []
    watchdog_start_attempts = 0

    class Dashboard:
        async def send_command(self, command):
            calls.append(("dashboard", command))
            return "OK"

    def set_service_state(action, service):
        nonlocal watchdog_start_attempts
        calls.append(("service", action, service))
        if action == "start" and service == "rotorsync_watchdog.service":
            watchdog_start_attempts += 1
            if watchdog_start_attempts == 1:
                raise RuntimeError("watchdog start failed")

    monkeypatch.setattr(config_handler, "_box_mode_uses_trailer_list", lambda: True)
    monkeypatch.setattr(
        config_handler,
        "_candidate_trailer_sensor_identity",
        lambda _trailer: ("2", "FRONT-2", "BACK-2"),
    )
    monkeypatch.setattr(config_handler, "_load_config", lambda: dict(previous))
    monkeypatch.setattr(
        config_handler,
        "_apply_trailer",
        lambda trailer: calls.append(("apply", trailer)) or {"trailer": trailer},
    )
    monkeypatch.setattr(
        config_handler,
        "_save_config",
        lambda cfg: calls.append(("rollback", dict(cfg))),
    )
    monkeypatch.setattr(
        config_handler,
        "_set_sensor_service_state",
        set_service_state,
    )

    response = asyncio.run(
        config_handler.ConfigHandler(Dashboard()).handle(
            {"op": "SELECT_TRAILER", "trailer": 2, "request_id": "wifi-2"}
        )
    )

    assert response["ok"] is False
    assert "watchdog start failed" in response["error"]
    assert calls == [
        ("service", "stop", "rotorsync_watchdog.service"),
        ("service", "stop", "rotorsync.service"),
        ("dashboard", "RESET_TRAILER_SENSOR_TELEMETRY"),
        ("apply", 2),
        ("dashboard", "TRAILER_SENSOR_IDENTITY_CHANGED"),
        ("service", "start", "rotorsync.service"),
        ("service", "start", "rotorsync_watchdog.service"),
        ("rollback", previous),
        ("service", "stop", "rotorsync_watchdog.service"),
        ("service", "stop", "rotorsync.service"),
        ("service", "start", "rotorsync.service"),
        ("service", "start", "rotorsync_watchdog.service"),
        ("dashboard", "RESET_TRAILER_SENSOR_TELEMETRY"),
        ("dashboard", "TRAILER_SENSOR_IDENTITY_CHANGED"),
    ]


def test_interrupted_selection_marker_recovers_pair_and_dashboard(monkeypatch):
    calls = []

    class Dashboard:
        async def send_command(self, command):
            calls.append(("dashboard", command))
            return "OK"

    monkeypatch.setattr(
        config_handler,
        "_set_sensor_service_state",
        lambda action, service: calls.append(("service", action, service)),
    )
    config_handler._mark_sensor_service_transaction()

    recovered = asyncio.run(
        config_handler.ConfigHandler(Dashboard()).recover_interrupted_trailer_selection()
    )

    assert recovered is True
    assert calls == [
        ("service", "stop", "rotorsync_watchdog.service"),
        ("service", "stop", "rotorsync.service"),
        ("service", "start", "rotorsync.service"),
        ("service", "start", "rotorsync_watchdog.service"),
        ("dashboard", "RESET_TRAILER_SENSOR_TELEMETRY"),
        ("dashboard", "TRAILER_SENSOR_IDENTITY_CHANGED"),
    ]
    assert config_handler.sensor_service_transaction_pending() is False


def test_interrupted_selection_keeps_marker_if_dashboard_reconcile_fails(
    monkeypatch,
):
    class Dashboard:
        async def send_command(self, _command):
            return None

    monkeypatch.setattr(
        config_handler,
        "_set_sensor_service_state",
        lambda _action, _service: None,
    )
    config_handler._mark_sensor_service_transaction()

    with pytest.raises(RuntimeError, match="interrupted trailer reset"):
        asyncio.run(
            config_handler.ConfigHandler(
                Dashboard()
            ).recover_interrupted_trailer_selection()
        )

    assert config_handler.sensor_service_transaction_pending() is True


def test_wifi_mopeka_history_reader_filters_to_confirmed_identity(monkeypatch):
    rows = [
        {
            "timestamp": "100",
            "reason": "start",
            "front_gal": "10",
            "back_gal": "20",
            "trailer_id": "1",
            "front_sensor_id": "FRONT-1",
            "back_sensor_id": "BACK-1",
        },
        {
            "timestamp": "200",
            "reason": "start",
            "front_gal": "30",
            "back_gal": "40",
            "trailer_id": "2",
            "front_sensor_id": "FRONT-2",
            "back_sensor_id": "BACK-2",
        },
        {
            "timestamp": "50",
            "reason": "periodic",
            "front_gal": "99",
            "back_gal": "99",
        },
    ]
    monkeypatch.setattr(config_handler, "_mopeka_history_paths", lambda: ["history"])
    monkeypatch.setattr(
        config_handler,
        "_read_mopeka_history_rows",
        lambda _path: list(rows),
    )
    monkeypatch.setattr(config_handler, "_clamped_history_window", lambda _cmd: (0, 1000))
    monkeypatch.setattr(
        config_handler,
        "_history_timestamp_epoch",
        lambda value: float(value),
    )

    for trailer, front, back, expected_gallons in (
        (1, "FRONT-1", "BACK-1", 10.0),
        (2, "FRONT-2", "BACK-2", 30.0),
    ):
        monkeypatch.setattr(
            config_handler,
            "_load_config",
            lambda trailer=trailer, front=front, back=back: {
                "assigned_trailer": trailer,
                "front_id": front,
                "back_id": back,
            },
        )
        items = config_handler._load_mopeka_history_items({})
        assert [item["fg"] for item in items] == [expected_gallons]


def test_rotorlink_identity_change_clears_only_in_memory_sensor_payloads(monkeypatch):
    # The focused BBB venv intentionally carries only pytest; server import
    # needs the WebSocket symbols at runtime but this cache test does not.
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        types.SimpleNamespace(ConnectionClosed=Exception, serve=None),
    )
    sys.modules.pop("rotorlink.server", None)
    server_module = importlib.import_module("rotorlink.server")
    server = server_module.RotorLinkServer()
    trailer_one = {
        "box_mode": "fleet",
        "trailer": 1,
        "front": {"id": "FRONT-1"},
        "back": {"id": "BACK-1"},
    }
    trailer_two = {
        "box_mode": "fleet",
        "trailer": 2,
        "front": {"id": "FRONT-2"},
        "back": {"id": "BACK-2"},
    }
    trailer_one_identity = '["1","FRONT-1","BACK-1"]'
    trailer_two_identity = '["2","FRONT-2","BACK-2"]'
    server._synchronize_sensor_cache_identity(trailer_one_identity)
    server._last_bms = {"soc": 86, "last_update": 100.0}
    server._last_mopeka = {
        1: {"gallons": 10.0, "last_update": 100.0},
        2: {"gallons": 20.0, "last_update": 100.0},
    }

    assert server._synchronize_sensor_cache_identity(trailer_one_identity) is False
    assert server._last_bms is not None

    assert server._synchronize_sensor_cache_identity(trailer_two_identity) is True
    assert server._last_bms is None
    assert server._last_mopeka == {1: None, 2: None}
    sys.modules.pop("rotorlink.server", None)


def test_rotorlink_unassigned_identity_revokes_and_clears_sensor_cache(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        types.SimpleNamespace(ConnectionClosed=Exception, serve=None),
    )
    sys.modules.pop("rotorlink.server", None)
    server_module = importlib.import_module("rotorlink.server")
    server = server_module.RotorLinkServer()
    server._synchronize_sensor_cache_identity(
        '["1","FRONT-1","BACK-1"]',
        confirmed=True,
    )
    server._last_bms = {"soc": 86, "last_update": 100.0}
    server._last_mopeka = {
        1: {"gallons": 10.0, "last_update": 100.0},
        2: {"gallons": 20.0, "last_update": 100.0},
    }

    assert server._synchronize_sensor_cache_identity(None) is True
    assert server._sensor_cache_identity is None
    assert server._sensor_cache_identity_confirmed is False
    assert server._last_bms is None
    assert server._last_mopeka == {1: None, 2: None}
    sys.modules.pop("rotorlink.server", None)


def test_rotorlink_new_client_after_unassign_never_receives_prior_sensor_cache(
    monkeypatch,
):
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        types.SimpleNamespace(ConnectionClosed=Exception, serve=None),
    )
    sys.modules.pop("rotorlink.server", None)
    server_module = importlib.import_module("rotorlink.server")
    server = server_module.RotorLinkServer()
    server._sensor_cache_identity = '["1","FRONT-1","BACK-1"]'
    server._sensor_cache_identity_confirmed = True
    server._last_bms = {"soc": 86, "last_update": 100.0}
    server._last_mopeka = {
        1: {"gallons": 10.0, "last_update": 100.0},
        2: {"gallons": 20.0, "last_update": 100.0},
    }
    server._read_trailer_snapshot = lambda: (
        {"box_mode": "fleet", "trailer": None, "enabled": True},
        None,
    )
    server._write_wifi_snapshot = lambda: None

    async def no_pilot_status():
        return None

    server._push_pilot_status = no_pilot_status
    monkeypatch.setattr(server_module.connection_registry, "record_event", lambda *a, **k: None)
    messages = []

    async def record_send(_state, message):
        messages.append(message)

    server._send = record_send

    class EmptyWebSocket:
        remote_address = ("127.0.0.1", 12345)

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    asyncio.run(server._handle(EmptyWebSocket()))

    assert [message["type"] for message in messages] == ["hello", "trailer_config"]
    assert server._sensor_cache_identity is None
    assert server._sensor_cache_identity_confirmed is False
    assert server._last_bms is None
    assert server._last_mopeka == {1: None, 2: None}
    sys.modules.pop("rotorlink.server", None)


def test_trailer_snapshot_rejects_missing_or_partial_config(monkeypatch, tmp_path):
    config_path = tmp_path / "mopeka_config.json"
    monkeypatch.setattr(config_handler.config, "MOPEKA_CONFIG_PATH", str(config_path))

    with pytest.raises(FileNotFoundError):
        config_handler._current_trailer_snapshot()

    config_path.write_text('{"assigned_trailer":', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        config_handler._current_trailer_snapshot()


def test_customer_snapshot_keeps_manual_ids_for_sensor_cache_ownership(monkeypatch):
    cfg = {
        "box_mode": "customer",
        "assigned_trailer": None,
        "trailer": None,
        "front_id": "FRONT-CUSTOMER",
        "back_id": "BACK-CUSTOMER",
    }
    monkeypatch.setattr(config_handler, "_load_config_strict", lambda: dict(cfg))

    trailer, identity = config_handler._current_trailer_snapshot()

    assert trailer == {"box_mode": "customer", "trailer": None, "enabled": False}
    assert identity == '["","FRONT-CUSTOMER","BACK-CUSTOMER"]'


def test_rotorlink_config_read_failure_preserves_cache_but_sends_no_new_owner(
    monkeypatch,
):
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        types.SimpleNamespace(ConnectionClosed=Exception, serve=None),
    )
    sys.modules.pop("rotorlink.server", None)
    server_module = importlib.import_module("rotorlink.server")
    server = server_module.RotorLinkServer()
    identity = '["1","FRONT-1","BACK-1"]'
    server._synchronize_sensor_cache_identity(identity, confirmed=True)
    server._last_bms = {"soc": 86, "last_update": 100.0}
    server._last_mopeka[1] = {"gallons": 10.0, "last_update": 100.0}
    server._read_trailer_snapshot = lambda: None

    asyncio.run(server._broadcast_trailer_config())

    assert server._sensor_cache_identity == identity
    assert server._sensor_cache_identity_confirmed is True
    assert server._last_bms == {"soc": 86, "last_update": 100.0}
    assert server._last_mopeka[1] == {"gallons": 10.0, "last_update": 100.0}
    sys.modules.pop("rotorlink.server", None)


def test_rotorlink_retries_failed_initial_identity_confirmation(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        types.SimpleNamespace(ConnectionClosed=Exception, serve=None),
    )
    sys.modules.pop("rotorlink.server", None)
    server_module = importlib.import_module("rotorlink.server")
    server = server_module.RotorLinkServer()
    identity = '["1","FRONT-1","BACK-1"]'
    trailer = {
        "box_mode": "fleet",
        "trailer": 1,
        "front": {"id": "FRONT-1"},
        "back": {"id": "BACK-1"},
    }
    snapshots = iter((None, (trailer, identity), (trailer, identity)))
    server._read_trailer_snapshot = lambda: next(snapshots)
    messages = []

    async def record(message):
        messages.append(message)

    server._broadcast = record
    state = {
        "version": "V2.46",
        "requested_gal": 0.0,
        "actual_gal": 0.0,
        "flow_gpm": 0.0,
        "mode": "fill",
        "trailer_sensor_identity_generation": 1,
        "trailer_sensor_identity": identity,
        "mopeka_enabled": True,
        "front_tank_has_reading": True,
        "front_tank_gal": 10.0,
        "front_tank_quality": 3,
        "front_tank_last_update": 100.0,
    }

    asyncio.run(server._publish_dashboard_state(state))
    assert server._sensor_cache_identity_confirmed is False
    assert not any(message["type"] == "mopeka" for message in messages)

    asyncio.run(server._publish_dashboard_state(state))
    assert server._sensor_cache_identity == identity
    assert server._sensor_cache_identity_confirmed is True
    assert sum(message["type"] == "mopeka" for message in messages) == 1
    sys.modules.pop("rotorlink.server", None)


def test_unassigned_sensor_state_does_not_reread_config_each_tick(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        types.SimpleNamespace(ConnectionClosed=Exception, serve=None),
    )
    sys.modules.pop("rotorlink.server", None)
    server_module = importlib.import_module("rotorlink.server")
    server = server_module.RotorLinkServer()
    reads = 0

    def read_unassigned():
        nonlocal reads
        reads += 1
        return ({"box_mode": "fleet", "trailer": None, "enabled": True}, None)

    server._read_trailer_snapshot = read_unassigned

    async def publish_twice():
        await server._broadcast_sensors({})
        await server._broadcast_sensors({})

    asyncio.run(publish_twice())

    assert reads == 1
    assert server._sensor_cache_identity_resolved is True
    assert server._sensor_cache_identity_confirmed is False
    sys.modules.pop("rotorlink.server", None)


def test_rotorlink_customer_manual_identity_publishes_matching_sensor_data(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        types.SimpleNamespace(ConnectionClosed=Exception, serve=None),
    )
    sys.modules.pop("rotorlink.server", None)
    server_module = importlib.import_module("rotorlink.server")
    server = server_module.RotorLinkServer()
    customer_identity = '["","FRONT-CUSTOMER","BACK-CUSTOMER"]'
    server._read_trailer_snapshot = lambda: (
        {"box_mode": "customer", "trailer": None, "enabled": False},
        customer_identity,
    )
    messages = []

    async def record(message):
        messages.append(message)

    server._broadcast = record
    state = {
        "version": "V2.46",
        "requested_gal": 0.0,
        "actual_gal": 0.0,
        "flow_gpm": 0.0,
        "mode": "fill",
        "trailer_sensor_identity_generation": 1,
        "trailer_sensor_identity": customer_identity,
        "bms_has_reading": True,
        "bms_soc": 86.0,
        "bms_voltage": 13.4,
        "bms_last_update": 100.0,
        "mopeka_enabled": True,
        "front_tank_has_reading": True,
        "front_tank_gal": 10.0,
        "front_tank_quality": 3,
        "front_tank_last_update": 101.0,
    }

    asyncio.run(server._publish_dashboard_state(state))

    assert server._sensor_cache_identity == customer_identity
    assert server._sensor_cache_identity_confirmed is True
    assert [message["type"] for message in messages] == [
        "state",
        "trailer_config",
        "bms",
        "mopeka",
    ]
    sys.modules.pop("rotorlink.server", None)


def test_rotorlink_timestamp_only_change_skips_redundant_compact_state(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        types.SimpleNamespace(ConnectionClosed=Exception, serve=None),
    )
    sys.modules.pop("rotorlink.server", None)
    server_module = importlib.import_module("rotorlink.server")
    server = server_module.RotorLinkServer()
    sensor_identity = '["1","FRONT-1","BACK-1"]'
    server._read_trailer_snapshot = lambda: (
        {
            "box_mode": "fleet",
            "trailer": 1,
            "front": {"id": "FRONT-1"},
            "back": {"id": "BACK-1"},
        },
        sensor_identity,
    )
    messages = []

    async def record(message):
        messages.append(message)

    server._broadcast = record
    first = {
        "version": "V2.46",
        "requested_gal": 10.0,
        "actual_gal": 5.0,
        "flow_gpm": 2.0,
        "mode": "fill",
        "trailer_sensor_identity_generation": 1,
        "trailer_sensor_identity": sensor_identity,
        "mopeka_enabled": True,
        "front_tank_has_reading": True,
        "front_tank_gal": 42.0,
        "front_tank_quality": 3,
        "front_tank_last_update": 100.0,
    }
    second = dict(first, front_tank_last_update=115.0)

    async def publish_both():
        await server._publish_dashboard_state(first)
        await server._publish_dashboard_state(second)

    asyncio.run(publish_both())

    sensor_messages = [
        message for message in messages if message["type"] == "mopeka"
    ]
    assert sum(message["type"] == "state" for message in messages) == 1
    assert len(sensor_messages) == 2
    assert sensor_messages[0]["mopeka"]["last_update"] == 100.0
    assert sensor_messages[1]["mopeka"]["last_update"] == 115.0
    sys.modules.pop("rotorlink.server", None)


def test_rotorlink_accepts_old_dashboard_startup_identity_once(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        types.SimpleNamespace(ConnectionClosed=Exception, serve=None),
    )
    sys.modules.pop("rotorlink.server", None)
    server_module = importlib.import_module("rotorlink.server")
    server = server_module.RotorLinkServer()
    trailer = {
        "box_mode": "fleet",
        "trailer": 1,
        "front": {"id": "FRONT-1"},
        "back": {"id": "BACK-1"},
    }
    messages = []

    async def record(message):
        messages.append(message)

    server._broadcast = record
    snapshot_reads = 0

    def read_snapshot_once():
        nonlocal snapshot_reads
        snapshot_reads += 1
        return trailer, '["1","FRONT-1","BACK-1"]'

    server._read_trailer_snapshot = read_snapshot_once
    old_dashboard_state = {
        "version": "V2.45",
        "requested_gal": 0.0,
        "actual_gal": 0.0,
        "flow_gpm": 0.0,
        "mode": "fill",
        "mopeka_enabled": True,
        "mopeka_connected": True,
        "front_tank_gal": 10.0,
        "front_tank_quality": 3,
    }

    asyncio.run(server._publish_dashboard_state(old_dashboard_state))

    assert server._legacy_sensor_identity_initialized is True
    assert server._sensor_cache_identity_confirmed is True
    assert snapshot_reads == 1
    assert [message["type"] for message in messages] == [
        "state",
        "trailer_config",
        "mopeka",
    ]
    sys.modules.pop("rotorlink.server", None)


def test_rotorlink_external_identity_change_suppresses_unconfirmed_cache(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        types.SimpleNamespace(ConnectionClosed=Exception, serve=None),
    )
    sys.modules.pop("rotorlink.server", None)
    server_module = importlib.import_module("rotorlink.server")
    server = server_module.RotorLinkServer()
    trailer_one = {
        "box_mode": "fleet",
        "trailer": 1,
        "front": {"id": "FRONT-1"},
        "back": {"id": "BACK-1"},
    }
    trailer_two = {
        "box_mode": "fleet",
        "trailer": 2,
        "front": {"id": "FRONT-2"},
        "back": {"id": "BACK-2"},
    }
    messages = []

    async def record(message):
        messages.append(message)

    server._broadcast = record
    trailer_one_identity = '["1","FRONT-1","BACK-1"]'
    trailer_two_identity = '["2","FRONT-2","BACK-2"]'
    server._synchronize_sensor_cache_identity(trailer_one_identity, confirmed=True)
    assert server._sensor_cache_identity_confirmed is True

    # A config-file edit has no matching dashboard generation barrier. Clear
    # the RotorLink cache and fail closed instead of rebinding stale state.
    assert server._synchronize_sensor_cache_identity(trailer_two_identity) is True
    assert server._sensor_cache_identity_confirmed is False
    stale_dashboard_state = {
        "mopeka_enabled": True,
        "front_tank_has_reading": True,
        "front_tank_gal": 10.0,
        "front_tank_quality": 3,
        "front_tank_last_update": 100.0,
    }

    asyncio.run(server._broadcast_sensors(stale_dashboard_state))

    assert messages == []
    assert server._last_mopeka == {1: None, 2: None}
    sys.modules.pop("rotorlink.server", None)


def test_fresh_rotorlink_rejects_dashboard_cache_owned_by_other_trailer(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        types.SimpleNamespace(ConnectionClosed=Exception, serve=None),
    )
    sys.modules.pop("rotorlink.server", None)
    server_module = importlib.import_module("rotorlink.server")
    server = server_module.RotorLinkServer()
    trailer_two = {
        "box_mode": "fleet",
        "trailer": 2,
        "front": {"id": "FRONT-2"},
        "back": {"id": "BACK-2"},
    }
    server._read_trailer_snapshot = lambda: (
        trailer_two,
        '["2","FRONT-2","BACK-2"]',
    )
    messages = []

    async def record(message):
        messages.append(message)

    server._broadcast = record
    stale = {
        "version": "V2.46",
        "requested_gal": 0.0,
        "actual_gal": 0.0,
        "flow_gpm": 0.0,
        "mode": "fill",
        "trailer_sensor_identity_generation": 4,
        "trailer_sensor_identity": '["1","FRONT-1","BACK-1"]',
        "mopeka_enabled": True,
        "front_tank_has_reading": True,
        "front_tank_gal": 10.0,
        "front_tank_quality": 3,
        "front_tank_last_update": 100.0,
    }

    asyncio.run(server._publish_dashboard_state(stale))

    assert server._sensor_cache_identity_confirmed is False
    assert not any(message["type"] == "mopeka" for message in messages)

    current = dict(
        stale,
        trailer_sensor_identity='["2","FRONT-2","BACK-2"]',
        front_tank_last_update=101.0,
    )
    asyncio.run(server._publish_dashboard_state(current))

    assert server._sensor_cache_identity_confirmed is True
    sensor_messages = [
        message for message in messages if message["type"] == "mopeka"
    ]
    assert len(sensor_messages) == 1
    assert sensor_messages[0]["mopeka"]["last_update"] == 101.0
    sys.modules.pop("rotorlink.server", None)


def test_rotorlink_revokes_confirmation_when_dashboard_owner_diverges(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        types.SimpleNamespace(ConnectionClosed=Exception, serve=None),
    )
    sys.modules.pop("rotorlink.server", None)
    server_module = importlib.import_module("rotorlink.server")
    server = server_module.RotorLinkServer()
    trailer_two = {
        "box_mode": "fleet",
        "trailer": 2,
        "front": {"id": "FRONT-2"},
        "back": {"id": "BACK-2"},
    }
    server._read_trailer_snapshot = lambda: (
        trailer_two,
        '["2","FRONT-2","BACK-2"]',
    )
    messages = []

    async def record(message):
        messages.append(message)

    server._broadcast = record
    current = {
        "version": "V2.46",
        "requested_gal": 0.0,
        "actual_gal": 0.0,
        "flow_gpm": 0.0,
        "mode": "fill",
        "trailer_sensor_identity_generation": 4,
        "trailer_sensor_identity": '["2","FRONT-2","BACK-2"]',
        "mopeka_enabled": True,
        "front_tank_has_reading": True,
        "front_tank_gal": 30.0,
        "front_tank_quality": 3,
        "front_tank_last_update": 100.0,
    }
    asyncio.run(server._publish_dashboard_state(current))
    assert server._sensor_cache_identity_confirmed is True
    assert server._last_mopeka[1] is not None
    messages.clear()

    divergent = dict(
        current,
        trailer_sensor_identity='["1","FRONT-1","BACK-1"]',
        front_tank_gal=10.0,
        front_tank_last_update=101.0,
    )
    asyncio.run(server._publish_dashboard_state(divergent))

    assert server._sensor_cache_identity_confirmed is False
    assert server._last_bms is None
    assert server._last_mopeka == {1: None, 2: None}
    assert not any(message["type"] == "mopeka" for message in messages)
    sys.modules.pop("rotorlink.server", None)


def test_rotorlink_suppresses_initialized_zeroes_and_supports_partial_tanks():
    state = {
        "mopeka_enabled": True,
        "mopeka_connected": True,
        "front_tank_has_reading": True,
        "back_tank_has_reading": False,
        "front_tank_gal": 0.0,
        "front_tank_quality": 3,
        "front_tank_last_update": 1720000000.25,
        "back_tank_gal": 0.0,
        "back_tank_quality": 0,
    }

    assert state_encoder.encode_mopeka(state, 1) == {
        "gallons": 0.0,
        "quality": 3,
        "last_update": 1720000000.25,
    }
    assert state_encoder.encode_mopeka(state, 2) is None


def test_rotorlink_does_not_fabricate_or_forward_invalid_timestamps():
    bms = state_encoder.encode_bms({
        "bms_has_reading": True,
        "bms_voltage": 13.4,
        "bms_soc": None,
        "bms_last_update": math.nan,
    })
    tank = state_encoder.encode_mopeka({
        "mopeka_enabled": True,
        "mopeka_connected": True,
        "front_tank_has_reading": True,
        "front_tank_gal": 42.0,
        "front_tank_quality": None,
        "front_tank_last_update": None,
    }, 1)

    assert bms == {"voltage": 13.4}
    assert tank == {"gallons": 42.0}
    assert "last_update" not in bms
    assert "last_update" not in tank


def test_rotorlink_accepts_legacy_dashboard_state_without_new_flags():
    state = {
        "bms_voltage": 13.2,
        "bms_soc": 81,
        "mopeka_enabled": True,
        "mopeka_connected": True,
        "front_tank_gal": 55.5,
        "front_tank_quality": 2,
    }

    assert state_encoder.encode_bms(state) == {"voltage": 13.2, "soc": 81}
    assert state_encoder.encode_mopeka(state, 1) == {
        "gallons": 55.5,
        "quality": 2,
    }
