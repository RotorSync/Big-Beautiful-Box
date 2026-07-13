"""The release bundle must carry every runtime both maintenance engines apply."""

import importlib
import sys
import tarfile
import types

import pytest

from scripts import build_update_bundle as builder
from src import box_update
from src.box_update import BoxUpdateReceiver
from tests.test_maintenance_auth import install_bumble_stubs


def _write_bundle_source(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "dashboard.py").write_text("print('dashboard')\n", encoding="utf-8")
    (root / "rotorsync_bumble.py").write_text("print('bumble')\n", encoding="utf-8")
    (root / "VERSION").write_text("V9.99\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "rotorlink").mkdir()
    (root / "rotorlink" / "__init__.py").write_text("", encoding="utf-8")
    (root / "rotorlink" / "server.py").write_text(
        "BUNDLE_MARKER = 'rotorlink'\n",
        encoding="utf-8",
    )


def _build_bundle(tmp_path, monkeypatch):
    source = tmp_path / "source"
    output = tmp_path / "bbb-update.tar.gz"
    _write_bundle_source(source)
    monkeypatch.setattr(builder, "repository_root", lambda: source)
    monkeypatch.setattr(
        builder,
        "parse_args",
        lambda: types.SimpleNamespace(output=output, allow_dirty=False),
    )

    assert builder.main() == 0
    return output


@pytest.fixture
def bumble_module(monkeypatch):
    monkeypatch.setenv("BBB_MAINTENANCE_SECRET", "unit-test-secret")
    install_bumble_stubs(monkeypatch)
    sys.modules.pop("rotorsync_bumble", None)
    module = importlib.import_module("rotorsync_bumble")
    yield module
    sys.modules.pop("rotorsync_bumble", None)


def test_builder_requires_rotorlink_runtime(tmp_path):
    (tmp_path / "dashboard.py").write_text("", encoding="utf-8")
    (tmp_path / "rotorsync_bumble.py").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()

    with pytest.raises(SystemExit, match="rotorlink"):
        builder.collect_files(tmp_path)


def test_built_bundle_contains_rotorlink_and_both_engines_accept_it(
    tmp_path,
    monkeypatch,
    bumble_module,
):
    bundle = _build_bundle(tmp_path, monkeypatch)

    with tarfile.open(bundle) as archive:
        names = archive.getnames()
    assert any(name.endswith("/rotorlink/__init__.py") for name in names)
    assert any(name.endswith("/rotorlink/server.py") for name in names)

    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        return Result()

    monkeypatch.setattr(box_update.subprocess, "run", fake_run)

    wifi_repo = tmp_path / "wifi-installed"
    wifi_repo.mkdir()
    receiver = BoxUpdateReceiver(
        emit_ack=lambda _frame: None,
        emit_status=lambda _frame: None,
        repo_dir=str(wifi_repo),
        update_dir=str(tmp_path / "wifi-updates"),
        tmp_dir=str(tmp_path / "wifi-scratch"),
        refresh_opt=False,
    )
    receiver._validate_archive(bundle)
    monkeypatch.setattr(receiver, "_restore_repo_runtime_ownership", lambda _repo: None)
    receiver._apply_tar("wifi", bundle)
    assert (
        wifi_repo / "rotorlink" / "server.py"
    ).read_text(encoding="utf-8") == "BUNDLE_MARKER = 'rotorlink'\n"

    ble_repo = tmp_path / "ble-installed"
    ble_repo.mkdir()
    monkeypatch.setattr(bumble_module, "MAINTENANCE_REPO_DIR", str(ble_repo))
    monkeypatch.setattr(
        bumble_module,
        "MAINTENANCE_UPDATE_DIR",
        str(tmp_path / "ble-updates"),
    )
    monkeypatch.setattr(
        bumble_module,
        "MAINTENANCE_TMP_DIR",
        str(tmp_path / "ble-scratch"),
    )
    monkeypatch.setattr(bumble_module, "_refresh_opt_runtime", lambda _repo: None)
    monkeypatch.setattr(
        bumble_module,
        "_restore_repo_runtime_ownership",
        lambda _repo: None,
    )

    bumble_module._apply_tar_update("ble", bundle)

    assert (
        ble_repo / "rotorlink" / "server.py"
    ).read_text(encoding="utf-8") == "BUNDLE_MARKER = 'rotorlink'\n"
    compile_calls = [call for call in calls if "compileall" in call]
    assert len(compile_calls) == 2
    assert all(any(part.endswith("/rotorlink") for part in call) for call in compile_calls)
