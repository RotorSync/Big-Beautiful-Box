"""The display redraw chain must survive any tick failure and revive in place.

Locks the fix for the field failure "box screen frozen but the app still shows
flow": one exception inside update_dashboard() used to kill the Tk after-chain
forever while the flow-control thread and the :9999 listener kept serving BLE/
WiFi clients, and a dead or stalled log pipe used to make every print() raise
(BrokenPipeError) or block the mainloop. No service restart is acceptable as a
cure - a restart blanks the screen and drops pending-fill state mid-fill - so
these tests assert the in-place healing behavior.
"""
import ast
import os
import queue
import subprocess
import sys
import threading
import time as real_time
import traceback
from pathlib import Path

import pytest

import config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_PATH = PROJECT_ROOT / "dashboard.py"
LOG_FILTER = PROJECT_ROOT / "src" / "log_filter.py"


class FakeTime:
    def __init__(self, now=1000.0):
        self.now = now

    def time(self):
        return self.now

    def strftime(self, fmt, *args):
        return real_time.strftime(fmt, *args)


class FakeRoot:
    def __init__(self):
        self.scheduled = []
        self.cancelled = []
        self._next_id = 0

    def after(self, delay, callback, *args):
        self._next_id += 1
        after_id = f"after#{self._next_id}"
        self.scheduled.append((delay, callback, args, after_id))
        return after_id

    def after_cancel(self, after_id):
        self.cancelled.append(after_id)


