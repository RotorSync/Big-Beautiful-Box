import asyncio
import contextlib
import importlib
import json
import sys
import types

import pytest

from tests.test_maintenance_auth import install_bumble_stubs


@pytest.fixture
def bumble_module(monkeypatch):
    install_bumble_stubs(monkeypatch)
    sys.modules.pop('rotorsync_bumble', None)
    module = importlib.import_module('rotorsync_bumble')
    monkeypatch.setattr(module, 'mark_gatt_client_seen', lambda *_args, **_kwargs: None)
    yield module
    if module.control_command_worker_task:
        module.control_command_worker_task.cancel()
    if module.gatt_connection_bookkeeping_task:
        module.gatt_connection_bookkeeping_task.cancel()
    if module.connected_advertising_maintainer_task:
        module.connected_advertising_maintainer_task.cancel()
    sys.modules.pop('rotorsync_bumble', None)


def connection(peer):
    return types.SimpleNamespace(peer_address=peer)


class FakeConnection:
    def __init__(self, peer):
        self.peer_address = peer
        self.listeners = {}
        self.disconnect_calls = 0

    def on(self, event, callback):
        self.listeners[event] = callback

    async def disconnect(self):
        self.disconnect_calls += 1


class FakeGattDevice:
    def __init__(self):
        self.advertising = False
        self.public_address = 'AA:BB:CC:DD:EE:FF'
        self.listeners = {}
        self.start_calls = []
        self.stop_calls = []

    def on(self, event, callback):
        self.listeners[event] = callback

    async def start_advertising(self, **kwargs):
        self.start_calls.append(kwargs)
        self.advertising = True

    async def stop_advertising(self):
        self.stop_calls.append({})
        self.advertising = False


async def stop_worker(module):
    if module.control_command_worker_task:
        module.control_command_worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await module.control_command_worker_task


def test_control_writes_are_processed_in_arrival_order(bumble_module, monkeypatch):
    sent = []
    queries = []

    monkeypatch.setattr(
        bumble_module,
        'send_dashboard_command',
        lambda cmd: sent.append(cmd) or 'OK',
    )
    monkeypatch.setattr(
        bumble_module,
        'query_dashboard_status',
        lambda: queries.append('query') or True,
    )

    async def run():
        bumble_module.start_control_command_worker()
        bumble_module.gallons_write_handler(connection('ipad'), b'+1')
        bumble_module.command_write_handler(
            connection('iphone'),
            json.dumps({'cmd': 'adjust', 'delta': 10}).encode('utf-8'),
        )
        bumble_module.pump_write_handler(connection('ipad'), b'PS')

        await bumble_module.control_command_queue.join()
        await stop_worker(bumble_module)

    asyncio.run(run())

    assert sent == ['+1', '+10', 'PS']
    assert queries == ['query', 'query', 'query']


def test_control_writes_keep_peer_and_source_metadata(bumble_module, monkeypatch):
    processed = []

    def capture(item):
        processed.append(item.copy())

    monkeypatch.setattr(bumble_module, '_run_control_command', capture)

    async def run():
        bumble_module.start_control_command_worker()
        bumble_module.command_write_handler(
            connection('iphone'),
            json.dumps({'cmd': 'set_mode', 'mode': 'mix'}).encode('utf-8'),
        )

        await bumble_module.control_command_queue.join()
        await stop_worker(bumble_module)

    asyncio.run(run())

    assert processed == [
        {
            'seq': 1,
            'peer': 'iphone',
            'source': 'command:set_mode',
            'actions': ['MIX'],
            'refresh': True,
        }
    ]


def test_reset_flow_command_forwards_dashboard_reset(bumble_module, monkeypatch):
    sent = []
    queries = []

    monkeypatch.setattr(
        bumble_module,
        'send_dashboard_command',
        lambda cmd: sent.append(cmd) or 'OK',
    )
    monkeypatch.setattr(
        bumble_module,
        'query_dashboard_status',
        lambda: queries.append('query') or True,
    )

    bumble_module.command_write_handler(
        connection('iphone'),
        json.dumps({'cmd': 'reset_flow'}).encode('utf-8'),
    )

    assert sent == ['RESET']
    assert queries == ['query']


def test_confirm_fill_command_forwards_dashboard_tu(bumble_module, monkeypatch):
    sent = []
    queries = []

    monkeypatch.setattr(
        bumble_module,
        'send_dashboard_command',
        lambda cmd: sent.append(cmd) or 'OK',
    )
    monkeypatch.setattr(
        bumble_module,
        'query_dashboard_status',
        lambda: queries.append('query') or True,
    )

    bumble_module.command_write_handler(
        connection('iphone'),
        json.dumps({'cmd': 'confirm_fill'}).encode('utf-8'),
    )

    assert sent == ['TU']
    assert queries == ['query']


def test_ov_command_forwards_dashboard_ov(bumble_module, monkeypatch):
    sent = []
    queries = []

    monkeypatch.setattr(
        bumble_module,
        'send_dashboard_command',
        lambda cmd: sent.append(cmd) or 'OK',
    )
    monkeypatch.setattr(
        bumble_module,
        'query_dashboard_status',
        lambda: queries.append('query') or True,
    )

    bumble_module.command_write_handler(
        connection('iphone'),
        json.dumps({'cmd': 'ov'}).encode('utf-8'),
    )

    assert sent == ['OV']
    assert queries == ['query']


def test_set_target_command_forwards_absolute_dashboard_target(bumble_module, monkeypatch):
    sent = []
    queries = []

    monkeypatch.setattr(
        bumble_module,
        'send_dashboard_command',
        lambda cmd: sent.append(cmd) or 'OK',
    )
    monkeypatch.setattr(
        bumble_module,
        'query_dashboard_status',
        lambda: queries.append('query') or True,
    )

    bumble_module.command_write_handler(
        connection('iphone'),
        json.dumps({'cmd': 'set_target', 'gallons': 87}).encode('utf-8'),
    )

    assert sent == ['SET_REQUESTED_GALLONS:87.000']
    assert queries == ['query']


def test_set_target_command_clamps_picker_range(bumble_module, monkeypatch):
    sent = []
    monkeypatch.setattr(
        bumble_module,
        'send_dashboard_command',
        lambda cmd: sent.append(cmd) or 'OK',
    )
    monkeypatch.setattr(bumble_module, 'query_dashboard_status', lambda: True)

    bumble_module.command_write_handler(
        connection('iphone'),
        json.dumps({'cmd': 'set_target', 'gallons': 3000}).encode('utf-8'),
    )

    assert sent == ['SET_REQUESTED_GALLONS:2140.000']


