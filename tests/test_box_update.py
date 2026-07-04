"""Streamed offline box update: the iPad sends a BBB repo tarball in chunks and
the box reassembles, verifies (size+sha256+BBB-shape), applies in place, and
rolls back on any apply failure. Signature verification is the caller's job;
this covers the reassembly + validated apply + rollback engine."""
import base64
import hashlib
import io
import subprocess
import tarfile

import pytest

from src import box_update
from src.box_update import BoxUpdateReceiver


def _make_bbb_tarball(version="V9.99", root="RotorSync-Big-Beautiful-Box-abc123"):
    """A GitHub-style tarball: single top dir containing a BBB repo snapshot."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name, data: bytes):
            info = tarfile.TarInfo(f"{root}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        add("dashboard.py", b"# dashboard\nprint('hi')\n")
        add("rotorsync_bumble.py", b"# bumble\n")
        add("src/__init__.py", b"")
        add("src/thing.py", b"x = 1\n")
        add("VERSION", (version + "\n").encode())
    return buf.getvalue()


def _receiver(tmp_path, **overrides):
    events = []
    kwargs = dict(
        emit_ack=lambda f: events.append(("ack", f)),
        emit_status=lambda f: events.append(("status", f)),
        repo_dir=str(tmp_path / "repo"),
        update_dir=str(tmp_path / "updates"),
        tmp_dir=str(tmp_path / "tmp"),
        refresh_opt=False,
    )
    kwargs.update(overrides)
    rx = BoxUpdateReceiver(**kwargs)
    return rx, events


def _seed_repo(tmp_path, version="V1.00"):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "dashboard.py").write_text("# old dashboard\n")
    (repo / "rotorsync_bumble.py").write_text("# old bumble\n")
    (repo / "src" / "old.py").write_text("old = True\n")
    (repo / "VERSION").write_text(version + "\n")
    return repo


def _stream(rx, tarball, update_id="upd-1", chunk_size=1024):
    sha = hashlib.sha256(tarball).hexdigest()
    rx.handle_begin({"update_id": update_id, "size": len(tarball), "sha256": sha})
    for off in range(0, len(tarball), chunk_size):
        piece = tarball[off:off + chunk_size]
        rx.handle_chunk({
            "update_id": update_id, "offset": off,
            "data_b64": base64.b64encode(piece).decode(),
        })
    rx.handle_finalize({"update_id": update_id})
    return sha


@pytest.fixture(autouse=True)
def _mock_subprocess(monkeypatch):
    """No-op systemctl/systemd-run/compile so tests don't touch the real system;
    record what would have run."""
    calls = []

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kw):
        calls.append(list(args))
        return _R()

    def fake_popen(args, **kw):
        calls.append(list(args))
        return _R()

    monkeypatch.setattr(box_update.subprocess, "run", fake_run)
    monkeypatch.setattr(box_update.subprocess, "Popen", fake_popen)
    return calls


def test_streamed_update_applies_in_place(tmp_path, _mock_subprocess):
    _seed_repo(tmp_path, "V1.00")
    rx, events = _receiver(tmp_path)
    tarball = _make_bbb_tarball(version="V9.99")
    _stream(rx, tarball)
    rx.handle_apply({"update_id": "upd-1"})

    repo = tmp_path / "repo"
    assert (repo / "VERSION").read_text().strip() == "V9.99"    # new code in place
    assert "print('hi')" in (repo / "dashboard.py").read_text()
    # a restart was scheduled (systemd-run) including rotorlink
    assert any("systemd-run" in c[0] for c in _mock_subprocess)
    assert any("update_applied" == f.get("type") for kind, f in events if kind == "status")


def test_sha_mismatch_rejected_at_finalize(tmp_path):
    _seed_repo(tmp_path)
    rx, _ = _receiver(tmp_path)
    tarball = _make_bbb_tarball()
    rx.handle_begin({"update_id": "u", "size": len(tarball),
                     "sha256": "0" * 64})   # wrong sha
    for off in range(0, len(tarball), 4096):
        rx.handle_chunk({"update_id": "u", "offset": off,
                         "data_b64": base64.b64encode(tarball[off:off + 4096]).decode()})
    with pytest.raises(ValueError, match="sha256 mismatch"):
        rx.handle_finalize({"update_id": "u"})


def test_non_bbb_tarball_rejected(tmp_path):
    _seed_repo(tmp_path)
    rx, _ = _receiver(tmp_path)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("random/notes.txt")
        data = b"nope"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    tarball = buf.getvalue()
    sha = hashlib.sha256(tarball).hexdigest()
    rx.handle_begin({"update_id": "u", "size": len(tarball), "sha256": sha})
    rx.handle_chunk({"update_id": "u", "offset": 0,
                     "data_b64": base64.b64encode(tarball).decode()})
    with pytest.raises(ValueError, match="BBB repo snapshot"):
        rx.handle_finalize({"update_id": "u"})


def test_chunk_offset_must_be_in_order(tmp_path):
    _seed_repo(tmp_path)
    rx, _ = _receiver(tmp_path)
    tarball = _make_bbb_tarball()
    sha = hashlib.sha256(tarball).hexdigest()
    rx.handle_begin({"update_id": "u", "size": len(tarball), "sha256": sha})
    with pytest.raises(ValueError, match="offset mismatch"):
        rx.handle_chunk({"update_id": "u", "offset": 999,
                         "data_b64": base64.b64encode(tarball[:10]).decode()})


def test_apply_rolls_back_when_copy_fails(tmp_path, monkeypatch, _mock_subprocess):
    _seed_repo(tmp_path, "V1.00")
    rx, events = _receiver(tmp_path)
    tarball = _make_bbb_tarball(version="V9.99")
    _stream(rx, tarball)

    # Make the in-place copy blow up AFTER the backup was taken.
    real_copy = box_update._copy_path
    state = {"n": 0}

    def flaky_copy(src, dst):
        # Fail the FORWARD apply (new code -> repo) but let backup capture and
        # rollback restore (src under .../backup) through, so we exercise a
        # successful rollback.
        if "repo" in str(dst) and "backup" not in str(src) and "backup" not in str(dst):
            raise OSError("disk full")
        return real_copy(src, dst)

    monkeypatch.setattr(box_update, "_copy_path", flaky_copy)
    with pytest.raises(RuntimeError, match="restored previous runtime"):
        rx.handle_apply({"update_id": "upd-1"})

    # Rollback restored the old VERSION (repo not left half-written).
    monkeypatch.setattr(box_update, "_copy_path", real_copy)
    assert (tmp_path / "repo" / "VERSION").read_text().strip() == "V1.00"
