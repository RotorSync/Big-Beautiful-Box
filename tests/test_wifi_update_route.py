"""A signed update stream over the WiFi maintenance channel must verify, route
to the box-update receiver, reassemble, and apply — the field-update path over
the trailer AP (iPad supplies the tarball; box has no internet)."""
import asyncio
import base64
import functools
import hashlib
import io
import tarfile
import time

import pytest

from rotorlink import maintenance_handler as mh
from rotorlink.maintenance_handler import (
    MAINTENANCE_DEVELOPMENT_SECRET,
    MaintenanceHandler,
    _maintenance_frame_signature_with_secret,
)
from src.box_update import BoxUpdateReceiver


def _sign(**frame):
    frame.setdefault("seq", 1)
    frame.setdefault("expires_at", time.time() + 60)
    frame["sig"] = _maintenance_frame_signature_with_secret(
        frame, MAINTENANCE_DEVELOPMENT_SECRET
    )
    return frame


def _bbb_tarball():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (
            ("root/dashboard.py", b"print('new')\n"),
            ("root/rotorsync_bumble.py", b"# b\n"),
            ("root/src/__init__.py", b""),
            ("root/VERSION", b"V9.99\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_signed_update_stream_applies_over_wifi(tmp_path, monkeypatch):
    # Point the receiver the handler creates at temp dirs, and stub the system
    # calls (systemctl/systemd-run/compile) so the test never touches the host.
    factory = functools.partial(
        BoxUpdateReceiver,
        repo_dir=str(tmp_path / "repo"),
        update_dir=str(tmp_path / "upd"),
        tmp_dir=str(tmp_path / "tmp"),
        refresh_opt=False,
    )
    monkeypatch.setattr(mh, "BoxUpdateReceiver", factory)

    calls = []

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr("src.box_update.subprocess.run",
                        lambda a, **k: (calls.append(list(a)), _R())[1])
    monkeypatch.setattr("src.box_update.subprocess.Popen",
                        lambda a, **k: (calls.append(list(a)), _R())[1])

    # Seed an existing repo so apply overwrites it.
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "dashboard.py").write_text("# old\n")
    (repo / "rotorsync_bumble.py").write_text("# old\n")
    (repo / "VERSION").write_text("V1.00\n")

    tarball = _bbb_tarball()
    sha = hashlib.sha256(tarball).hexdigest()
    emitted = []

    async def emit(frame):
        emitted.append(frame)

    async def run():
        h = MaintenanceHandler(emit, asyncio.get_running_loop())
        await h.handle_control(_sign(type="update_begin", update_id="u1",
                                     size=len(tarball), sha256=sha))
        for off in range(0, len(tarball), 4096):
            piece = tarball[off:off + 4096]
            await h.handle_control(_sign(type="update_chunk", update_id="u1",
                                         offset=off,
                                         data_b64=base64.b64encode(piece).decode()))
        await h.handle_control(_sign(type="update_finalize", update_id="u1"))
        await h.handle_control(_sign(type="update_apply", update_id="u1"))

    asyncio.run(run())

    # Applied: new code in the repo, restart scheduled including rotorlink.
    assert (repo / "VERSION").read_text().strip() == "V9.99"
    types = [f.get("type") for f in emitted]
    assert "update_verified" in types
    assert "update_applied" in types
    assert any("systemd-run" in c[0] for c in calls)
    restart_cmd = " ".join(c for call in calls if "systemd-run" in call[0] for c in call)
    assert "rotorlink.service" in restart_cmd


def test_unsigned_update_frame_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(mh, "BoxUpdateReceiver",
                        functools.partial(BoxUpdateReceiver,
                                          repo_dir=str(tmp_path / "r"),
                                          update_dir=str(tmp_path / "u"),
                                          tmp_dir=str(tmp_path / "t")))
    emitted = []

    async def emit(frame):
        emitted.append(frame)

    async def run():
        h = MaintenanceHandler(emit, asyncio.get_running_loop())
        # No signature → must be rejected before any staging.
        await h.handle_control({"type": "update_begin", "update_id": "x",
                                "size": 10, "sha256": "a" * 64,
                                "seq": 1, "expires_at": time.time() + 60})

    asyncio.run(run())
    assert any(f.get("type") == "error" for f in emitted)
    assert not (tmp_path / "u" / "x").exists()   # nothing staged