def test_set_target_command_ignores_invalid_numbers(bumble_module, monkeypatch):
    sent = []
    monkeypatch.setattr(
        bumble_module,
        'send_dashboard_command',
        lambda cmd: sent.append(cmd) or 'OK',
    )
    monkeypatch.setattr(bumble_module, 'query_dashboard_status', lambda: True)

    bumble_module.command_write_handler(
        connection('iphone'),
        json.dumps({'cmd': 'set_target', 'gallons': 'nope'}).encode('utf-8'),
    )

    assert sent == []


@pytest.mark.parametrize(
    ('command', 'expected_action'),
    [
        ('reboot_box', 'REBOOT'),
        ('shutdown_box', 'SHUTDOWN'),
    ],
)
def test_power_commands_forward_without_refresh(bumble_module, monkeypatch, command, expected_action):
    sent = []
    queries = []

    monkeypatch.setattr(
        bumble_module,
        'send_dashboard_command',
        lambda cmd: sent.append(cmd) or 'OK',
    )
    monkeypatch.setattr(
        bumble_module,
        'query_dashboard_status',
        lambda: queries.append('query') or True,
    )

    bumble_module.command_write_handler(
        connection('iphone'),
        json.dumps({'cmd': command}).encode('utf-8'),
    )

    assert sent == [expected_action]
    assert queries == []


def test_accept_pending_curve_command_forwards_dashboard_apply(bumble_module, monkeypatch):
    sent = []
    queries = []

    monkeypatch.setattr(
        bumble_module,
        'send_dashboard_command',
        lambda cmd: sent.append(cmd) or 'CURVE_ACCEPTED:{}',
    )
    monkeypatch.setattr(
        bumble_module,
        'query_dashboard_status',
        lambda: queries.append('query') or True,
    )

    bumble_module.command_write_handler(
        connection('iphone'),
        json.dumps({'cmd': 'accept_pending_curve'}).encode('utf-8'),
    )

    assert sent == ['ACCEPT_PENDING_CURVE']
    assert queries == ['query']


@pytest.mark.parametrize(
    ('payload', 'expected_action'),
    [
        (
            {'cmd': 'cursor_move', 'dx': 12, 'dy': -8},
            'MOUSE:{"dx":12,"dy":-8,"action":"move"}',
        ),
        (
            {'cmd': 'cursor_scroll', 'steps': -3},
            'MOUSE:{"steps":-3,"action":"scroll"}',
        ),
        (
            {'cmd': 'cursor_click', 'button': 3},
            'MOUSE:{"button":3,"action":"click"}',
        ),
        (
            {'cmd': 'cursor_key', 'key': 'alt_f4'},
            'MOUSE:{"key":"alt_f4","action":"key"}',
        ),
    ],
)
def test_cursor_commands_forward_without_refresh(bumble_module, monkeypatch, payload, expected_action):
    sent = []
    queries = []

    monkeypatch.setattr(
        bumble_module,
        'send_dashboard_command',
        lambda cmd: sent.append(cmd) or 'MOUSE_OK',
    )
    monkeypatch.setattr(
        bumble_module,
        'query_dashboard_status',
        lambda: queries.append('query') or True,
    )

    bumble_module.command_write_handler(
        connection('iphone'),
        json.dumps(payload).encode('utf-8'),
    )

    assert sent == [expected_action]
    assert queries == []


def test_control_write_falls_back_inline_before_worker_starts(bumble_module, monkeypatch):
    sent = []
    queries = []

    monkeypatch.setattr(
        bumble_module,
        'send_dashboard_command',
        lambda cmd: sent.append(cmd) or 'OK',
    )
    monkeypatch.setattr(
        bumble_module,
        'query_dashboard_status',
        lambda: queries.append('query') or True,
    )

    bumble_module.command_write_handler(
        connection('ipad'),
        json.dumps({'cmd': 'set_override', 'enabled': True}).encode('utf-8'),
    )

    assert sent == ['OV:1']
    assert queries == ['query']


def test_config_responses_are_isolated_by_ble_peer(bumble_module):
    ipad = connection('ipad')
    iphone = connection('iphone')

    bumble_module.config_cmd_write_handler(ipad, b'{not-json')
    bumble_module.config_cmd_write_handler(
        iphone,
        json.dumps({'op': 'NO_SUCH_OP', 'request_id': 'phone-1'}).encode('utf-8'),
    )

    ipad_response = json.loads(bumble_module.config_data_read_handler(ipad))
    iphone_response = json.loads(bumble_module.config_data_read_handler(iphone))

    assert ipad_response['error'].startswith('Invalid JSON')
    assert iphone_response == {
        'ok': False,
        'error': 'Unknown op: NO_SUCH_OP',
        'request_id': 'phone-1',
        'op': 'NO_SUCH_OP',
    }


def test_state_payload_stays_compact_with_curve_labels(bumble_module):
    payload = json.loads(bumble_module._encode_ble_state_payload({
        'version': 'V2.8',
        'requested_gal': 10.0,
        'actual_gal': 0.0,
        'flow_gpm': 0.0,
        'mode': 'fill',
        'override': False,
        'thumbs_visible': False,
        'fill_pending': False,
        'can_confirm_fill': False,
        'colors_green': False,
        'pump_stop_latched': False,
        'flow_meter_connected': True,
        'switch_box_connected': True,
        'current_curve': 'Learned -0.10 gal',
        'pending_curve': 'No pending curve',
    }))

    assert payload == {
        'ver': 'V2.8',
        'req': 10.0,
        'act': 0.0,
        'flow': 0.0,
        'mode': 'fill',
        'bc': 1,
        'cc': '-0.10',
    }
    assert len(json.dumps(payload, separators=(',', ':'))) < 160


def test_live_telemetry_payload_includes_requested_actual_and_flow(bumble_module):
    payload = json.loads(bumble_module._encode_live_telemetry_payload(10.0, 12.3456, 78.901))

    assert payload == {
        'req': 10.0,
        'act': 12.346,
        'flow': 78.9,
        'rs': False,
        'ff': False,
    }
    assert len(json.dumps(payload, separators=(',', ':'))) < 70


