"""Transport-agnostic box software-update receiver.

The iPad streams a BBB repo tarball to the box in chunks and the box applies it
in place — the field update path when the box has NO internet of its own (the
iPad fetches the tarball over cellular; the box never talks to GitHub). The BLE
server (rotorsync_bumble.py) has its own copy of this flow; this module is the
shared, parameterised version the WiFi server (rotorlink) uses, so a field
update works over the trailer AP exactly as over BLE.

Wire protocol (frames, verified/authenticated by the caller BEFORE dispatch —
this module does NOT verify signatures, it only reassembles + applies):
  update_begin    {update_id, size, sha256}         -> stage a temp file
  update_chunk    {update_id, offset, data_b64}     -> append (strict ordering)
  update_finalize {update_id}                       -> size+sha256 check, validate
  update_apply    {update_id}                       -> backup, apply, schedule restart
  update_status   {update_id}                       -> report staged state

Safety mirrors the BLE path: tar members are path-checked (no abs/.. /links),
the archive must look like a BBB repo snapshot (dashboard.py + rotorsync_bumble.py
+ src), the new code is compile-gated before it replaces the running code, the
current runtime is backed up first, and any apply failure ROLLS BACK to the
backup so a bad package can never brick the box. Services restart out-of-band
(systemd-run) so the process applying the update can restart itself.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional

DEFAULT_RUNTIME_PATHS = (
    "dashboard.py",
    "rotorsync_bumble.py",
    "rotorsync_watchdog.py",
    "start_iol_dashboard.sh",
    "VERSION",
    "config.py",
    "requirements.txt",
    "install.sh",
    "src",
    "deploy",
)
# The WiFi path restarts rotorlink too (the BLE path doesn't touch it).
DEFAULT_RESTART_SERVICES = (
    "iol_dashboard.service",
    "rotorsync_watchdog.service",
    "rotorsync.service",
    "rotorlink.service",
)


def _safe_update_id(update_id) -> str:
    text = str(update_id or "").strip()
    if not text or not re.match(r"^[A-Za-z0-9._-]{1,128}$", text):
        raise ValueError("invalid update_id")
    return text


def _validate_tar_member(member: tarfile.TarInfo) -> None:
    name = member.name
    if name.startswith("/") or ".." in Path(name).parts:
        raise ValueError(f"unsafe tar path: {name}")
    if member.islnk() or member.issym() or member.isdev():
        raise ValueError(f"unsafe tar member type: {name}")


def _tar_contains_bbb_snapshot(members) -> bool:
    names = [Path(m.name) for m in members]
    root_names = {p.parts[0] for p in names if p.parts}
    candidate_roots = [""]
    if len(root_names) == 1:
        candidate_roots.append(next(iter(root_names)))
    for root in candidate_roots:
        prefix = f"{root}/" if root else ""
        has_dashboard = any(m.name == f"{prefix}dashboard.py" for m in members)
        has_bumble = any(m.name == f"{prefix}rotorsync_bumble.py" for m in members)
        has_src = any(
            m.name == f"{prefix}src" or m.name.startswith(f"{prefix}src/") for m in members
        )
        if has_dashboard and has_bumble and has_src:
            return True
    return False


def _find_extracted_update_root(extract_dir) -> Path:
    root = Path(extract_dir)
    if (root / "dashboard.py").exists() and (root / "rotorsync_bumble.py").exists():
        return root
    children = [c for c in root.iterdir() if c.is_dir()]
    if len(children) == 1:
        child = children[0]
        if (child / "dashboard.py").exists() and (child / "rotorsync_bumble.py").exists():
            return child
    raise ValueError("update tar does not look like a BBB repo snapshot")


def _copy_path(src, dst) -> None:
    src = Path(src)
    dst = Path(dst)
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    elif src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


class BoxUpdateReceiver:
    """Reassembles a streamed tarball and applies it. Signature verification is
    the caller's job; this only handles bytes → validated apply → restart."""

    def __init__(
        self,
        *,
        emit_ack: Callable[[dict], None],
        emit_status: Callable[[dict], None],
        logger=None,
        repo_dir: str = "/home/pi/Big-Beautiful-Box",
        update_dir: str = "/home/pi/rotorsync-maintenance-updates",
        tmp_dir: str = "/tmp/rotorsync-maintenance-update",
        runtime_paths: Iterable[str] = DEFAULT_RUNTIME_PATHS,
        restart_services: Iterable[str] = DEFAULT_RESTART_SERVICES,
        refresh_opt: bool = True,
    ) -> None:
        self._emit_ack = emit_ack
        self._emit_status = emit_status
        self._log = logger
        self.repo_dir = repo_dir
        self.update_dir = update_dir
        self.tmp_dir = tmp_dir
        self.runtime_paths = tuple(runtime_paths)
        self.restart_services = tuple(restart_services)
        self.refresh_opt = refresh_opt
        self._meta: Dict[str, dict] = {}

    # --- staging paths / meta ----------------------------------------------
    def _paths(self, update_id: str) -> dict:
        base = Path(self.update_dir) / update_id
        return {
            "base": base,
            "tmp": base / "artifact.part",
            "artifact": base / "artifact.tar",
            "meta": base / "meta.json",
        }

    def _write_meta(self, update_id: str, meta: dict) -> None:
        self._meta[update_id] = meta
        paths = self._paths(update_id)
        try:
            paths["base"].mkdir(parents=True, exist_ok=True)
            paths["meta"].write_text(json.dumps(meta))
        except OSError:
            pass

    def _read_meta(self, update_id: str) -> Optional[dict]:
        if update_id in self._meta:
            return self._meta[update_id]
        try:
            return json.loads(self._paths(update_id)["meta"].read_text())
        except (OSError, ValueError):
            return None

    def _ack(self, update_id: str, **extra) -> None:
        frame = {"type": "update_ack", "update_id": update_id}
        frame.update({k: v for k, v in extra.items() if v is not None})
        self._emit_ack(frame)

    def _status(self, event_type: str, update_id: str, text: str = "", **extra) -> None:
        frame = {"type": event_type, "update_id": update_id}
        if text:
            frame["text"] = text
        frame.update({k: v for k, v in extra.items() if v is not None})
        self._emit_status(frame)

    # --- frame handlers ----------------------------------------------------
    def handle_begin(self, frame: dict) -> None:
        update_id = _safe_update_id(frame.get("update_id"))
        expected_size = int(frame.get("size", -1))
        expected_sha = str(frame.get("sha256", "")).lower()
        if expected_size <= 0 or not re.match(r"^[a-f0-9]{64}$", expected_sha):
            raise ValueError("invalid update size or sha256")
        paths = self._paths(update_id)
        paths["base"].mkdir(parents=True, exist_ok=True)
        with open(paths["tmp"], "wb"):
            pass
        meta = {
            "update_id": update_id,
            "expected_size": expected_size,
            "expected_sha256": expected_sha,
            "received": 0,
            "status": "receiving",
            "started_at": time.time(),
        }
        self._write_meta(update_id, meta)
        self._ack(update_id, status="receiving", received=0, expected_size=expected_size)
        self._status("update_receiving", update_id, f"Receiving update {update_id}\n")

    def handle_chunk(self, frame: dict) -> None:
        update_id = _safe_update_id(frame.get("update_id"))
        paths = self._paths(update_id)
        meta = self._read_meta(update_id)
        if not meta or meta.get("status") != "receiving":
            raise ValueError("update is not receiving")
        offset = int(frame.get("offset", -1))
        try:
            chunk = base64.b64decode(str(frame.get("data_b64", "")), validate=True)
        except Exception as e:
            raise ValueError(f"invalid update chunk base64: {e}") from e
        current_size = paths["tmp"].stat().st_size if paths["tmp"].exists() else 0
        if offset != current_size:
            raise ValueError(f"chunk offset mismatch: got {offset}, expected {current_size}")
        expected_size = int(meta["expected_size"])
        if current_size + len(chunk) > expected_size:
            raise ValueError("update chunk exceeds expected size")
        with open(paths["tmp"], "ab") as f:
            f.write(chunk)
        meta["received"] = current_size + len(chunk)
        self._write_meta(update_id, meta)
        self._ack(
            update_id, offset=offset, size=len(chunk),
            received=meta["received"], expected_size=expected_size, status="receiving",
        )
        if meta["received"] == expected_size:
            self._status("update_received", update_id,
                         f'Received {meta["received"]} bytes for {update_id}\n')

    def handle_finalize(self, frame: dict) -> None:
        update_id = _safe_update_id(frame.get("update_id"))
        paths = self._paths(update_id)
        meta = self._read_meta(update_id)
        if not meta:
            raise ValueError("unknown update")
        expected_size = int(meta["expected_size"])
        actual_size = paths["tmp"].stat().st_size if paths["tmp"].exists() else -1
        if actual_size != expected_size:
            raise ValueError(f"update size mismatch: got {actual_size}, expected {expected_size}")
        digest = hashlib.sha256()
        with open(paths["tmp"], "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(block)
        actual_sha = digest.hexdigest()
        if actual_sha != meta["expected_sha256"]:
            raise ValueError("update sha256 mismatch")
        self._validate_archive(paths["tmp"])
        os.replace(paths["tmp"], paths["artifact"])
        meta.update({"status": "verified", "sha256": actual_sha, "size": actual_size})
        self._write_meta(update_id, meta)
        self._ack(update_id, sha256=actual_sha, size=actual_size,
                  received=actual_size, expected_size=expected_size, status="verified")
        self._status("update_verified", update_id,
                     f"Verified update {update_id}: {actual_size} bytes\n",
                     sha256=actual_sha, size=actual_size)

    def handle_status(self, frame: dict) -> None:
        update_id = _safe_update_id(frame.get("update_id"))
        meta = self._read_meta(update_id)
        if not meta:
            self._status("update_status", update_id,
                         f"No staged update {update_id}\n", status="missing")
            return
        self._status("update_status", update_id,
                     f'Update {update_id}: {meta.get("status", "unknown")}\n',
                     status=meta.get("status"))

    def handle_apply(self, frame: dict) -> None:
        """Blocking: validate + backup + apply + schedule restart. Run this in
        an executor from an async caller so the event loop isn't stalled."""
        update_id = _safe_update_id(frame.get("update_id"))
        paths = self._paths(update_id)
        meta = self._read_meta(update_id)
        if not meta or meta.get("status") != "verified" or not paths["artifact"].exists():
            raise ValueError("update is not verified")
        self._status("update_applying", update_id, f"Applying update {update_id}\n")
        try:
            self._apply_tar(update_id, paths["artifact"])
        except Exception as e:
            self._status("update_apply_failed", update_id,
                         f"Update apply failed: {e}\n", status="failed", error=str(e))
            raise
        meta["status"] = "applied"
        self._write_meta(update_id, meta)
        self._ack(update_id, status="applied")
        self._status("update_applied", update_id,
                     "Update applied; restarting services\n", status="applied")
        self._schedule_service_restart()

    # --- validate + apply --------------------------------------------------
    def _runtime_members(self, members):
        """The archive members under the runtime paths we actually install.

        A GitHub tarball is the WHOLE repo — including assets we never apply
        (e.g. web-sim/ holds a symlink). We only install the runtime paths, so
        we only validate + extract those: unrelated repo content (symlinks and
        all) is ignored instead of failing the whole update."""
        result = []
        for member in members:
            parts = Path(member.name).parts
            # Tolerate an optional single top-level dir (GitHub prefixes the tar
            # with e.g. "Big-Beautiful-Box-master/").
            rel = parts[1:] if len(parts) >= 2 else parts
            if rel and rel[0] in self.runtime_paths:
                result.append(member)
        return result

    def _validate_archive(self, artifact_path) -> None:
        if not tarfile.is_tarfile(artifact_path):
            raise ValueError("update artifact is not a tar archive")
        with tarfile.open(artifact_path) as archive:
            members = archive.getmembers()
            if not _tar_contains_bbb_snapshot(members):
                raise ValueError("update tar does not look like a BBB repo snapshot")
            # Only the members we will extract/install must be safe.
            for member in self._runtime_members(members):
                _validate_tar_member(member)

    def _backup_current_runtime(self, update_id: str) -> Path:
        backup_dir = Path(self.update_dir) / update_id / "backup"
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        repo = Path(self.repo_dir)
        for name in self.runtime_paths:
            src = repo / name
            if src.exists():
                _copy_path(src, backup_dir / name)
        return backup_dir

    def _restore_runtime_backup(self, backup_dir: Path) -> None:
        repo = Path(self.repo_dir)
        for name in self.runtime_paths:
            src = Path(backup_dir) / name
            if src.exists():
                _copy_path(src, repo / name)

    def _refresh_opt_runtime(self, repo_root: Path) -> None:
        # Mirror the /opt runtime the root services import (bumble + src) so a
        # restart picks up the new code, matching install/deploy behaviour.
        try:
            opt_bumble = Path("/opt/rotorsync_bumble.py")
            if (repo_root / "rotorsync_bumble.py").exists():
                _copy_path(repo_root / "rotorsync_bumble.py", opt_bumble)
                os.chmod(opt_bumble, 0o755)
            opt_watchdog = Path("/opt/rotorsync_watchdog.py")
            if (repo_root / "rotorsync_watchdog.py").exists():
                _copy_path(repo_root / "rotorsync_watchdog.py", opt_watchdog)
                os.chmod(opt_watchdog, 0o755)
            if (repo_root / "src").exists():
                Path("/opt/src").mkdir(parents=True, exist_ok=True)
                for item in (repo_root / "src").iterdir():
                    _copy_path(item, Path("/opt/src") / item.name)
        except OSError as e:
            if self._log:
                self._log.warning("refresh /opt runtime failed: %s", e)

    def _apply_tar(self, update_id: str, artifact_path) -> Path:
        extract_dir = Path(self.tmp_dir) / update_id
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)

        with tarfile.open(artifact_path) as archive:
            runtime_members = self._runtime_members(archive.getmembers())
            for member in runtime_members:
                _validate_tar_member(member)
            # Extract only the runtime paths we install — never touch unrelated
            # repo assets (web-sim symlink, etc.).
            archive.extractall(extract_dir, members=runtime_members)

        update_root = _find_extracted_update_root(extract_dir)
        for required in ("dashboard.py", "rotorsync_bumble.py", "src"):
            if not (update_root / required).exists():
                raise ValueError(f"update is missing {required}")

        # Compile-gate BEFORE replacing the running code — a package that won't
        # even import must never reach the services.
        subprocess.run(
            ["python3", "-m", "py_compile",
             str(update_root / "dashboard.py"), str(update_root / "rotorsync_bumble.py")],
            check=True, capture_output=True, text=True, timeout=30,
        )
        subprocess.run(
            ["python3", "-m", "compileall", "-q", str(update_root / "src")],
            check=True, capture_output=True, text=True, timeout=60,
        )

        repo = Path(self.repo_dir)
        if not repo.exists():
            raise ValueError(f"{self.repo_dir} does not exist")

        backup_dir = self._backup_current_runtime(update_id)
        try:
            for name in self.runtime_paths:
                src = update_root / name
                if src.exists():
                    _copy_path(src, repo / name)
            if self.refresh_opt:
                self._refresh_opt_runtime(repo)
            subprocess.run(["systemctl", "daemon-reload"],
                           capture_output=True, text=True, timeout=10)
        except Exception as apply_error:
            try:
                self._restore_runtime_backup(backup_dir)
                if self.refresh_opt:
                    self._refresh_opt_runtime(repo)
            except Exception as rollback_error:
                raise RuntimeError(
                    f"update apply failed and rollback failed: {apply_error}; "
                    f"rollback: {rollback_error}"
                ) from rollback_error
            raise RuntimeError(
                f"update apply failed; restored previous runtime: {apply_error}"
            ) from apply_error
        return update_root

    def _schedule_service_restart(self) -> None:
        services = " ".join(self.restart_services)
        restart_cmd = f"sleep 1; systemctl restart {services}"
        try:
            subprocess.run(
                ["systemd-run", "--unit=bbb-post-update-restart",
                 "--description=Restart BBB services after WiFi update",
                 "--on-active=1s", "/bin/bash", "-lc", restart_cmd],
                check=True, capture_output=True, text=True, timeout=10,
            )
        except Exception as e:
            if self._log:
                self._log.warning("systemd-run restart scheduling failed; fallback: %s", e)
            subprocess.Popen(["bash", "-lc", restart_cmd])
