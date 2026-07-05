"""Last-resort disk guard: free space before the SD card seizes the box.

logrotate keeps the known logs bounded on a schedule, but nothing reacts to
the disk actually FILLING (a runaway log, journald on an uncapped box, update
staging piling up). This guard runs hourly (bbb-disk-guard.timer) and only
acts when free space is genuinely low, in escalating stages:

  Stage 1 (< STAGE1_FREE_BYTES free):  delete expendable, already-rotated
    diagnostics — oldest compressed/rotated log archives first, then all but
    the newest offline-update staging dir, then vacuum journald down.
  Stage 2 (< STAGE2_FREE_BYTES free):  additionally truncate the live
    diagnostics logs in place (their writers use O_APPEND; truncation is
    safe mid-write).

NEVER touched, at any stage: fill_history.log (the season's load record —
small, and already uploaded to the server as loads), bug_reports/ (local
evidence, tiny), calibration/config files, the repo, /opt. Everything the
guard deletes is regenerable diagnostics. Every action is printed so the
journal shows exactly what was freed and why.
"""

import argparse
import glob
import os
import subprocess
import time

HOME = '/home/pi'

STAGE1_FREE_BYTES = 1_500_000_000  # below this, start clearing diagnostics
STAGE1_TARGET_BYTES = 2_500_000_000  # clear until this much is free (or out of things to clear)
STAGE2_FREE_BYTES = 400_000_000  # emergency: also truncate live logs

# Rotated/compressed diagnostics archives, safe to delete oldest-first.
ARCHIVE_PATTERNS = (
    '*.log.[0-9]*.zst', '*.log.[0-9]*.gz', '*.log.[0-9]',
    '*.csv.[0-9]*.zst', '*.csv.[0-9]*.gz', '*.csv.[0-9]',
)

# Live logs whose writers append; truncating in place is safe. fill_history
# and bug_reports are deliberately NOT here.
STAGE2_TRUNCATE = (
    'iol_dashboard.log', 'flow_control.log', 'serial_debug.log',
    'button_debug.log', 'menu_debug.log', 'reset_debug.log',
    'relay_test.log', 'rotorsync_watchdog.log', 'mopeka_history.csv',
)

UPDATE_STAGING_DIR = os.path.join(HOME, 'rotorsync-maintenance-updates')


def free_bytes(path='/'):
    s = os.statvfs(path)
    return s.f_bavail * s.f_frsize


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def collect_archives(home=HOME):
    """Rotated archives, oldest first (by mtime)."""
    found = []
    for pattern in ARCHIVE_PATTERNS:
        for path in glob.glob(os.path.join(home, pattern)):
            try:
                st = os.stat(path)
            except OSError:
                continue
            found.append((st.st_mtime, st.st_size, path))
    found.sort()
    return found


def collect_stale_update_staging(staging_dir=UPDATE_STAGING_DIR):
    """All offline-update staging dirs except the newest (rollback backup)."""
    try:
        entries = [
            os.path.join(staging_dir, name)
            for name in os.listdir(staging_dir)
        ]
    except OSError:
        return []
    dirs = [(os.path.getmtime(p), p) for p in entries if os.path.isdir(p)]
    dirs.sort()
    return [p for _mtime, p in dirs[:-1]]  # keep the newest


def vacuum_journal(target='200M', run=subprocess.run):
    try:
        result = run(
            ['journalctl', f'--vacuum-size={target}'],
            capture_output=True, text=True, timeout=60,
        )
        return result.returncode == 0
    except Exception:
        return False


def run_guard(
    home=HOME,
    *,
    free_fn=free_bytes,
    vacuum_fn=vacuum_journal,
    dry_run=False,
    log=print,
):
    """Returns the list of actions taken (strings). No-op when disk is fine."""
    actions = []
    free = free_fn()
    if free >= STAGE1_FREE_BYTES:
        return actions

    log(f'disk_guard: LOW DISK — {free / 1e9:.2f}GB free; clearing expendable diagnostics')

    def act(description, fn):
        actions.append(description)
        log(f'disk_guard: {description}{" (dry-run)" if dry_run else ""}')
        if not dry_run:
            try:
                fn()
            except Exception as e:
                log(f'disk_guard: FAILED {description}: {type(e).__name__}: {e}')

    # 1. Oldest rotated archives until the target is met.
    for _mtime, size, path in collect_archives(home):
        if free_fn() >= STAGE1_TARGET_BYTES:
            break
        act(f'delete archive {path} ({size / 1e6:.1f}MB)',
            lambda p=path: os.remove(p))

    # 2. Stale offline-update staging (keep the newest for rollback).
    if free_fn() < STAGE1_TARGET_BYTES:
        for path in collect_stale_update_staging():
            size = _dir_size(path)
            act(f'delete stale update staging {path} ({size / 1e6:.1f}MB)',
                lambda p=path: __import__('shutil').rmtree(p, ignore_errors=True))

    # 3. Journald vacuum.
    if free_fn() < STAGE1_TARGET_BYTES:
        act('vacuum journald to 200M', lambda: vacuum_fn('200M'))

    # Stage 2: still critically low — truncate live diagnostics logs in place.
    if free_fn() < STAGE2_FREE_BYTES:
        log('disk_guard: EMERGENCY — still critically low; truncating live diagnostics logs')
        for name in STAGE2_TRUNCATE:
            path = os.path.join(home, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size < 1_000_000:
                continue
            act(f'truncate live log {path} ({size / 1e6:.1f}MB)',
                lambda p=path: os.truncate(p, 0))

    log(f'disk_guard: done — {free_fn() / 1e9:.2f}GB free, {len(actions)} action(s)')
    return actions


def main(argv=None):
    parser = argparse.ArgumentParser(description='BBB last-resort disk guard')
    parser.add_argument('--dry-run', action='store_true',
                        help='report what would be deleted without deleting')
    args = parser.parse_args(argv)
    started = time.time()
    actions = run_guard(dry_run=args.dry_run)
    if actions:
        print(f'disk_guard: {len(actions)} action(s) in {time.time() - started:.1f}s', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