def test_state_payload_includes_active_flow_fault_summary(bumble_module):
    payload = json.loads(bumble_module._encode_ble_state_payload({
        'version': 'V2.30',
        'requested_gal': 25.0,
        'actual_gal': -1.25,
        'flow_gpm': -0.5,
        'mode': 'fill',
        'negative_totalizer_fault': True,
        'negative_totalizer_gal': -1.25,
        'negative_flow_fault': True,
        'negative_flow_gpm': -0.5,
        'flow_meter_fault_reason': 'NEGATIVE FLOW METER - GALLON RESET REQUIRED',
    }))

    assert payload['ntf'] is True
    assert payload['ntg'] == -1.25
    assert payload['nff'] is True
    assert payload['nfg'] == -0.5
    assert payload['ff'] is True
    assert payload['fc'] == 'negative_totalizer'
    assert payload['fmr'] == 'NEGATIVE FLOW METER - GALLON RESET REQUIRED'


def test_live_telemetry_payload_includes_active_flow_fault_summary(bumble_module):
    payload = json.loads(bumble_module._encode_live_telemetry_payload(
        10.0,
        12.3456,
        3.21,
        False,
        True,
        'positive_drift',
        'FLOW METER DRIFT - RESET REQUIRED',
    ))

    assert payload == {
        'req': 10.0,
        'act': 12.346,
        'flow': 3.21,
        'rs': False,
        'ff': True,
        'fc': 'positive_drift',
        'fmr': 'FLOW METER DRIFT - RESET REQUIRED',
    }


def test_client_hello_marks_pilot_priority_state(bumble_module, monkeypatch):
    queries = []
    monkeypatch.setattr(bumble_module, 'query_dashboard_status', lambda: queries.append(True))

    bumble_module.active_gatt_connections.update({'iphone', 'ipad'})
    bumble_module.command_write_handler(
        connection('iphone'),
        json.dumps({
            'cmd': 'client_hello',
            'role': 'pilot',
            'user_id': 'pilot-1',
            'device': 'Norman iPhone',
        }).encode('utf-8'),
    )

    payload = json.loads(bumble_module._encode_ble_state_payload({
        'version': 'V2.8',
        'requested_gal': 20.0,
        'actual_gal': 1.0,
        'flow_gpm': 2.0,
        'mode': 'fill',
    }))

    assert bumble_module.gatt_client_metadata_by_connection['iphone']['role'] == 'pilot'
    assert payload['pilot'] is True
    assert payload['prio'] is True
    assert queries == [True]


def test_state_notify_compare_can_ignore_live_only_fields(bumble_module):
    first = json.dumps(
        {'req': 30.0, 'act': 1.234, 'flow': 88.0, 'mode': 'fill'},
        separators=(',', ':'),
    )
    second = json.dumps(
        {'req': 30.0, 'act': 2.345, 'flow': 91.0, 'mode': 'fill'},
        separators=(',', ':'),
    )
    requested_changed = json.dumps(
        {'req': 31.0, 'act': 2.345, 'flow': 91.0, 'mode': 'fill'},
        separators=(',', ':'),
    )

    assert bumble_module._state_notify_compare_json(first) != second
    assert bumble_module._state_notify_compare_json(
        first,
        suppress_live_fields=True,
    ) == bumble_module._state_notify_compare_json(
        second,
        suppress_live_fields=True,
    )
    assert bumble_module._state_notify_compare_json(
        first,
        suppress_live_fields=True,
    ) != bumble_module._state_notify_compare_json(
        requested_changed,
        suppress_live_fields=True,
    )


def test_state_live_fields_suppress_only_for_multipoint_active_flow(bumble_module):
    assert not bumble_module._state_notify_should_suppress_live_fields(
        controller_count=1,
        state={'flow_gpm': 100.0},
    )
    assert not bumble_module._state_notify_should_suppress_live_fields(
        controller_count=2,
        state={'flow_gpm': 0.0},
    )
    assert bumble_module._state_notify_should_suppress_live_fields(
        controller_count=2,
        state={'flow_gpm': 100.0},
    )


def test_live_telemetry_read_queries_fresh_dashboard_values(bumble_module, monkeypatch):
    monkeypatch.setattr(
        bumble_module,
        'send_dashboard_command',
        lambda cmd: 'LIVE:{"req":12.0,"act":4.321,"flow":65.5,"ff":true,"fc":"positive_drift","fmr":"FLOW METER DRIFT"}' if cmd == 'LIVE_TELEMETRY' else None,
    )

    read_value = bumble_module.make_live_telemetry_read_handler()
    payload = json.loads(read_value(connection('iphone')).decode('utf-8'))

    assert payload == {
        'req': 12.0,
        'act': 4.321,
        'flow': 65.5,
        'rs': False,
        'ff': True,
        'fc': 'positive_drift',
        'fmr': 'FLOW METER DRIFT',
    }
    assert bumble_module.dashboard_status['requested'] == 12.0
    assert bumble_module.dashboard_status['actual'] == 4.321


def test_live_telemetry_read_clears_cached_flow_fault_summary(bumble_module, monkeypatch):
    bumble_module.dashboard_status['state'] = {
        'requested_gal': 12.0,
        'actual_gal': -2.0,
        'flow_gpm': -1.0,
        'negative_totalizer_fault': True,
        'negative_totalizer_gal': -2.0,
        'flow_meter_fault_reason': 'NEGATIVE FLOW METER',
    }
    monkeypatch.setattr(
        bumble_module,
        'send_dashboard_command',
        lambda cmd: 'LIVE:{"req":12.0,"act":0.0,"flow":0.0,"ff":false}' if cmd == 'LIVE_TELEMETRY' else None,
    )

    assert bumble_module.query_live_telemetry() is True

    payload = json.loads(bumble_module.dashboard_status['live_json'])
    assert payload['ff'] is False
    state = bumble_module.dashboard_status['state']
    assert state['flow_fault_active'] is False
    assert state['negative_totalizer_fault'] is False
    assert state['flow_meter_fault_reason'] == ''


def test_live_telemetry_single_controller_notify_is_immediate(bumble_module):
    assert bumble_module._live_telemetry_notify_due(
        controller_count=1,
        now=100.0,
        last_notify_at=99.99,
    )


