import importlib
import sys
import time
import types

import pytest


def install_bumble_stubs(monkeypatch):
    bumble = types.ModuleType('bumble')
    hci = types.ModuleType('bumble.hci')
    device = types.ModuleType('bumble.device')
    host = types.ModuleType('bumble.host')
    transport = types.ModuleType('bumble.transport')
    hci_socket = types.ModuleType('bumble.transport.hci_socket')
    gatt = types.ModuleType('bumble.gatt')
    core = types.ModuleType('bumble.core')

    class Dummy:
        def __init__(self, *args, **kwargs):
            pass

        def __bytes__(self):
            return b''

    device.Device = Dummy
    device.Peer = Dummy
    host.Host = Dummy
    hci_socket.open_hci_socket_transport = lambda *args, **kwargs: None
    gatt.Service = Dummy
    gatt.Characteristic = Dummy
    gatt.CharacteristicValue = Dummy
    core.UUID = Dummy
    core.AdvertisingData = Dummy

    monkeypatch.setitem(sys.modules, 'bumble', bumble)
    monkeypatch.setitem(sys.modules, 'bumble.hci', hci)
    monkeypatch.setitem(sys.modules, 'bumble.device', device)
    monkeypatch.setitem(sys.modules, 'bumble.host', host)
    monkeypatch.setitem(sys.modules, 'bumble.transport', transport)
    monkeypatch.setitem(sys.modules, 'bumble.transport.hci_socket', hci_socket)
    monkeypatch.setitem(sys.modules, 'bumble.gatt', gatt)
    monkeypatch.setitem(sys.modules, 'bumble.core', core)


@pytest.fixture
def bumble_module(monkeypatch):
    monkeypatch.setenv('BBB_MAINTENANCE_SECRET', 'unit-test-secret')
    install_bumble_stubs(monkeypatch)
    sys.modules.pop('rotorsync_bumble', None)
    module = importlib.import_module('rotorsync_bumble')
    yield module
    sys.modules.pop('rotorsync_bumble', None)


def signed_frame(module, **overrides):
    frame = {
        'type': 'heartbeat',
        'session_id': 'session-1',
        'seq': 123,
        'expires_at': time.time() + 60,
        **overrides,
    }
    frame['sig'] = module._maintenance_frame_signature(frame)
    return frame


def test_accepts_valid_signed_maintenance_frame(bumble_module):
    frame = signed_frame(bumble_module)

    bumble_module._verify_maintenance_frame(frame)


def test_signature_matches_backend_canonical_format(bumble_module):
    frame = {
        'type': 'heartbeat',
        'session_id': 'session-1',
        'seq': 123,
        'expires_at': 2000000000,
    }

    assert (
        bumble_module._maintenance_frame_signature(frame)
        == 'adoE-tDnQwuX1a6JoXNSvsYzzt6dLhclmPYxhco3HAQ'
    )


def test_rejects_missing_maintenance_signature(bumble_module):
    frame = signed_frame(bumble_module)
    frame.pop('sig')

    with pytest.raises(ValueError, match='missing frame signature'):
        bumble_module._verify_maintenance_frame(frame)


def test_rejects_tampered_maintenance_frame(bumble_module):
    frame = signed_frame(bumble_module)
    frame['session_id'] = 'other-session'

    with pytest.raises(ValueError, match='invalid frame signature'):
        bumble_module._verify_maintenance_frame(frame)


def test_rejects_expired_maintenance_frame(bumble_module):
    frame = signed_frame(bumble_module, expires_at=time.time() - 1)

    with pytest.raises(ValueError, match='expired maintenance frame'):
        bumble_module._verify_maintenance_frame(frame)
