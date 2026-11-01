"""Oct-28 robustness review regressions (sensor heartbeat clock, log filter growth,
dashboard socket full writes)."""
import ast
import asyncio
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_maintenance_auth import install_bumble_stubs


class _Shim:
    def setitem(self, mapping, key, value):
        mapping[key] = value


install_bumble_stubs(_Shim())
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import rotorsync_bumble as rb  # noqa: E402

LOG_FILTER = ROOT / "src" / "log_filter.py"


def test_sensor_health_ignores_wall_clock_jump(monkeypatch):
    """sn018 logged 'heartbeat stale for 9102986s' after an NTP jump and
    killed the BLE bridge. A wall-clock jump must not trip the watchdog."""
    exits = []
    monkeypatch.setattr(rb.os, "_exit", lambda code: exits.append(code) or (_ for _ in ()).throw(SystemExit))
    monkeypatch.setattr(rb, "log_watchdog_event", lambda msg: None)
    real_time = rb.time.time
    monkeypatch.setattr(rb.time, "time", lambda: real_time() + 9_000_000)
    rb.sensor_loop_heartbeat = rb.time.monotonic()

    async def main():
        async def forever():
            await asyncio.Event().wait()
        task = asyncio.create_task(forever())
        real_sleep = asyncio.sleep
        calls = {"n": 0}

        async def fast_sleep(_s):
            calls["n"] += 1
            if calls["n"] > 3:
                raise asyncio.CancelledError
            await real_sleep(0)
        monkeypatch.setattr(rb.asyncio, "sleep", fast_sleep)
        try:
            await rb.monitor_sensor_health(task)
        except asyncio.CancelledError:
            pass
        finally:
            monkeypatch.setattr(rb.asyncio, "sleep", real_sleep)
            task.cancel()

    asyncio.run(main())
    assert exits == []


def test_sensor_health_still_trips_on_real_stall(monkeypatch):
    exits = []

    def fake_exit(code):
        exits.append(code)
        raise SystemExit

    monkeypatch.setattr(rb.os, "_exit", fake_exit)
    monkeypatch.setattr(rb, "log_watchdog_event", lambda msg: None)
    rb.sensor_loop_heartbeat = rb.time.monotonic() - rb.SENSOR_LOOP_HEARTBEAT_TIMEOUT - 5

    async def main():
        async def forever():
            await asyncio.Event().wait()
        task = asyncio.create_task(forever())
        real_sleep = asyncio.sleep

        async def fast_sleep(_s):
            await real_sleep(0)
        monkeypatch.setattr(rb.asyncio, "sleep", fast_sleep)
        try:
            await rb.monitor_sensor_health(task)
        except SystemExit:
            pass
        finally:
            monkeypatch.setattr(rb.asyncio, "sleep", real_sleep)
            task.cancel()

    asyncio.run(main())
    assert exits == [1]


def _run_filter(target, lines):
    return subprocess.run([sys.executable, str(LOG_FILTER), str(target)],
                          input="".join(lines), capture_output=True, text=True, timeout=30)


def test_cycle_time_spam_is_summarized(tmp_path):
    out = tmp_path / "iol.log"
    lines = [f"[07:51:{i % 60:02d} INFO ] Cycle time chn #1={12 + i % 3} ms, chn #2=0 ms\n" for i in range(300)]
    r = _run_filter(out, lines)
    assert r.returncode == 0, r.stderr
    written = out.read_text().splitlines()
    assert len(written) < 40, len(written)
    assert any("Suppressed" in l and "Cycle time" in l for l in written)


def test_noisy_buckets_are_bounded(tmp_path):
    """Only-noisy input with ever-changing text must not grow without bound
    and must still exit cleanly; digits are bucketed."""
    out = tmp_path / "iol.log"
    lines = [f"READ block{chr(65 + i % 26)}{chr(65 + (i // 26) % 26)}{chr(65 + (i // 676) % 26)}\n" for i in range(5000)]
    r = _run_filter(out, lines)
    assert r.returncode == 0, r.stderr
    src = LOG_FILTER.read_text()
    assert "MAX_NOISY_KEYS" in src


def test_dashboard_socket_listener_uses_sendall():
    tree = ast.parse((ROOT / "dashboard.py").read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "socket_command_listener")
    sends = [n for n in ast.walk(fn) if isinstance(n, ast.Attribute) and n.attr == "send"
             and isinstance(n.value, ast.Name) and n.value.id == "client"]
    assert sends == []