def test_live_telemetry_multipoint_notify_is_throttled(bumble_module):
    assert not bumble_module._live_telemetry_notify_due(
        controller_count=2,
        now=100.0,
        last_notify_at=99.5,
        flow_active=False,
    )
    assert bumble_module._live_telemetry_notify_due(
        controller_count=2,
        now=100.0,
        last_notify_at=99.25,
        flow_active=False,
    )


def test_live_telemetry_active_flow_notify_is_immediate_for_multipoint(bumble_module):
    assert bumble_module._live_telemetry_notify_due(
        controller_count=2,
        now=100.0,
        last_notify_at=99.99,
        flow_active=True,
    )


def test_live_telemetry_active_flow_notify_is_throttled_for_pilot_priority(bumble_module):
    assert not bumble_module._live_telemetry_notify_due(
        controller_count=2,
        now=100.0,
        last_notify_at=99.5,
        flow_active=True,
        pilot_priority_active=True,
    )
    assert bumble_module._live_telemetry_notify_due(
        controller_count=2,
        now=100.0,
        last_notify_at=99.25,
        flow_active=True,
        pilot_priority_active=True,
    )


def test_gatt_advertising_resume_hook_keeps_advertising_on(bumble_module, monkeypatch):
    persisted = []
    device = FakeGattDevice()
    monkeypatch.delenv('ROTORSYNC_DISABLE_CONNECTED_ADVERTISING', raising=False)

    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_advertising_ready',
        lambda name, address: persisted.append((name, address)),
    )

    async def run():
        installed = bumble_module.install_gatt_advertising_resume_hook(
            device,
            b'adv',
            b'scan',
            'TrailerSync-TR2',
        )
        assert installed is True
        iphone = FakeConnection('iphone')
        device.listeners['connection'](iphone)
        deadline = asyncio.get_running_loop().time() + 1
        while not device.start_calls and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        ipad = FakeConnection('ipad')
        device.listeners['connection'](ipad)
        await asyncio.sleep(0.05)

    asyncio.run(run())

    assert device.start_calls == [
        {
            'advertising_data': b'adv',
            'scan_response_data': b'scan',
            'auto_restart': False,
        }
    ]
    assert device.stop_calls == []
    assert persisted == [('TrailerSync-TR2', 'AA:BB:CC:DD:EE:FF')]


def test_gatt_advertising_stays_on_when_one_client_disconnects(
    bumble_module,
    monkeypatch,
):
    persisted = []
    device = FakeGattDevice()
    monkeypatch.delenv('ROTORSYNC_DISABLE_CONNECTED_ADVERTISING', raising=False)
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_advertising_ready',
        lambda name, address: persisted.append((name, address)),
    )

    async def run():
        installed = bumble_module.install_gatt_advertising_resume_hook(
            device,
            b'adv',
            b'scan',
            'TrailerSync-TR2',
        )
        assert installed is True

        ipad = FakeConnection('ipad')
        iphone = FakeConnection('iphone')
        device.listeners['connection'](ipad)
        deadline = asyncio.get_running_loop().time() + 1
        while not device.start_calls and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        device.listeners['connection'](iphone)
        iphone.listeners['disconnection']()
        await asyncio.sleep(0.05)

    asyncio.run(run())

    assert bumble_module.active_gatt_connections == {'ipad'}
    assert device.advertising is True
    assert len(device.start_calls) == 1
    assert device.stop_calls == []


def test_gatt_advertising_maintainer_retries_while_one_client_connected(
    bumble_module,
    monkeypatch,
):
    persisted = []
    device = FakeGattDevice()
    monkeypatch.delenv('ROTORSYNC_DISABLE_CONNECTED_ADVERTISING', raising=False)
    monkeypatch.setattr(
        bumble_module,
        'GATT_CONNECTED_ADVERTISING_VERIFY_INTERVAL_SECONDS',
        0.01,
    )
    monkeypatch.setattr(
        bumble_module,
        'GATT_CONNECTED_ADVERTISING_RETRY_INTERVAL_SECONDS',
        0.01,
    )
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_advertising_ready',
        lambda name, address: persisted.append((name, address)),
    )

    async def run():
        installed = bumble_module.install_gatt_advertising_resume_hook(
            device,
            b'adv',
            b'scan',
            'TrailerSync-TR2',
        )
        assert installed is True

        ipad = FakeConnection('ipad')
        device.listeners['connection'](ipad)
        deadline = asyncio.get_running_loop().time() + 1
        while not device.start_calls and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        device.advertising = False
        deadline = asyncio.get_running_loop().time() + 1
        while len(device.start_calls) < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

    asyncio.run(run())

    assert bumble_module.active_gatt_connections == {'ipad'}
    assert len(device.start_calls) == 2
    assert device.stop_calls == []
    assert persisted == [
        ('TrailerSync-TR2', 'AA:BB:CC:DD:EE:FF'),
        ('TrailerSync-TR2', 'AA:BB:CC:DD:EE:FF'),
    ]


def test_gatt_advertising_maintainer_pauses_when_second_client_connects(
    bumble_module,
    monkeypatch,
):
    device = FakeGattDevice()
    monkeypatch.delenv('ROTORSYNC_DISABLE_CONNECTED_ADVERTISING', raising=False)
    monkeypatch.setattr(
        bumble_module,
        'GATT_CONNECTED_ADVERTISING_VERIFY_INTERVAL_SECONDS',
        0.01,
    )
    monkeypatch.setattr(
        bumble_module,
        'GATT_CONNECTED_ADVERTISING_RETRY_INTERVAL_SECONDS',
        0.01,
    )
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_advertising_ready',
        lambda _name, _address: None,
    )

    async def run():
        installed = bumble_module.install_gatt_advertising_resume_hook(
            device,
            b'adv',
            b'scan',
            'TrailerSync-TR2',
        )
        assert installed is True

        ipad = FakeConnection('ipad')
        iphone = FakeConnection('iphone')
        device.listeners['connection'](ipad)
        deadline = asyncio.get_running_loop().time() + 1
        while not device.start_calls and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        device.listeners['connection'](iphone)
        await asyncio.sleep(0.05)

    asyncio.run(run())

    assert bumble_module.active_gatt_connections == {'ipad', 'iphone'}
    assert len(device.start_calls) == 1
    assert device.stop_calls == []


