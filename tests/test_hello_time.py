"""Tests for src/hello_time.py — client_hello clock sync, shared BLE + WiFi.

The WiFi path (rotorlink) never set the box clock from a hello; crews are
WiFi-first now, and the no-internet field trailer is exactly the case where
only a WiFi hello may ever arrive. These tests pin the shared rules and the
cross-process once-per-boot marker.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.hello_time import (
    TIME_SYNC_MIN_EPOCH,
    maybe_apply_hello_time,
)

GOOD_EPOCH = 1783200000.0  # mid-2026, in range


def run(command, *, synced=False, marker=None, now=GOOD_EPOCH - 40.0):
    """Harness: capture logs and clock sets, isolated marker file."""
    logs, sets = [], []
    if marker is None:
        marker = os.path.join(tempfile.mkdtemp(), 'clock-set-marker')
    action = maybe_apply_hello_time(
        command,
        'Norman (iPhone) via wifi',
        logs.append,
        now_fn=lambda: now,
        set_clock=sets.append,
        is_synchronized=lambda: synced,
        marker_path=marker,
    )
    return action, logs, sets, marker


def test_sets_clock_when_unsynchronized():
    action, logs, sets, marker = run({'time': GOOD_EPOCH})
    assert action == 'applied'
    assert sets == [GOOD_EPOCH]
    assert os.path.exists(marker), 'once-per-boot marker not written'
    assert any('Clock set' in line for line in logs)


def test_ntp_synchronized_clock_is_never_touched():
    action, logs, sets, _ = run({'time': GOOD_EPOCH}, synced=True, now=GOOD_EPOCH - 500)
    assert action == 'ntp-authoritative'
    assert sets == []
    assert any('DISCREPANCY' in line for line in logs)  # 500s off -> logged


def test_ntp_small_disagreement_is_silent():
    action, logs, sets, _ = run({'time': GOOD_EPOCH}, synced=True, now=GOOD_EPOCH - 30)
    assert action == 'ntp-authoritative'
    assert sets == [] and logs == []


def test_marker_prevents_second_set_across_processes():
    action, _, sets, marker = run({'time': GOOD_EPOCH})
    assert action == 'applied' and len(sets) == 1
    # Second hello (e.g. the OTHER server) sees the marker: no re-step.
    action2, _, sets2, _ = run({'time': GOOD_EPOCH + 5}, marker=marker)
    assert action2 == 'already-set'
    assert sets2 == []


def test_missing_and_garbage_time_do_nothing():
    for cmd, expected in (
        ({}, 'no-time'),
        ({'time': 'abc'}, 'bad-time'),
        ({'time': TIME_SYNC_MIN_EPOCH - 100}, 'bad-time'),
        ({'time': 9e12}, 'bad-time'),
    ):
        action, _, sets, _ = run(cmd)
        assert action == expected, cmd
        assert sets == []


def test_epoch_fallback_field():
    action, _, sets, _ = run({'epoch': GOOD_EPOCH})
    assert action == 'applied'
    assert sets == [GOOD_EPOCH]


def test_set_clock_failure_is_reported_not_raised():
    logs, marker = [], os.path.join(tempfile.mkdtemp(), 'm')

    def boom(epoch):
        raise PermissionError('CAP_SYS_TIME')

    action = maybe_apply_hello_time(
        {'time': GOOD_EPOCH}, 'x', logs.append,
        now_fn=lambda: GOOD_EPOCH, set_clock=boom,
        is_synchronized=lambda: False, marker_path=marker,
    )
    assert action == 'failed'
    assert not os.path.exists(marker), 'marker must not be written on failure'
    assert any('FAILED' in line for line in logs)
