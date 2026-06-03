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
