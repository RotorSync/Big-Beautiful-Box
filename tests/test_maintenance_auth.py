import importlib
import json
import sys
import tarfile
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


def write_runtime_tree(root, marker):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'dashboard.py').write_text(f'print("{marker} dashboard")\n', encoding='utf-8')
    (root / 'rotorsync_bumble.py').write_text(f'print("{marker} bumble")\n', encoding='utf-8')
    (root / 'VERSION').write_text(f'{marker}\n', encoding='utf-8')
    (root / 'src').mkdir(exist_ok=True)
    (root / 'src' / '__init__.py').write_text('', encoding='utf-8')
    (root / 'src' / 'marker.py').write_text(f'MARKER = "{marker}"\n', encoding='utf-8')


def write_update_tar(path, marker='new'):
    build_root = path.parent / 'bundle-root'
    write_runtime_tree(build_root, marker)
    (build_root / 'install.sh').write_text('#!/bin/sh\n', encoding='utf-8')
    with tarfile.open(path, 'w:gz') as archive:
        for item in build_root.rglob('*'):
            archive.add(item, arcname=f'Big-Beautiful-Box-test/{item.relative_to(build_root)}')


def test_apply_tar_update_rolls_back_runtime_after_copy_failure(bumble_module, monkeypatch, tmp_path):
    repo = tmp_path / 'repo'
    updates = tmp_path / 'updates'
    scratch = tmp_path / 'scratch'
    artifact = tmp_path / 'update.tar.gz'
    write_runtime_tree(repo, 'old')
    write_update_tar(artifact, 'new')

    monkeypatch.setattr(bumble_module, 'MAINTENANCE_REPO_DIR', str(repo))
    monkeypatch.setattr(bumble_module, 'MAINTENANCE_UPDATE_DIR', str(updates))
    monkeypatch.setattr(bumble_module, 'MAINTENANCE_TMP_DIR', str(scratch))

    def fail_refresh(_repo):
        raise RuntimeError('opt copy failed')

    monkeypatch.setattr(bumble_module, '_refresh_opt_runtime', fail_refresh)

    with pytest.raises(RuntimeError, match='restored previous runtime'):
        bumble_module._apply_tar_update('update-1', artifact)

    assert (repo / 'dashboard.py').read_text(encoding='utf-8') == 'print("old dashboard")\n'
    assert (repo / 'rotorsync_bumble.py').read_text(encoding='utf-8') == 'print("old bumble")\n'
    assert (repo / 'src' / 'marker.py').read_text(encoding='utf-8') == 'MARKER = "old"\n'
    assert not (repo / 'install.sh').exists()


def test_apply_failure_marks_update_failed_and_reports_status(bumble_module, monkeypatch, tmp_path):
    updates = tmp_path / 'updates'
    monkeypatch.setattr(bumble_module, 'MAINTENANCE_UPDATE_DIR', str(updates))
    paths = bumble_module._update_paths('update-1')
    paths['base'].mkdir(parents=True)
    paths['artifact'].write_bytes(b'placeholder')
    bumble_module._write_update_meta('update-1', {
        'update_id': 'update-1',
        'status': 'verified',
        'expected_size': 11,
        'sha256': 'a' * 64,
    })
    monkeypatch.setattr(bumble_module.tarfile, 'is_tarfile', lambda _path: True)
    monkeypatch.setattr(
        bumble_module,
        '_apply_tar_update',
        lambda _update_id, _artifact_path: (_ for _ in ()).throw(RuntimeError('copy failed')),
    )

    with pytest.raises(RuntimeError, match='copy failed'):
        bumble_module._handle_update_apply({'update_id': 'update-1'})

    meta = bumble_module._read_update_meta('update-1')
    assert meta['status'] == 'apply_failed'
    assert meta['apply_error'] == 'copy failed'
    payload = json.loads(bumble_module.maintenance_last_stdout_payload)
    assert payload['type'] == 'update_apply_failed'
    assert payload['update_id'] == 'update-1'
