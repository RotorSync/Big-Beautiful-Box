import importlib
import asyncio
import base64
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


def test_maintenance_secret_reports_env_source(bumble_module):
    source, secret = bumble_module._maintenance_secret_source()

    assert source == 'env:BBB_MAINTENANCE_SECRET'
    assert secret == b'unit-test-secret'


def test_maintenance_secret_reports_development_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv('BBB_MAINTENANCE_SECRET', raising=False)
    monkeypatch.delenv('MAINTENANCE_RELAY_SECRET', raising=False)
    install_bumble_stubs(monkeypatch)
    sys.modules.pop('rotorsync_bumble', None)
    module = importlib.import_module('rotorsync_bumble')
    monkeypatch.setattr(
        module,
        'MAINTENANCE_SECRET_PATHS',
        (str(tmp_path / 'missing.secret'),),
    )

    source, secret = module._maintenance_secret_source()

    assert source == 'development-default'
    assert secret == module.MAINTENANCE_DEVELOPMENT_SECRET
    sys.modules.pop('rotorsync_bumble', None)


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


def test_bootstrap_secret_frame_provisions_unconfigured_trailer(monkeypatch, tmp_path):
    monkeypatch.delenv('BBB_MAINTENANCE_SECRET', raising=False)
    monkeypatch.delenv('MAINTENANCE_RELAY_SECRET', raising=False)
    install_bumble_stubs(monkeypatch)
    sys.modules.pop('rotorsync_bumble', None)
    module = importlib.import_module('rotorsync_bumble')
    secret_path = tmp_path / '.rotorsync-maintenance-secret'
    monkeypatch.setattr(module, 'MAINTENANCE_SECRET_PATHS', (str(secret_path),))
    monkeypatch.setattr(module, 'MAINTENANCE_USER_SECRET_PATH', str(secret_path))

    new_secret = b'x' * 64
    frame = {
        'type': 'update_begin',
        'session_id': 'session-1',
        'update_id': 'bbb-master-V2.17-abcdef12',
        'size': 100,
        'sha256': 'a' * 64,
        'maintenance_secret_b64': base64.b64encode(new_secret).decode('ascii'),
        'expires_at': time.time() + 60,
    }
    frame['sig'] = module._maintenance_frame_signature_with_secret(frame, new_secret)

    module._verify_maintenance_frame(frame)

    assert secret_path.read_bytes().strip() == new_secret
    assert oct(secret_path.stat().st_mode & 0o777) == '0o600'
    sys.modules.pop('rotorsync_bumble', None)


def test_bootstrap_secret_does_not_replace_existing_secret(bumble_module, monkeypatch, tmp_path):
    existing_secret = b'e' * 64
    replacement_secret = b'r' * 64
    secret_path = tmp_path / '.rotorsync-maintenance-secret'
    secret_path.write_bytes(existing_secret + b'\n')
    monkeypatch.delenv('BBB_MAINTENANCE_SECRET', raising=False)
    monkeypatch.delenv('MAINTENANCE_RELAY_SECRET', raising=False)
    monkeypatch.setattr(bumble_module, 'MAINTENANCE_SECRET_PATHS', (str(secret_path),))
    monkeypatch.setattr(bumble_module, 'MAINTENANCE_USER_SECRET_PATH', str(secret_path))

    frame = {
        'type': 'update_begin',
        'session_id': 'session-1',
        'update_id': 'bbb-master-V2.17-abcdef12',
        'size': 100,
        'sha256': 'a' * 64,
        'maintenance_secret_b64': base64.b64encode(replacement_secret).decode('ascii'),
        'expires_at': time.time() + 60,
    }
    frame['sig'] = bumble_module._maintenance_frame_signature_with_secret(frame, replacement_secret)

    with pytest.raises(ValueError, match='invalid frame signature'):
        bumble_module._verify_maintenance_frame(frame)

    assert secret_path.read_bytes().strip() == existing_secret


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


def test_restore_repo_runtime_ownership_chowns_runtime_paths(bumble_module, monkeypatch, tmp_path):
    repo = tmp_path / 'repo'
    write_runtime_tree(repo, 'current')
    (repo / 'deploy').mkdir()
    (repo / 'deploy' / 'bbb-logrotate.conf').write_text('rotate\n', encoding='utf-8')

    calls = []
    monkeypatch.setattr(bumble_module.os, 'chown', lambda path, uid, gid: calls.append((str(path), uid, gid)))

    bumble_module._restore_repo_runtime_ownership(repo)

    uid, gid = repo.stat().st_uid, repo.stat().st_gid
    assert (str(repo / 'dashboard.py'), uid, gid) in calls
    assert (str(repo / 'rotorsync_bumble.py'), uid, gid) in calls
    assert (str(repo / 'src'), uid, gid) in calls
    assert (str(repo / 'src' / 'marker.py'), uid, gid) in calls
    assert (str(repo / 'deploy' / 'bbb-logrotate.conf'), uid, gid) in calls


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