def test_gatt_advertising_maintainer_does_not_start_if_second_client_arrives_during_delay(
    bumble_module,
    monkeypatch,
):
    device = FakeGattDevice()
    monkeypatch.delenv('ROTORSYNC_DISABLE_CONNECTED_ADVERTISING', raising=False)
    monkeypatch.setattr(
        bumble_module,
        'GATT_CONNECTED_ADVERTISING_VERIFY_INTERVAL_SECONDS',
        0.01,
    )
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_advertising_ready',
        lambda _name, _address: None,
    )

    async def run():
        installed = bumble_module.install_gatt_advertising_resume_hook(
            device,
            b'adv',
            b'scan',
            'TrailerSync-TR2',
        )
        assert installed is True

        ipad = FakeConnection('ipad')
        iphone = FakeConnection('iphone')
        device.listeners['connection'](ipad)
        device.listeners['connection'](iphone)
        await asyncio.sleep(bumble_module.GATT_ADVERTISING_RESUME_DELAY_SECONDS + 0.05)

    asyncio.run(run())

    assert bumble_module.active_gatt_connections == {'ipad', 'iphone'}
    assert device.start_calls == []
    assert device.stop_calls == []


def test_gatt_connected_advertising_helper_only_runs_for_one_client(
    bumble_module,
    monkeypatch,
):
    persisted = []
    device = FakeGattDevice()
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_advertising_ready',
        lambda name, address: persisted.append((name, address)),
    )

    async def run():
        bumble_module.active_gatt_connections.update({'ipad', 'iphone'})
        result = await bumble_module.keep_gatt_connected_advertising_on(
            device,
            b'adv',
            b'scan',
            'TrailerSync-TR2',
            delay=0,
        )
        assert result is False

    asyncio.run(run())

    assert device.start_calls == []
    assert persisted == []


def test_connected_self_adv_refresh_does_not_run_while_anchor_connected(bumble_module):
    bumble_module.active_gatt_connections.add('ipad')
    bumble_module.last_gatt_advertising_ready_at = 100.0
    bumble_module.last_gatt_self_adv_seen_write = 0.0

    assert bumble_module.connected_self_adv_refresh_due(now=129.0) is False
    assert bumble_module.connected_self_adv_refresh_due(now=300.0) is False


def test_connected_self_adv_refresh_waits_for_recent_self_scan(bumble_module):
    bumble_module.active_gatt_connections.add('ipad')
    bumble_module.last_gatt_advertising_ready_at = 100.0
    bumble_module.last_gatt_self_adv_seen_write = 170.0

    assert bumble_module.connected_self_adv_refresh_due(now=199.0) is False
    assert bumble_module.connected_self_adv_refresh_due(now=300.0) is False


def test_connected_self_adv_refresh_does_not_run_for_multipoint(bumble_module):
    bumble_module.active_gatt_connections.update({'ipad', 'iphone'})
    bumble_module.last_gatt_advertising_ready_at = 100.0

    assert bumble_module.connected_self_adv_refresh_due(now=300.0) is False


def test_refresh_gatt_advertising_for_discoverability_restarts_advert(
    bumble_module,
    monkeypatch,
):
    persisted = []
    device = FakeGattDevice()
    device.advertising = True
    bumble_module.active_gatt_connections.add('ipad')
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_advertising_ready',
        lambda name, address: persisted.append((name, address)),
    )

    async def run():
        result = await bumble_module.refresh_gatt_advertising_for_discoverability(
            device,
            b'adv',
            b'scan',
            'TrailerSync-TR2',
            reason='test',
        )
        assert result is True

    asyncio.run(run())

    assert device.stop_calls == [{}]
    assert device.start_calls == [
        {
            'advertising_data': b'adv',
            'scan_response_data': b'scan',
            'auto_restart': False,
        }
    ]
    assert persisted == [('TrailerSync-TR2', 'AA:BB:CC:DD:EE:FF')]


def test_find_sensor_peer_by_name_ignores_duplicate_matches(
    bumble_module,
    monkeypatch,
):
    monkeypatch.setattr(
        bumble_module.AdvertisingData,
        'Type',
        types.SimpleNamespace(COMPLETE_LOCAL_NAME=9, SHORTENED_LOCAL_NAME=8),
        raising=False,
    )

    class FakeAdData:
        def get(self, key):
            if key == 9:
                return 'TR2-BMS'
            return None

    class FakeSensorDevice:
        def __init__(self):
            self.scanning = False
            self.listener = None
            self.removed = []
            self.stop_calls = 0

        def on(self, event, callback):
            assert event == 'advertisement'
            self.listener = callback

        def remove_listener(self, event, callback):
            self.removed.append((event, callback))

        async def start_scanning(self, **_kwargs):
            self.scanning = True
            advertisement = types.SimpleNamespace(
                address='AA:BB:CC:DD:EE:FF',
                data=FakeAdData(),
            )
            self.listener(advertisement)
            self.listener(advertisement)

        async def stop_scanning(self):
            self.stop_calls += 1
            self.scanning = False

    async def run():
        device = FakeSensorDevice()
        address = await bumble_module.find_sensor_peer_by_name(
            device,
            'TR2-BMS',
            timeout=0.1,
        )
        assert address == 'AA:BB:CC:DD:EE:FF'
        assert device.stop_calls == 1
        assert len(device.removed) == 1

    asyncio.run(run())


def test_gatt_duplicate_connection_event_does_not_restart_advertising(
    bumble_module,
    monkeypatch,
):
    device = FakeGattDevice()
    monkeypatch.delenv('ROTORSYNC_DISABLE_CONNECTED_ADVERTISING', raising=False)
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_advertising_ready',
        lambda _name, _address: None,
    )

    async def run():
        installed = bumble_module.install_gatt_advertising_resume_hook(
            device,
            b'adv',
            b'scan',
            'TrailerSync-TR2',
        )
        assert installed is True

        ipad = FakeConnection('ipad')
        iphone = FakeConnection('iphone')
        device.listeners['connection'](ipad)
        deadline = asyncio.get_running_loop().time() + 1
        while not device.start_calls and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        device.listeners['connection'](iphone)
        device.listeners['connection'](iphone)
        await asyncio.sleep(0.05)

    asyncio.run(run())

    assert bumble_module.active_gatt_connections == {'ipad', 'iphone'}
    assert bumble_module.active_gatt_connection_counts['iphone'] == 1
    assert len(device.start_calls) == 1


