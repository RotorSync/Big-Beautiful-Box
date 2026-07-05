"""Tests for src/disk_guard.py — the last-resort SD-card guard.

Pins: no action while disk is fine; oldest archives deleted first and only
until the target is met; the newest update-staging dir survives; live logs
truncated ONLY at emergency level; fill_history.log and bug_reports are
never touched at any stage.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import disk_guard


def build_home(tmp_path):
    home = tmp_path
    files = {
        'iol_dashboard.log': 30_000_000,
        'flow_control.log': 5_000_000,
        'fill_history.log': 40_000,
        'iol_dashboard.log.2.zst': 2_000_000,
        'iol_dashboard.log.3.zst': 2_000_000,
        'iol_dashboard.log.4.zst': 2_000_000,
    }
    for i, (name, size) in enumerate(files.items()):
        p = home / name
        p.write_bytes(b'x' * min(size, 4096))  # small real files; sizes faked by test logic
        age_days = {'iol_dashboard.log.4.zst': 9, 'iol_dashboard.log.3.zst': 5,
                    'iol_dashboard.log.2.zst': 2}.get(name, 0)
        stamp = time.time() - age_days * 86400
        os.utime(p, (stamp, stamp))
    reports = home / 'bug_reports'
    reports.mkdir()
    (reports / 'report1.txt').write_text('field report')
    staging = home / 'rotorsync-maintenance-updates'
    staging.mkdir()
    for i, name in enumerate(['old-update-1', 'old-update-2', 'newest-update']):
        d = staging / name
        d.mkdir()
        (d / 'chunk').write_bytes(b'y' * 1024)
        stamp = time.time() - (3 - i) * 3600
        os.utime(d, (stamp, stamp))
    return home


def snapshot(home):
    out = set()
    for root, _dirs, files in os.walk(home):
        for f in files:
            out.add(os.path.relpath(os.path.join(root, f), home))
    return out


def test_noop_when_disk_is_fine(tmp_path):
    home = build_home(tmp_path)
    before = snapshot(home)
    actions = disk_guard.run_guard(
        str(home), free_fn=lambda path='/': 10_000_000_000,
        vacuum_fn=lambda t: True, log=lambda m: None,
    )
    assert actions == []
    assert snapshot(home) == before


def test_stage1_deletes_oldest_archives_first_and_stops_at_target(tmp_path):
    home = build_home(tmp_path)
    frees = iter([
        1_000_000_000,  # initial check: low -> stage 1
        1_000_000_000,  # before archive 1 (oldest, .4)
        1_000_000_000,  # before archive 2 (.3)
        3_000_000_000,  # target met -> stop deleting archives
        3_000_000_000,  # staging check: above target -> skip
        3_000_000_000,  # journal check: skip
        3_000_000_000,  # stage2 check: fine
        3_000_000_000,  # final report
    ])
    actions = disk_guard.run_guard(
        str(home), free_fn=lambda path='/': next(frees),
        vacuum_fn=lambda t: True, log=lambda m: None,
    )
    deleted = [a for a in actions if a.startswith('delete archive')]
    assert len(deleted) == 2
    assert 'iol_dashboard.log.4.zst' in deleted[0]  # oldest first
    assert 'iol_dashboard.log.3.zst' in deleted[1]
    files = snapshot(home)
    assert 'iol_dashboard.log.2.zst' in files       # newest archive kept
    assert 'fill_history.log' in files
    assert os.path.join('bug_reports', 'report1.txt') in files


def test_stage1_prunes_stale_staging_keeps_newest(tmp_path):
    home = build_home(tmp_path)
    staging = home / 'rotorsync-maintenance-updates'
    disk_guard_staging = disk_guard.collect_stale_update_staging(str(staging))
    names = {os.path.basename(p) for p in disk_guard_staging}
    assert names == {'old-update-1', 'old-update-2'}


def test_stage2_truncates_live_logs_but_never_fill_history(tmp_path):
    home = build_home(tmp_path)
    fill_before = (home / 'fill_history.log').read_bytes()
    actions = disk_guard.run_guard(
        str(home), free_fn=lambda path='/': 100_000_000,  # below emergency
        vacuum_fn=lambda t: True, log=lambda m: None,
    )
    truncated = [a for a in actions if a.startswith('truncate live log')]
    # Only files >=1MB get truncated; our fakes are 4KB so none qualify —
    # verify the guard *attempted* stage 2 by making one big enough.
    assert truncated == []
    big = home / 'iol_dashboard.log'
    big.write_bytes(b'z' * 2_000_000)
    actions = disk_guard.run_guard(
        str(home), free_fn=lambda path='/': 100_000_000,
        vacuum_fn=lambda t: True, log=lambda m: None,
    )
    truncated = [a for a in actions if a.startswith('truncate live log')]
    assert len(truncated) == 1 and 'iol_dashboard.log' in truncated[0]
    assert (home / 'iol_dashboard.log').stat().st_size == 0
    assert (home / 'fill_history.log').read_bytes() == fill_before


def test_dry_run_reports_without_deleting(tmp_path):
    home = build_home(tmp_path)
    before = snapshot(home)
    actions = disk_guard.run_guard(
        str(home), free_fn=lambda path='/': 100_000_000,
        vacuum_fn=lambda t: True, dry_run=True, log=lambda m: None,
    )
    assert actions, 'expected planned actions at emergency level'
    assert snapshot(home) == before