def _extract(names):
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(DASHBOARD_PATH))
    module = ast.Module(
        body=[
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    return compile(module, str(DASHBOARD_PATH), "exec")


def _chain_namespace(tick):
    """Load the chain-guard functions with a controllable world around them."""
    fake_time = FakeTime()
    root = FakeRoot()
    errors = []
    flow_logs = []
    ns = {
        "config": config,
        "os": os,
        "time": fake_time,
        "traceback": traceback,
        "root": root,
        "_update_dashboard_tick": tick,
        "_log_tick_error": errors.append,
        "log_flow_control": flow_logs.append,
        "DISPLAY_TICK_STALE_SECONDS": 5.0,
        "DISPLAY_REVIVE_MIN_SPACING_SECONDS": 10.0,
        "last_dashboard_tick_at": fake_time.now,
        "_dashboard_chain_started": False,
        "_dashboard_after_id": None,
        "_dashboard_tick_failures": 0,
        "_dashboard_tick_last_error_at": 0.0,
        "_dashboard_revives": 0,
        "_display_revive_last_kick_at": 0.0,
    }
    exec(
        _extract({"update_dashboard", "_revive_dashboard_chain", "_maybe_revive_display_chain"}),
        ns,
    )
    return ns, fake_time, root, errors, flow_logs


def test_tick_exception_does_not_kill_chain():
    """The historical bug: an OSError (full/read-only SD) in the tick body
    must cost one frame, never the chain."""
    def bad_tick():
        raise OSError(28, "No space left on device")

    ns, fake_time, root, errors, _ = _chain_namespace(bad_tick)

    ns["update_dashboard"]()

    assert len(root.scheduled) == 1, "tick must reschedule despite the exception"
    assert root.scheduled[0][0] == config.UPDATE_INTERVAL
    assert ns["_dashboard_tick_failures"] == 1
    assert ns["last_dashboard_tick_at"] == fake_time.now
    assert any("No space left on device" in e for e in errors)

    # The chain keeps going tick after tick even while the fault persists.
    root.scheduled[-1][1]()
    root.scheduled[-1][1]()
    assert len(root.scheduled) == 3
    assert ns["_dashboard_tick_failures"] == 3


def test_tick_error_logging_is_rate_limited():
    def bad_tick():
        raise ValueError("boom")

    ns, fake_time, root, errors, _ = _chain_namespace(bad_tick)

    ns["update_dashboard"]()
    fake_time.now += 1.0
    root.scheduled[-1][1]()
    assert len(errors) == 1, "second failure within 30s must not log again"

    fake_time.now += 31.0
    root.scheduled[-1][1]()
    assert len(errors) == 2


def test_successful_tick_resets_failure_count():
    state = {"fail": True}

    def flaky_tick():
        if state["fail"]:
            raise RuntimeError("transient")

    ns, _, root, _, _ = _chain_namespace(flaky_tick)

    ns["update_dashboard"]()
    assert ns["_dashboard_tick_failures"] == 1

    state["fail"] = False
    root.scheduled[-1][1]()
    assert ns["_dashboard_tick_failures"] == 0


def test_revive_is_noop_while_chain_is_fresh():
    ns, fake_time, root, _, _ = _chain_namespace(lambda: None)
    ns["last_dashboard_tick_at"] = fake_time.now  # fresh heartbeat

    ns["_revive_dashboard_chain"]()

    assert ns["_dashboard_revives"] == 0
    assert root.scheduled == []
    assert root.cancelled == []


def test_revive_restarts_a_stale_chain_in_place():
    ticks = []
    ns, fake_time, root, errors, _ = _chain_namespace(lambda: ticks.append(1))
    ns["last_dashboard_tick_at"] = fake_time.now - 12.0
    ns["_dashboard_after_id"] = "after#zombie"

    ns["_revive_dashboard_chain"]()

    assert root.cancelled == ["after#zombie"], "pending zombie tick must be cancelled first"
    assert ticks == [1], "revive runs a real tick immediately"
    assert len(root.scheduled) == 1, "and the chain is rescheduled"
    assert ns["_dashboard_revives"] == 1
    assert ns["last_dashboard_tick_at"] == fake_time.now
    assert any("revived" in e for e in errors)


def test_maybe_revive_queues_on_tk_thread_with_spacing():
    ns, fake_time, root, _, flow_logs = _chain_namespace(lambda: None)

    # Not started yet: never kicks (mainloop may not be up).
    ns["last_dashboard_tick_at"] = fake_time.now - 60.0
    ns["_maybe_revive_display_chain"]()
    assert root.scheduled == []

    ns["_dashboard_chain_started"] = True
    ns["_maybe_revive_display_chain"]()
    assert len(root.scheduled) == 1
    assert root.scheduled[0][0] == 0
    assert root.scheduled[0][1] is ns["_revive_dashboard_chain"]
    assert any("display_chain_stale" in line for line in flow_logs)

    # Within the spacing window: no second kick.
    fake_time.now += 5.0
    ns["_maybe_revive_display_chain"]()
    assert len(root.scheduled) == 1

    # After the window (still stale): kick again.
    fake_time.now += 6.0
    ns["_maybe_revive_display_chain"]()
    assert len(root.scheduled) == 2

    # Fresh heartbeat: no kick even long after the spacing window.
    fake_time.now += 60.0
    ns["last_dashboard_tick_at"] = fake_time.now - 1.0
    ns["_maybe_revive_display_chain"]()
    assert len(root.scheduled) == 2


def _stream_cls():
    ns = {"queue": queue, "threading": threading}
    exec(_extract({"_ResilientStream"}), ns)
    return ns["_ResilientStream"]


class _BrokenRaw:
    def __init__(self):
        self.attempts = 0

    def write(self, text):
        self.attempts += 1
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self):
        pass


class _BlockingRaw:
    def __init__(self):
        self.release = threading.Event()
        self.wrote = []

    def write(self, text):
        self.release.wait(timeout=10)
        self.wrote.append(text)

    def flush(self):
        pass


def test_resilient_stream_write_never_raises_on_broken_pipe():
    """print() after the log-filter process dies must not raise - a
    BrokenPipeError inside the display tick froze the box screen."""
    stream = _stream_cls()(_BrokenRaw())
    for _ in range(50):
        stream.write("line\n")  # must not raise
        stream.flush()
    stream.close_and_drain()


def test_resilient_stream_write_never_blocks_on_stalled_pipe():
    """print() into a stalled pipe must return immediately and drop, not
    wedge the Tk mainloop."""
    raw = _BlockingRaw()
    stream = _stream_cls()(raw, max_queued=4)
    try:
        started = real_time.monotonic()
        for _ in range(50):
            stream.write("line\n")
        elapsed = real_time.monotonic() - started

        assert elapsed < 0.5, "writes must not block on the stalled drain"
        assert stream.dropped > 0, "overflow must be dropped, not queued unbounded"
    finally:
        raw.release.set()
        stream.close_and_drain()


def _run_log_filter(target, lines, timeout=15):
    return subprocess.run(
        [sys.executable, str(LOG_FILTER), str(target)],
        input="".join(lines),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.mark.skipif(not os.path.exists("/dev/full"), reason="requires /dev/full")
def test_log_filter_survives_unwritable_target():
    """ENOSPC on every write (the 19GB-log incident) must not kill the
    filter: a dead filter breaks the dashboard's stdout pipe and a stopped
    reader backs the pipe up until print() blocks the GUI."""
    result = _run_log_filter("/dev/full", [f"line {i}\n" for i in range(500)])
    assert result.returncode == 0, result.stderr


def test_log_filter_still_writes_and_suppresses(tmp_path):
    target = tmp_path / "out.log"
    lines = ["unique start\n"] + ["READ sensor block\n"] * 30 + ["unique end\n"]
    result = _run_log_filter(target, lines)

    assert result.returncode == 0, result.stderr
    text = target.read_text()
    assert "unique start" in text
    assert "unique end" in text
    assert text.count("READ sensor block") < 30, "noisy repeats must be suppressed"
    assert "Suppressed" in text


def test_log_filter_recovers_when_target_becomes_writable(tmp_path):
    """After write failures the filter must retry the file and resume logging
    (dropping lines while it can't), not stay silent forever."""
    target = tmp_path / "out.log"
    target.mkdir()  # opening a directory for append fails, even as root

    env = dict(os.environ, LOG_FILTER_REOPEN_SEC="0.05")
    proc = subprocess.Popen(
        [sys.executable, str(LOG_FILTER), str(target)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    try:
        proc.stdin.write("while unwritable\n")
        proc.stdin.flush()
        real_time.sleep(0.3)

        target.rmdir()  # target becomes creatable; the reopen retry should kick in
        real_time.sleep(0.3)

        proc.stdin.write("after recovery\n")
        proc.stdin.flush()
        proc.stdin.close()
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()

    text = target.read_text()
    assert "after recovery" in text, "logging must resume once the target is writable"
    assert "while unwritable" not in text, "lines during the outage are dropped, not queued"