def test_gatt_duplicate_connection_event_disconnect_does_not_leave_phantom_anchor(
    bumble_module,
    monkeypatch,
):
    device = FakeGattDevice()
    monkeypatch.delenv('ROTORSYNC_DISABLE_CONNECTED_ADVERTISING', raising=False)
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_advertising_ready',
        lambda _name, _address: None,
    )

    async def run():
        installed = bumble_module.install_gatt_advertising_resume_hook(
            device,
            b'adv',
            b'scan',
            'TrailerSync-TR2',
        )
        assert installed is True

        ipad = FakeConnection('ipad')
        iphone = FakeConnection('iphone')
        device.listeners['connection'](ipad)
        device.listeners['connection'](iphone)
        device.listeners['connection'](iphone)
        iphone.listeners['disconnection']()
        await asyncio.sleep(0.05)

    asyncio.run(run())

    assert bumble_module.active_gatt_connections == {'ipad'}
    assert 'iphone' not in bumble_module.active_gatt_connection_counts


def test_gatt_advertising_restarts_after_all_clients_disconnect(
    bumble_module,
    monkeypatch,
):
    persisted = []
    device = FakeGattDevice()
    monkeypatch.delenv('ROTORSYNC_DISABLE_CONNECTED_ADVERTISING', raising=False)
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_advertising_ready',
        lambda name, address: persisted.append((name, address)),
    )

    async def run():
        installed = bumble_module.install_gatt_advertising_resume_hook(
            device,
            b'adv',
            b'scan',
            'TrailerSync-TR2',
        )
        assert installed is True

        iphone = FakeConnection('iphone')
        device.listeners['connection'](iphone)
        deadline = asyncio.get_running_loop().time() + 1
        while not device.start_calls and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        device.advertising = False
        iphone.listeners['disconnection']()
        deadline = asyncio.get_running_loop().time() + 1
        while len(device.start_calls) < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

    asyncio.run(run())

    assert len(device.start_calls) == 2
    assert bumble_module.active_gatt_connections == set()
    assert bumble_module.connected_advertising_resume_succeeded is False
    assert persisted == [
        ('TrailerSync-TR2', 'AA:BB:CC:DD:EE:FF'),
        ('TrailerSync-TR2', 'AA:BB:CC:DD:EE:FF'),
    ]


def test_gatt_advertising_resume_hook_can_be_disabled_for_single_client_mode(
    bumble_module,
    monkeypatch,
):
    device = FakeGattDevice()
    monkeypatch.setenv('ROTORSYNC_DISABLE_CONNECTED_ADVERTISING', '1')

    installed = bumble_module.install_gatt_advertising_resume_hook(
        device,
        b'adv',
        b'scan',
        'TrailerSync-TR2',
    )

    assert installed is False
    assert device.listeners == {}
    assert device.start_calls == []


def test_gatt_advertising_resume_failure_is_nonfatal(bumble_module):
    class RejectingDevice(FakeGattDevice):
        async def start_advertising(self, **kwargs):
            self.start_calls.append(kwargs)
            raise RuntimeError('command disallowed')

    async def run():
        bumble_module.active_gatt_connections.add('iphone')
        result = await bumble_module.resume_gatt_advertising_after_connection(
            RejectingDevice(),
            b'adv',
            b'scan',
            'TrailerSync-TR2',
            delay=0,
        )
        assert result is False

    asyncio.run(run())


def test_gatt_connection_state_includes_client_activity_details(
    bumble_module,
    monkeypatch,
    tmp_path,
):
    state_path = tmp_path / 'connections.json'
    monkeypatch.setattr(bumble_module, 'GATT_CONNECTION_STATE_FILE', str(state_path))

    bumble_module.active_gatt_connections.update({'ipad', 'iphone'})
    bumble_module.gatt_client_metadata_by_connection.update({
        'ipad': {
            'role': 'pilot',
            'connected_at': 100.0,
            'last_seen': 200.0,
        },
        'iphone': {
            'role': 'ground_crew',
            'connected_at': 110.0,
            'last_seen': 150.0,
        },
    })

    bumble_module.persist_gatt_connection_state('test')

    payload = json.loads(state_path.read_text(encoding='utf-8'))
    assert payload['count'] == 2
    assert payload['clients'] == ['ipad', 'iphone']
    assert payload['client_details'] == [
        {'id': 'ipad', 'role': 'pilot', 'connected_at': 100.0, 'last_seen': 200.0},
        {
            'id': 'iphone',
            'role': 'ground_crew',
            'connected_at': 110.0,
            'last_seen': 150.0,
        },
    ]


def test_inactive_gatt_prune_removes_stale_extra_client(bumble_module, monkeypatch):
    persisted = []
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_connection_state',
        lambda reason='': persisted.append((reason, sorted(bumble_module.active_gatt_connections))),
    )
    bumble_module.active_gatt_connections.update({'fresh', 'stale'})
    bumble_module.active_gatt_connection_counts.update({'fresh': 1, 'stale': 1})
    bumble_module.gatt_client_metadata_by_connection.update({
        'fresh': {'role': 'pilot', 'connected_at': 100.0, 'last_seen': 190.0},
        'stale': {'role': 'ground_crew', 'connected_at': 90.0, 'last_seen': 120.0},
    })

    removed = bumble_module.prune_inactive_gatt_connections(
        now=200.0,
        reason='test_prune',
    )

    assert removed == ['stale']
    assert bumble_module.active_gatt_connections == {'fresh'}
    assert 'stale' not in bumble_module.active_gatt_connection_counts
    assert persisted == [('test_prune', ['fresh'])]


def test_inactive_gatt_prune_keeps_stale_open_connection_object(
    bumble_module,
    monkeypatch,
):
    persisted = []
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_connection_state',
        lambda reason='': persisted.append(reason),
    )
    stale_connection = FakeConnection('iphone')
    stale_connection.connected = True
    bumble_module.active_gatt_connections.update({'ipad', 'iphone'})
    bumble_module.active_gatt_connection_counts.update({'ipad': 1, 'iphone': 1})
    bumble_module.gatt_client_metadata_by_connection.update({
        'ipad': {'role': 'ground_crew', 'connected_at': 100.0, 'last_seen': 190.0},
        'iphone': {
            'role': 'pilot',
            'connected_at': 90.0,
            'last_seen': 100.0,
            'connection': stale_connection,
        },
    })

    removed = bumble_module.prune_inactive_gatt_connections(
        now=200.0,
        reason='test_open_connection',
    )

    assert removed == []
    assert bumble_module.active_gatt_connections == {'ipad', 'iphone'}
    assert persisted == []