def test_apply_rejects_stale_bbb_master_update(bumble_module, monkeypatch, tmp_path):
    updates = tmp_path / 'updates'
    monkeypatch.setattr(bumble_module, 'MAINTENANCE_UPDATE_DIR', str(updates))
    older_id = 'bbb-master-V2.5-11111111'
    newer_id = 'bbb-master-V2.5-22222222'

    for update_id, verified_at in ((older_id, 10), (newer_id, 20)):
        paths = bumble_module._update_paths(update_id)
        paths['base'].mkdir(parents=True)
        paths['artifact'].write_bytes(b'placeholder')
        bumble_module._write_update_meta(update_id, {
            'update_id': update_id,
            'status': 'verified',
            'expected_size': 11,
            'sha256': 'a' * 64,
            'verified_at': verified_at,
        })

    monkeypatch.setattr(bumble_module.tarfile, 'is_tarfile', lambda _path: True)
    monkeypatch.setattr(
        bumble_module,
        '_apply_tar_update',
        lambda _update_id, _artifact_path: pytest.fail('stale update should not apply'),
    )

    with pytest.raises(ValueError, match='stale update'):
        bumble_module._handle_update_apply({'update_id': older_id})

    assert bumble_module._read_update_meta(older_id)['status'] == 'verified'


def test_apply_allows_latest_verified_bbb_master_update(bumble_module, monkeypatch, tmp_path):
    updates = tmp_path / 'updates'
    monkeypatch.setattr(bumble_module, 'MAINTENANCE_UPDATE_DIR', str(updates))
    older_id = 'bbb-master-V2.5-11111111'
    newer_id = 'bbb-master-V2.5-22222222'

    for update_id, verified_at in ((older_id, 10), (newer_id, 20)):
        paths = bumble_module._update_paths(update_id)
        paths['base'].mkdir(parents=True)
        paths['artifact'].write_bytes(b'placeholder')
        bumble_module._write_update_meta(update_id, {
            'update_id': update_id,
            'status': 'verified',
            'expected_size': 11,
            'sha256': 'a' * 64,
            'verified_at': verified_at,
        })

    applied = []
    monkeypatch.setattr(bumble_module.tarfile, 'is_tarfile', lambda _path: True)
    monkeypatch.setattr(
        bumble_module,
        '_apply_tar_update',
        lambda update_id, _artifact_path: applied.append(update_id),
    )
    monkeypatch.setattr(bumble_module, '_schedule_service_restart', lambda: None)

    bumble_module._handle_update_apply({'update_id': newer_id})

    assert applied == [newer_id]
    assert bumble_module._read_update_meta(newer_id)['status'] == 'applied'


def test_service_restart_uses_transient_unit_and_restarts_rotorsync_last(
    bumble_module,
    monkeypatch,
):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(('run', cmd, kwargs))
        return types.SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(bumble_module.subprocess, 'run', fake_run)

    bumble_module._schedule_service_restart()

    assert calls
    kind, cmd, kwargs = calls[0]
    assert kind == 'run'
    assert cmd[:2] == ['systemd-run', '--unit=bbb-post-update-restart']
    restart_cmd = cmd[-1]
    assert 'systemctl restart iol_dashboard.service rotorsync_watchdog.service' in restart_cmd
    assert restart_cmd.index('iol_dashboard.service') < restart_cmd.index('rotorsync.service')
    assert kwargs['check'] is True


def test_service_restart_fallback_still_restarts_rotorsync_last(
    bumble_module,
    monkeypatch,
):
    popen_calls = []

    def fail_run(*_args, **_kwargs):
        raise RuntimeError('systemd-run unavailable')

    def fake_popen(cmd):
        popen_calls.append(cmd)

    monkeypatch.setattr(bumble_module.subprocess, 'run', fail_run)
    monkeypatch.setattr(bumble_module.subprocess, 'Popen', fake_popen)

    bumble_module._schedule_service_restart()

    assert popen_calls
    restart_cmd = popen_calls[0][-1]
    assert 'systemctl restart iol_dashboard.service rotorsync_watchdog.service' in restart_cmd
    assert restart_cmd.index('iol_dashboard.service') < restart_cmd.index('rotorsync.service')


class CapturingBleDevice:
    def __init__(self):
        self.notifications = []

    async def notify_subscribers(self, characteristic, data):
        self.notifications.append((characteristic, data.decode('utf-8')))


def test_maintenance_stdout_notifications_capture_each_payload(bumble_module):
    async def run_test():
        device = CapturingBleDevice()
        bumble_module.ble_device = device
        bumble_module.maintenance_stdout_char = object()
        bumble_module.maintenance_stdout_seq = 0
        bumble_module.maintenance_active_session_id = 'session-1'
        bumble_module.maintenance_stdout_notify_queue = []
        bumble_module.maintenance_stdout_notify_task = None
        bumble_module.MAINTENANCE_STDOUT_NOTIFY_INTERVAL = 0.001

        bumble_module._set_maintenance_stdout_obj({'text': 'first'})
        bumble_module._set_maintenance_stdout_obj({'text': 'second'})
        deadline = time.time() + 1
        while len(device.notifications) < 2 and time.time() < deadline:
            await asyncio.sleep(0.01)

        payloads = [
            json.loads(data)
            for _characteristic, data in device.notifications
        ]
        assert [payload['text'] for payload in payloads] == ['first', 'second']
        assert [payload['seq'] for payload in payloads] == [1, 2]

    asyncio.run(run_test())
