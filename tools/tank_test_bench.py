#!/usr/bin/env python3
"""PC acceptance run: simulated IO-Link + simulated MFP CAN + latency A/B.

Runs the REAL dashboard.flow_control_loop / read_flow_meter source (extracted
from dashboard.py with ast, like tests/test_dashboard_*.py do) against a mock
Picomag (15-byte process data, fresh at ~12.5 Hz) at the production 50 Hz
interval, alternating logger OFF / ON phases, while a simulated MFP probe
sends the beta frame set on a CAN bus. Loop periods come from the loop's own
loop_dt_ms (what _record_flow_control_audit sees in production).

    python3 tools/tank_test_bench.py --phase-s 30 --interface virtual --channel bench
    python3 tools/tank_test_bench.py --interface socketcan --channel vcan0
"""
import argparse
import ast
import json
import os
import statistics
import struct
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
from src.tank_test_logger import TankTestLogger  # noqa: E402
from tools.mfp_can_sim import MfpSim, load_table  # noqa: E402

FUNCS = ("read_flow_meter", "flow_control_loop", "_decode_flow_meter_temp_f")


class MockPicomag:
    """Process data per src/flow_meter.py layout: [4:8] totalizer L (>f),
    [8:12] flow L/s (>f), [12:14] temp 0.1 C (>h). New value every 80 ms."""

    def __init__(self, profile, period=0.08):
        self.profile, self.period = profile, period
        self.t0 = time.monotonic()
        self.tot = 0.0
        self.last_k = -1
        self.raw = b"\x00" * 15
        self.lock = threading.Lock()

    def pd(self, port, offset, length, data):
        with self.lock:
            k = int((time.monotonic() - self.t0) / self.period)
            while self.last_k < k:
                self.last_k += 1
                flow = self.profile(self.last_k * self.period)
                self.tot += flow * self.period
                self.raw = (b"\x01\x00\x00\x00" + struct.pack(">f", self.tot) +
                            struct.pack(">f", flow) + struct.pack(">h", 185) + b"\x00")
            return self.raw

    def led(self, *a):
        pass

    LED_RED = LED_GREEN = 0


def fill_profile(t):
    """L/s: idle 3 s, ramp to ~120 GPM over 3 s, hold, taper, stop."""
    peak = 120 / 15.850323
    if t < 3:
        return 0.0
    if t < 6:
        return peak * (t - 3) / 3
    return peak


def build_loop_namespace(picomag, periods):
    src = (ROOT / "dashboard.py").read_text()
    tree = ast.parse(src)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCS]
    assert {n.name for n in nodes} == set(FUNCS), "dashboard functions not found"
    mod = ast.Module(body=nodes, type_ignores=[])
    noop = lambda *a, **k: None  # noqa: E731
    ns = {
        "time": time, "struct": struct, "config": config, "iolhat": picomag,
        "iol_io_lock": threading.RLock(), "flow_control_stop_event": threading.Event(),
        "last_totalizer_liters": 0.0, "last_signed_totalizer_liters": 0.0,
        "last_flow_rate": 0.0, "connection_error": False, "error_message": "",
        "last_successful_read_time": time.time(), "last_flow_meter_temp_f": None,
        "last_flow_read_was_fresh": False, "last_fresh_flow_read_time": 0.0,
        "consecutive_identical_raw": 0, "last_raw_data": None,
        "flow_control_last_tick": None, "flow_control_last_loop_time": 0.0,
        "flow_control_last_error_log_time": 0.0, "tank_test_logger": None,
        "_log_iol_disconnect_status": noop, "_try_iol_power_cycle": noop,
        "stale_raw_threshold_reads": lambda: 10 ** 9, "_read_iol_status_ok": lambda: (True, None),
        "log_flow_control": noop, "update_negative_totalizer_fault": noop,
        "update_negative_flow_fault": noop, "update_positive_drift_fault": noop,
        "detect_totalizer_reset": noop, "update_flow_meter_fault_hold": noop,
        "_reset_flow_control_audit": noop, "set_pump_stop_fault_hold": noop,
        "_maybe_revive_display_chain": noop,
        "flow_read_interval_seconds": lambda: config.FLOW_CONTROL_INTERVAL,
        "_record_flow_control_audit": lambda dt, read_ok=True: periods.append(dt),
        "_flow_control_process_sample": noop,
    }
    exec(compile(mod, "dashboard.py(extract)", "exec"), ns)
    return ns