def test_inactive_gatt_prune_removes_stale_closed_connection_object(
    bumble_module,
    monkeypatch,
):
    persisted = []
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_connection_state',
        lambda reason='': persisted.append((reason, sorted(bumble_module.active_gatt_connections))),
    )
    stale_connection = FakeConnection('iphone')
    stale_connection.connected = False
    bumble_module.active_gatt_connections.update({'ipad', 'iphone'})
    bumble_module.active_gatt_connection_counts.update({'ipad': 1, 'iphone': 1})
    bumble_module.gatt_client_metadata_by_connection.update({
        'ipad': {'role': 'ground_crew', 'connected_at': 100.0, 'last_seen': 190.0},
        'iphone': {
            'role': 'pilot',
            'connected_at': 90.0,
            'last_seen': 100.0,
            'connection': stale_connection,
        },
    })

    removed = bumble_module.prune_inactive_gatt_connections(
        now=200.0,
        reason='test_closed_connection',
    )

    assert removed == ['iphone']
    assert bumble_module.active_gatt_connections == {'ipad'}
    assert persisted == [('test_closed_connection', ['ipad'])]


def test_inactive_gatt_prune_keeps_one_anchor_when_all_clients_are_stale(
    bumble_module,
    monkeypatch,
):
    persisted = []
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_connection_state',
        lambda reason='': persisted.append((reason, sorted(bumble_module.active_gatt_connections))),
    )
    bumble_module.active_gatt_connections.update({'ipad', 'iphone'})
    bumble_module.active_gatt_connection_counts.update({'ipad': 1, 'iphone': 1})
    bumble_module.gatt_client_metadata_by_connection.update({
        'ipad': {'role': 'ground_crew', 'connected_at': 90.0, 'last_seen': 100.0},
        'iphone': {'role': 'pilot', 'connected_at': 95.0, 'last_seen': 110.0},
    })

    removed = bumble_module.prune_inactive_gatt_connections(
        now=200.0,
        reason='test_prune_all_stale',
    )

    assert removed == ['ipad']
    assert bumble_module.active_gatt_connections == {'iphone'}
    assert 'ipad' not in bumble_module.active_gatt_connection_counts
    assert 'ipad' not in bumble_module.gatt_client_metadata_by_connection
    assert persisted == [('test_prune_all_stale', ['iphone'])]


def test_inactive_gatt_prune_keeps_last_client(bumble_module, monkeypatch):
    persisted = []
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_connection_state',
        lambda reason='': persisted.append(reason),
    )
    bumble_module.active_gatt_connections.add('stale')
    bumble_module.active_gatt_connection_counts['stale'] = 1
    bumble_module.gatt_client_metadata_by_connection['stale'] = {
        'role': 'pilot',
        'connected_at': 100.0,
        'last_seen': 100.0,
    }

    removed = bumble_module.prune_inactive_gatt_connections(now=200.0)

    assert removed == []
    assert bumble_module.active_gatt_connections == {'stale'}
    assert persisted == []


def test_reconcile_gatt_bookkeeping_restarts_anchor_advertising_after_stale_extra_prune(
    bumble_module,
    monkeypatch,
):
    scheduled = []
    device = FakeGattDevice()
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_connection_state',
        lambda reason='': None,
    )
    monkeypatch.setattr(
        bumble_module,
        'schedule_single_client_gatt_advertising_maintenance',
        lambda *_args, **kwargs: scheduled.append(kwargs.get('reason')),
    )
    bumble_module.active_gatt_connections.update({'ipad', 'iphone'})
    bumble_module.active_gatt_connection_counts.update({'ipad': 1, 'iphone': 1})
    bumble_module.gatt_client_metadata_by_connection.update({
        'ipad': {'role': 'ground_crew', 'connected_at': 100.0, 'last_seen': 190.0},
        'iphone': {'role': 'pilot', 'connected_at': 120.0, 'last_seen': 150.0},
    })

    removed = bumble_module.reconcile_gatt_connection_bookkeeping(
        device,
        b'adv',
        b'scan',
        'TrailerSync-TR6',
        now=200.0,
        reason='test_reconcile',
    )

    assert removed == ['iphone']
    assert bumble_module.active_gatt_connections == {'ipad'}
    assert scheduled == ['stale controller pruned; anchor remains']


def test_unknown_gatt_client_without_hello_is_disconnected(bumble_module, monkeypatch):
    persisted = []
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_connection_state',
        lambda reason='': persisted.append((reason, sorted(bumble_module.active_gatt_connections))),
    )
    conn = FakeConnection('unknown')
    bumble_module.active_gatt_connections.add('unknown')
    bumble_module.active_gatt_connection_counts['unknown'] = 1
    bumble_module.gatt_client_metadata_by_connection['unknown'] = {
        'role': 'unknown',
        'connected_at': 100.0,
        'last_seen': 110.0,
    }

    dropped = asyncio.run(
        bumble_module.disconnect_unknown_gatt_client_if_no_hello(
            conn,
            'unknown',
            100.0,
            grace_seconds=0,
        )
    )

    assert dropped is True
    assert conn.disconnect_calls == 1
    assert 'unknown' not in bumble_module.active_gatt_connections
    assert 'unknown' not in bumble_module.active_gatt_connection_counts
    assert 'unknown' not in bumble_module.gatt_client_metadata_by_connection
    assert persisted == [('unknown_client_no_hello', [])]


def test_unknown_gatt_client_deadline_keeps_identified_client(bumble_module, monkeypatch):
    persisted = []
    monkeypatch.setattr(
        bumble_module,
        'persist_gatt_connection_state',
        lambda reason='': persisted.append(reason),
    )
    conn = FakeConnection('iphone')
    bumble_module.active_gatt_connections.add('iphone')
    bumble_module.active_gatt_connection_counts['iphone'] = 1
    bumble_module.gatt_client_metadata_by_connection['iphone'] = {
        'role': 'pilot',
        'connected_at': 100.0,
        'last_seen': 110.0,
    }

    dropped = asyncio.run(
        bumble_module.disconnect_unknown_gatt_client_if_no_hello(
            conn,
            'iphone',
            100.0,
            grace_seconds=0,
        )
    )

    assert dropped is False
    assert conn.disconnect_calls == 0
    assert bumble_module.active_gatt_connections == {'iphone'}
    assert persisted == []


