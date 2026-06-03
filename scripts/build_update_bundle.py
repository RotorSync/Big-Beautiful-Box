#!/usr/bin/env python3
"""Build a BBB maintenance update bundle for the RotorSync admin bridge."""

from __future__ import annotations

import argparse
import gzip
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time


REQUIRED_PATHS = ("dashboard.py", "rotorsync_bumble.py", "src")
RUNTIME_PATHS = (
    "dashboard.py",
    "rotorsync_bumble.py",
    "rotorsync_watchdog.py",
    "start_iol_dashboard.sh",
    "VERSION",
    "config.py",
    "install.sh",
    "requirements.txt",
    "src",
    "deploy",
)


def run_git(repo: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def repository_root() -> Path:
    script_root = Path(__file__).resolve().parents[1]
    git_root = run_git(script_root, ["rev-parse", "--show-toplevel"])
    return Path(git_root) if git_root else script_root


def sanitize_name(value: str) -> str:
    value = value.strip() or "unknown"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "unknown"


def included_dirty_paths(repo: Path, paths: tuple[str, ...]) -> list[str]:
    status = run_git(repo, ["status", "--porcelain", "--", *paths])
    if not status:
        return []
    dirty = []
    for line in status.splitlines():
        if not line:
            continue
        dirty.append(line[3:] if len(line) > 3 else line)
    return dirty


def tracked_files(repo: Path, relative_path: str) -> list[Path]:
    output = run_git(repo, ["ls-files", "--", relative_path])
    if output is None:
        root = repo / relative_path
        if root.is_file():
            return [root]
        if not root.is_dir():
            return []
        return [
            path
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        ]
    return [repo / line for line in output.splitlines() if line]


def collect_files(repo: Path) -> list[Path]:
    missing_required = [name for name in REQUIRED_PATHS if not (repo / name).exists()]
    if missing_required:
        raise SystemExit(f"Missing required update paths: {', '.join(missing_required)}")

    files: list[Path] = []
    seen: set[Path] = set()
    for relative in RUNTIME_PATHS:
        for path in tracked_files(repo, relative):
            if not path.exists():
                continue
            if path.is_symlink():
                raise SystemExit(f"Refusing to package symlink: {path.relative_to(repo)}")
            if path not in seen:
                seen.add(path)
                files.append(path)
    return sorted(files)


def add_file(archive: tarfile.TarFile, repo: Path, root_name: str, path: Path) -> None:
    relative = path.relative_to(repo)
    arcname = f"{root_name}/{relative.as_posix()}"
    info = archive.gettarinfo(str(path), arcname=arcname)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    with path.open("rb") as handle:
        archive.addfile(info, handle)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def default_output(repo: Path, version: str, commit: str | None) -> Path:
    dist = repo / "dist"
    suffix = sanitize_name(commit[:8]) if commit else time.strftime("%Y%m%d%H%M%S")
    return dist / f"Big-Beautiful-Box-{sanitize_name(version)}-{suffix}.tar.gz"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a tar.gz BBB update bundle accepted by the RotorSync maintenance bridge."
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output tar.gz path. Defaults to dist/Big-Beautiful-Box-<VERSION>-<commit>.tar.gz.",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow uncommitted changes in included runtime paths.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = repository_root()
    version = (repo / "VERSION").read_text(encoding="utf-8").strip()
    commit = run_git(repo, ["rev-parse", "--short=12", "HEAD"])
    root_name = f"Big-Beautiful-Box-{sanitize_name(version)}"
    if commit:
        root_name = f"{root_name}-{sanitize_name(commit[:8])}"

    if not args.allow_dirty:
        dirty = included_dirty_paths(repo, RUNTIME_PATHS)
        if dirty:
            print("Refusing to build from dirty included runtime paths:", file=sys.stderr)
            for path in dirty:
                print(f"  {path}", file=sys.stderr)
            print("Commit/stash those paths or rerun with --allow-dirty for a test bundle.", file=sys.stderr)
            return 2

    files = collect_files(repo)
    output = args.output or default_output(repo, version, commit)
    if not output.is_absolute():
        output = repo / output
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("wb") as raw_file:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as gzip_file:
            with tarfile.open(fileobj=gzip_file, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in files:
                    add_file(archive, repo, root_name, path)

    digest = sha256(output)
    size = output.stat().st_size
    print(f"bundle={output}")
    print(f"version={version}")
    print(f"commit={commit or 'unknown'}")
    print(f"files={len(files)}")
    print(f"size={size}")
    print(f"sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