def pct(v, p):
    s = sorted(v)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def stats(periods, interval_ms):
    v = periods[2:]  # drop the start-up tick
    jit = [abs(x - interval_ms) for x in v]
    return {"n": len(v), "mean_ms": round(statistics.mean(v), 4),
            "p50_ms": round(pct(v, 50), 3), "p99_ms": round(pct(v, 99), 3),
            "p99.9_ms": round(pct(v, 99.9), 3), "max_ms": round(max(v), 3),
            "jitter_p50_ms": round(pct(jit, 50), 3), "jitter_p99_ms": round(pct(jit, 99), 3),
            "jitter_p99.9_ms": round(pct(jit, 99.9), 3), "jitter_max_ms": round(max(jit), 3)}


def submit_cost_ns(n=200000):
    lg = TankTestLogger("/tmp/tanktest_cost", can_interface=None, queue_max=10 ** 7)
    lg.start("cost")
    t = time.perf_counter_ns()
    for i in range(n):
        lg.submit_flow(1.0, 2.0, 3.0, 4.0, 5.0, True)
    on = (time.perf_counter_ns() - t) / n
    lg.stop()
    x = None
    t = time.perf_counter_ns()
    for i in range(n):
        if x is not None:
            pass
    off = (time.perf_counter_ns() - t) / n
    return round(on, 1), round(off, 1)


def main():
    import can
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase-s", type=float, default=30.0)
    ap.add_argument("--pairs", type=int, default=2)
    ap.add_argument("--interface", default="virtual")
    ap.add_argument("--channel", default="bench")
    ap.add_argument("--out", default="/tmp/tank_test_logs")
    ap.add_argument("--table", default=str(ROOT / "tools" / "example_tank_table.csv"))
    a = ap.parse_args()

    interval_ms = config.FLOW_CONTROL_INTERVAL * 1000
    picomag = MockPicomag(fill_profile)
    start_gal = 120.0
    sim_bus = can.Bus(interface=a.interface, channel=a.channel)
    sim = MfpSim(sim_bus, load_table(a.table),
                 lambda t: start_gal + 0.264172 * picomag.tot, hall_fault_at=8.0)
    sim.start()
    results = []
    run_dirs = []
    for i in range(a.pairs * 2):
        mode = "off" if i % 2 == 0 else "on"
        periods = []
        ns = build_loop_namespace(picomag, periods)
        lg = None
        if mode == "on":
            log_bus = can.Bus(interface=a.interface, channel=a.channel)
            lg = TankTestLogger(a.out, can_bus=log_bus, can_channel="can0")
            run_dirs.append(lg.start(f"bench{i}"))
            ns["tank_test_logger"] = lg
        th = threading.Thread(target=ns["flow_control_loop"], name="flow-control", daemon=True)
        th.start()
        time.sleep(a.phase_s / 2)
        if lg:
            lg.mark("midpoint")
        time.sleep(a.phase_s / 2)
        ns["flow_control_stop_event"].set()
        th.join(2)
        if lg:
            lg.stop()
            log_bus.shutdown()
        s = stats(periods, interval_ms)
        s.update(mode=mode, phase=i)
        if lg:
            s.update(rows=lg.rows, dropped=lg.dropped, can_frames=lg.can_frames,
                     submitted=lg._seq)
        results.append(s)
        print(json.dumps(s), flush=True)
    sim.stop()
    sim_bus.shutdown()
    on_ns, off_ns = submit_cost_ns()
    summary = {"interval_ms": interval_ms, "submit_flow_ns": on_ns, "off_check_ns": off_ns,
               "interface": f"{a.interface}:{a.channel}", "runs": results, "run_dirs": run_dirs}
    for mode in ("off", "on"):
        r = [x for x in results if x["mode"] == mode]
        summary[f"{mode}_worst_jitter_p99_ms"] = max(x["jitter_p99_ms"] for x in r)
        summary[f"{mode}_mean_period_ms"] = round(statistics.mean(x["mean_ms"] for x in r), 4)
    print(json.dumps({k: v for k, v in summary.items() if k != "runs"}, indent=1))
    with open(os.path.join(a.out, "bench_summary.json"), "w") as f:
        json.dump(summary, f, indent=1)


if __name__ == "__main__":
    main()
