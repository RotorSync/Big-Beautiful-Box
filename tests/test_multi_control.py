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
    monkeypatch.setattr(module, 'mark_gatt_client_seen', lambda: None)
    yield module
    if module.control_command_worker_task:
        module.control_command_worker_task.cancel()
    sys.modules.pop('rotorsync_bumble', None)


def connection(peer):
    return types.SimpleNamespace(peer_address=peer)


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
        'cc': '-0.10',
    }
    assert len(json.dumps(payload, separators=(',', ':'))) < 160


def test_gatt_advertising_resume_hook_restarts_on_connection(bumble_module, monkeypatch):
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
        device.listeners['connection'](connection('iphone'))
        deadline = asyncio.get_running_loop().time() + 1
        while not device.start_calls and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        device.listeners['connection'](connection('ipad'))
        deadline = asyncio.get_running_loop().time() + 1
        while not device.stop_calls and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

    asyncio.run(run())

    assert device.start_calls == [
        {
            'advertising_data': b'adv',
            'scan_response_data': b'scan',
            'auto_restart': True,
        }
    ]
    assert device.stop_calls == [{}]
    assert persisted == [('TrailerSync-TR2', 'AA:BB:CC:DD:EE:FF')]


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
        result = await bumble_module.resume_gatt_advertising_after_connection(
            RejectingDevice(),
            b'adv',
            b'scan',
            'TrailerSync-TR2',
            delay=0,
        )
        assert result is False

    asyncio.run(run())