def test_gatt_client_seen_recovers_pruned_active_client(bumble_module, monkeypatch, tmp_path):
    bumble_module = importlib.reload(bumble_module)
    heartbeat_path = tmp_path / 'seen'
    state_path = tmp_path / 'connections.json'
    monkeypatch.setattr(bumble_module, 'GATT_CLIENT_SEEN_FILE', str(heartbeat_path))
    monkeypatch.setattr(bumble_module, 'GATT_CONNECTION_STATE_FILE', str(state_path))
    monkeypatch.setattr(bumble_module.time, 'time', lambda: 200.0)

    bumble_module.mark_gatt_client_seen(FakeConnection('iphone'))

    assert bumble_module.active_gatt_connections == {'iphone'}
    assert bumble_module.active_gatt_connection_counts['iphone'] == 1
    payload = json.loads(state_path.read_text(encoding='utf-8'))
    assert payload['clients'] == ['iphone']


def test_sensor_defer_reason_waits_after_gatt_controller_change(bumble_module):
    bumble_module.active_gatt_connections.add('iphone')
    bumble_module.gatt_controller_changed_at = 100.0

    assert bumble_module.gatt_sensor_defer_reason('mopeka', now=105.0) == (
        'GATT controller settling (7s)'
    )
    assert bumble_module.gatt_sensor_defer_reason('mopeka', now=113.0) == ''


def test_sensor_defer_reason_skips_bms_during_multipoint(bumble_module):
    bumble_module.active_gatt_connections.update({'ipad', 'iphone'})
    bumble_module.gatt_controller_changed_at = 100.0

    assert bumble_module.gatt_sensor_defer_reason('mopeka', now=120.0) == ''
    assert bumble_module.gatt_sensor_defer_reason('bms', now=120.0) == (
        'multipoint GATT active'
    )


def test_short_ble_advertising_name_uses_trailer_alias(bumble_module):
    assert bumble_module._compute_short_ble_advertising_name('TrailerSync-TR6') == 'TR6'
    assert bumble_module._compute_short_ble_advertising_name('TrailerSync-TR12') == 'TR12'
    assert bumble_module._compute_short_ble_advertising_name('TrailerSync-Customer') == ''


def test_self_scan_heartbeat_records_own_gatt_advertisement(
    bumble_module,
    monkeypatch,
    tmp_path,
):
    class FakeAdvertisementData:
        def __init__(self, values):
            self.values = values

        def get_all(self, ad_type):
            return self.values.get(ad_type, [])

    class FakeAdvertisement:
        def __init__(self, address, values=None):
            self.address = address
            self.data = FakeAdvertisementData(values or {})
            self.rssi = -42

    monkeypatch.setattr(bumble_module.AdvertisingData, 'COMPLETE_LOCAL_NAME', 9, raising=False)
    monkeypatch.setattr(bumble_module.AdvertisingData, 'SHORTENED_LOCAL_NAME', 8, raising=False)
    monkeypatch.setattr(
        bumble_module.AdvertisingData,
        'INCOMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS',
        6,
        raising=False,
    )
    monkeypatch.setattr(
        bumble_module.AdvertisingData,
        'COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS',
        7,
        raising=False,
    )
    heartbeat_path = tmp_path / 'self_adv.json'
    ready_path = tmp_path / 'ready.json'
    monkeypatch.setattr(bumble_module, 'GATT_SELF_ADV_SEEN_FILE', str(heartbeat_path))
    monkeypatch.setattr(bumble_module, 'GATT_ADVERTISING_READY_FILE', str(ready_path))
    monkeypatch.setattr(bumble_module, 'last_gatt_self_adv_seen_write', 0.0)

    bumble_module.persist_gatt_advertising_ready('TrailerSync-TR6', 'E8:EA:6A:BD:E5:0E/P')

    assert bumble_module.maybe_mark_gatt_self_advertisement_seen(
        FakeAdvertisement('00:11:22:33:44:55'),
        now=1000,
    ) is False
    assert not heartbeat_path.exists()

    assert bumble_module.maybe_mark_gatt_self_advertisement_seen(
        FakeAdvertisement(
            'E8:EA:6A:BD:E5:0E',
            values={9: [b'TrailerSync-TR6']},
        ),
        now=1001,
    ) is True

    payload = json.loads(heartbeat_path.read_text(encoding='utf-8'))
    assert payload['address'] == 'E8:EA:6A:BD:E5:0E'
    assert payload['target_address'] == 'E8:EA:6A:BD:E5:0E'
    assert payload['address_match'] is True
    assert payload['name_match'] is True

    heartbeat_path.unlink()
    bumble_module.last_gatt_self_adv_seen_write = 0.0
    assert bumble_module.maybe_mark_gatt_self_advertisement_seen(
        FakeAdvertisement(
            'DC:7A:B1:5E:23:3D',
            values={8: [b'TR6']},
        ),
        now=1017,
    ) is True

    payload = json.loads(heartbeat_path.read_text(encoding='utf-8'))
    assert payload['address_match'] is False
    assert payload['name_match'] is True
    assert payload['name'] == 'TR6'
    assert payload['target_short_name'] == 'TR6'

    heartbeat_path.unlink()
    bumble_module.last_gatt_self_adv_seen_write = 0.0
    assert bumble_module.maybe_mark_gatt_self_advertisement_seen(
        FakeAdvertisement(
            'DC:7A:B1:5E:23:3D',
            values={6: [bytes(bumble_module.SERVICE_UUID)]},
        ),
        now=1033,
    ) is True

    payload = json.loads(heartbeat_path.read_text(encoding='utf-8'))
    assert payload['address_match'] is False
    assert payload['name_match'] is False
    assert payload['service_uuid_match'] is True


def test_self_scan_uses_active_scan_only_when_idle_and_stale(bumble_module):
    bumble_module.last_gatt_self_adv_seen_write = 100.0
    bumble_module.active_gatt_connections.clear()

    assert bumble_module._should_use_active_self_adv_scan(129.0) is False
    assert bumble_module._should_use_active_self_adv_scan(131.0) is True

    bumble_module.active_gatt_connections.add('iphone')

    assert bumble_module._should_use_active_self_adv_scan(131.0) is False
    assert bumble_module._should_use_active_self_adv_scan(1000.0) is False
