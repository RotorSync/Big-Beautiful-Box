#!/usr/bin/env python3
"""Opt-in tank-test data logger: Picomag flow + MFP CAN on one clock.

OFF BY DEFAULT. Nothing in this module runs unless config.TANK_TEST_LOGGER_ENABLED
is True (or BBB_TANK_TEST_LOGGER=1), or you run the CLI by hand.

Design rules (see docs/TANK_TEST_LOGGER.md):
  * Never opens IO-Link. Flow samples are handed in by the existing
    flow-control thread (dashboard.flow_control_loop) via submit_flow().
  * submit_flow() is the only code on the flow-control path. It is a bounded
    deque append; if the writer falls behind the sample is DROPPED and
    counted, never blocked on. It never raises.
  * One timestamp source: time.monotonic() (CLOCK_MONOTONIC) for every flow
    sample, CAN frame and marker. Wall time is recorded alongside.
  * CAN via python-can: socketcan (gs_usb/candleLight CANable -> can0) or
    slcan (CANable stock firmware -> /dev/ttyACMx), 250 kbit/s, 29-bit.
Outputs per run directory <root>/tanktest_YYYYmmdd_HHMMSS[_label]/:
  flow_mfp.csv   one row per fresh flow sample (+1 s heartbeat) + event rows
  can_raw.log    candump -l format "(epoch) can0 18FF1010#..." every frame
  markers.txt    START / MARK / STOP lines with mono + wall time
  run_info.json  settings, counters (written at stop)
"""
import collections
import csv
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime

try:  # package import (dashboard) or flat import (tests/CLI)
    from src import mfp_can_decode as mfp
except ImportError:  # pragma: no cover
    import mfp_can_decode as mfp

L_TO_GAL = 0.264172
LPS_TO_GPM = 15.850323

FRAME_KINDS = ("LEVEL", "FAST", "ATTITUDE", "HEALTH_A", "HEALTH_B", "HALL_FAULT")

CSV_FIELDS = [
    "event", "mono_s", "wall_epoch", "wall_iso", "sample_seq", "fresh",
    "totalizer_l", "totalizer_gal", "flow_lps", "flow_gpm", "picomag_temp_f",
    "mfp_sa",
    "level_vol_gal", "level_in", "level_flow_gpm", "mfp_status", "mfp_status_text",
    "level_seq", "level_age_s",
    "fast_vol_gal", "z_null_in_raw", "slosh_amp_in", "fast_age_s",
    "pitch_deg", "roll_deg", "mfp_temp_c", "vib_energy", "attitude_age_s",
    "sensor_fault_map", "health_a_age_s",
    "amplitude_pct", "fit_residual_pct", "uptime_min", "reset_count", "fw_rev",
    "tec", "rec", "health_b_age_s",
    "hall_last", "hall_n_active", "hall_age_s",
    "flow_dropped", "can_frames", "can_errors",
]


def _fmt(v, nd=4):
    if v is None:
        return ""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return v


class TankTestLogger:
    def __init__(self, out_root, can_interface="socketcan", can_channel="can0",
                 can_bitrate=250000, can_bus=None, queue_max=4096,
                 heartbeat_s=1.0, fresh_only=True, mfp_sa=None, clock=time.monotonic):
        self.out_root = out_root
        self.can_interface = can_interface
        self.can_channel = can_channel
        self.can_bitrate = can_bitrate
        self._bus = can_bus            # injectable (tests / python-can virtual)
        self._own_bus = can_bus is None
        self.queue_max = int(queue_max)
        self.heartbeat_s = heartbeat_s
        self.fresh_only = fresh_only
        self.mfp_sa = mfp_sa           # None = accept any SA (first seen is reported)
        self.clock = clock

        self._q = collections.deque()  # producer: flow thread; consumer: writer
        self._events = collections.deque()
        self._seq = 0
        self.dropped = 0
        self.running = False
        self.run_dir = None
        self.error = ""
        self.can_frames = 0
        self.can_errors = 0
        self.rows = 0

        self._mfp_lock = threading.Lock()   # logger-internal only (CAN vs writer)
        self._latest = {}                   # kind -> (mono, sa, fields)
        self._stop_evt = threading.Event()
        self._threads = []
        self._csv_f = self._can_f = self._mark_f = None

    # ------------------------------------------------------------ hot path
    def submit_flow(self, mono, wall, totalizer_l, flow_lps, temp_f=None, fresh=True):
        """Called from the flow-control thread. O(1), never blocks or raises."""
        if not self.running:
            return False
        try:
            if len(self._q) >= self.queue_max:
                self.dropped += 1
                return False
            self._seq += 1
            self._q.append((mono, wall, self._seq, fresh, totalizer_l, flow_lps, temp_f))
            return True
        except Exception:  # pragma: no cover - defensive, must not reach caller
            return False

    def mark(self, label="MARK"):
        if self.running:
            self._events.append((self.clock(), time.time(), f"MARK:{label}"))

    # ------------------------------------------------------------ lifecycle
    def start(self, label=""):
        if self.running:
            return self.run_dir
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"tanktest_{stamp}" + (f"_{_safe(label)}" if label else "")
        self.run_dir = os.path.join(self.out_root, name)
        os.makedirs(self.run_dir, exist_ok=True)
        self._csv_f = open(os.path.join(self.run_dir, "flow_mfp.csv"), "w", newline="")
        self._csv = csv.writer(self._csv_f)
        self._csv.writerow(CSV_FIELDS)
        self._can_f = open(os.path.join(self.run_dir, "can_raw.log"), "w")
        self._mark_f = open(os.path.join(self.run_dir, "markers.txt"), "w")
        self._start_mono = self.clock()
        self._stop_evt.clear()
        self.running = True
        self._events.append((self._start_mono, time.time(), "START" + (f":{label}" if label else "")))
        if self._bus is None and self.can_interface:
            self._bus = self._open_bus()
        if self._bus is not None:
            t = threading.Thread(target=self._can_loop, name="tanktest-can", daemon=True)
            t.start()
            self._threads.append(t)
        t = threading.Thread(target=self._writer_loop, name="tanktest-writer", daemon=True)
        t.start()
        self._threads.append(t)
        return self.run_dir

    def stop(self):
        if not self.running:
            return
        self._events.append((self.clock(), time.time(), "STOP"))
        self.running = False           # producers stop enqueuing now
        self._stop_evt.set()
        for t in self._threads:
            t.join(timeout=3.0)
        self._threads = []
        if self._own_bus and self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception:
                pass
            self._bus = None
        info = {
            "run_dir": self.run_dir, "rows": self.rows, "flow_dropped": self.dropped,
            "flow_samples_submitted": self._seq, "can_frames": self.can_frames,
            "can_errors": self.can_errors, "error": self.error,
            "can_interface": self.can_interface, "can_channel": self.can_channel,
            "can_bitrate": self.can_bitrate, "fresh_only": self.fresh_only,
            "clock": "time.monotonic (CLOCK_MONOTONIC)",
            "decoder": "mfp-beta-fw mfp_can.h + mfp_wire.c",
        }
        for f in (self._csv_f, self._can_f, self._mark_f):
            try:
                f.flush()
                os.fsync(f.fileno())
                f.close()
            except Exception:
                pass
        try:
            with open(os.path.join(self.run_dir, "run_info.json"), "w") as f:
                json.dump(info, f, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------ CAN side
    def _open_bus(self):
        try:
            import can
            kw = {"interface": self.can_interface, "channel": self.can_channel}
            if self.can_interface in ("slcan", "gs_usb"):
                kw["bitrate"] = self.can_bitrate
            # socketcan bitrate is set by `ip link set can0 type can bitrate 250000`
            kw["can_filters"] = [{"can_id": 0x18FF0000, "can_mask": 0x1FFF0000, "extended": True}]
            return can.Bus(**kw)
        except Exception as exc:
            self.error = f"CAN open failed: {exc}"
            self._events.append((self.clock(), time.time(), f"CAN_OPEN_FAILED:{exc}"))
            return None

    def _can_loop(self):
        bus = self._bus
        chan = self.can_channel if isinstance(self.can_channel, str) and \
            not self.can_channel.startswith("/") else "can0"
        while not self._stop_evt.is_set():
            try:
                msg = bus.recv(timeout=0.2)
            except Exception as exc:
                self.can_errors += 1
                self.error = f"CAN recv: {exc}"
                time.sleep(0.2)
                continue
            if msg is None or getattr(msg, "is_error_frame", False):
                continue
            mono, wall = self.clock(), time.time()
            data = bytes(msg.data)
            ide = msg.is_extended_id
            cid = f"{msg.arbitration_id:08X}" if ide else f"{msg.arbitration_id:03X}"
            try:
                self._can_f.write(f"({wall:.6f}) {chan} {cid}#{data.hex().upper()}\n")
            except OSError as exc:
                self.error = f"can log write: {exc}"
            self.can_frames += 1
            if not ide:
                continue
            dec = mfp.decode(msg.arbitration_id, data)
            if dec is None:
                continue
            name, sa, fields = dec
            if self.mfp_sa is not None and sa != self.mfp_sa:
                continue
            with self._mfp_lock:
                self._latest[name] = (mono, sa, fields)

    # ------------------------------------------------------------ writer
    def _writer_loop(self):
        last_row_mono = 0.0
        last_flush = self.clock()
        mark_path = os.path.join(self.out_root, "MARK")
        stop_path = os.path.join(self.out_root, "STOP")
        while True:
            stopping = self._stop_evt.wait(0.05)
            for path, kind in ((mark_path, "MARK"), (stop_path, "STOP_FILE")):
                if os.path.exists(path):
                    try:
                        with open(path) as f:
                            label = _safe(f.read().strip()) or kind
                        os.remove(path)
                    except OSError:
                        label = kind
                    if kind == "MARK":
                        self.mark(label)
                    else:
                        self._events.append((self.clock(), time.time(), "STOP_REQUESTED"))
                        threading.Thread(target=self.stop, daemon=True).start()
            while self._events:
                self._write_event(*self._events.popleft())
            while self._q:
                s = self._q.popleft()
                if self.fresh_only and not s[3] and (s[0] - last_row_mono) < self.heartbeat_s:
                    continue
                self._write_sample(s)
                last_row_mono = s[0]
            now = self.clock()
            if now - last_flush >= 1.0 or stopping:
                last_flush = now
                for f in (self._csv_f, self._can_f, self._mark_f):
                    try:
                        f.flush()
                    except OSError as exc:
                        self.error = f"flush: {exc}"
            if stopping and not self._q and not self._events:
                return

    def _snapshot(self):
        with self._mfp_lock:
            return dict(self._latest)

    def _write_event(self, mono, wall, text):
        iso = datetime.fromtimestamp(wall).isoformat(timespec="milliseconds")
        try:
            self._mark_f.write(f"{text}\tmono={mono:.6f}\twall={wall:.6f}\t{iso}\n")
            row = {k: "" for k in CSV_FIELDS}
            row.update(event=text, mono_s=f"{mono:.6f}", wall_epoch=f"{wall:.6f}", wall_iso=iso,
                       flow_dropped=self.dropped, can_frames=self.can_frames,
                       can_errors=self.can_errors)
            self._csv.writerow([row[k] for k in CSV_FIELDS])
            # candump comment-free: put a marker as a non-frame line is not valid
            # candump, so markers live in markers.txt + CSV only.
        except OSError as exc:
            self.error = f"event write: {exc}"

    def _write_sample(self, s):
        mono, wall, seq, fresh, tot_l, flow_lps, temp_f = s
        snap = self._snapshot()
        row = {k: "" for k in CSV_FIELDS}
        row.update(
            event="", mono_s=f"{mono:.6f}", wall_epoch=f"{wall:.6f}",
            wall_iso=datetime.fromtimestamp(wall).isoformat(timespec="milliseconds"),
            sample_seq=seq, fresh=int(bool(fresh)),
            totalizer_l=_fmt(tot_l), totalizer_gal=_fmt(None if tot_l is None else tot_l * L_TO_GAL),
            flow_lps=_fmt(flow_lps, 5), flow_gpm=_fmt(None if flow_lps is None else flow_lps * LPS_TO_GPM, 3),
            picomag_temp_f=_fmt(temp_f, 1),
            flow_dropped=self.dropped, can_frames=self.can_frames, can_errors=self.can_errors,
        )
        age_col = {"LEVEL": "level_age_s", "FAST": "fast_age_s", "ATTITUDE": "attitude_age_s",
                   "HEALTH_A": "health_a_age_s", "HEALTH_B": "health_b_age_s",
                   "HALL_FAULT": "hall_age_s"}
        for kind, (fmono, sa, fields) in snap.items():
            row["mfp_sa"] = f"0x{sa:02X}"
            row[age_col[kind]] = f"{mono - fmono:.3f}"
            if kind == "LEVEL":
                row.update(level_vol_gal=_fmt(fields["level_vol_gal"], 1),
                           level_in=_fmt(fields["level_in"], 3),
                           level_flow_gpm=_fmt(fields["level_flow_gpm"], 1),
                           mfp_status=f"0x{fields['status']:02X}",
                           mfp_status_text=fields["status_text"], level_seq=fields["seq"])
            elif kind == "HALL_FAULT":
                row["hall_last"] = (f"s{fields['hall_strip']}/n{fields['hall_sensor']}/"
                                    f"{fields['hall_code_text']}/{'ACT' if fields['hall_active'] else 'CLR'}")
                row["hall_n_active"] = fields["hall_n_active"]
            elif "error" not in fields:
                for k, v in fields.items():
                    if k in row:
                        row[k] = _fmt(v, 3)
        try:
            self._csv.writerow([row[k] for k in CSV_FIELDS])
            self.rows += 1
        except OSError as exc:  # disk full etc.: stop writing, keep counting
            self.error = f"csv write: {exc}"


def _safe(label):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(label))[:60]


# ---------------------------------------------------------------- dashboard glue
def maybe_create_from_config(config):
    """Return a started TankTestLogger if enabled in config/env, else None.
    Called once at dashboard start-up; any failure returns None (logger off)."""
    enabled = bool(getattr(config, "TANK_TEST_LOGGER_ENABLED", False)) or \
        os.environ.get("BBB_TANK_TEST_LOGGER", "") in ("1", "true", "yes")
    if not enabled:
        return None
    try:
        lg = TankTestLogger(
            out_root=getattr(config, "TANK_TEST_LOG_DIR", "/home/pi/tank_test_logs"),
            can_interface=getattr(config, "TANK_TEST_CAN_INTERFACE", "socketcan"),
            can_channel=getattr(config, "TANK_TEST_CAN_CHANNEL", "can0"),
            can_bitrate=getattr(config, "TANK_TEST_CAN_BITRATE", 250000),
            mfp_sa=getattr(config, "TANK_TEST_MFP_SA", None),
        )
        lg.start(os.environ.get("BBB_TANK_TEST_LABEL", ""))
        return lg
    except Exception as exc:
        print(f"tank test logger disabled: {exc}", flush=True)
        return None


# ---------------------------------------------------------------- CLI
def _cli(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="BBB tank-test logger (CAN side / sim). "
                                 "Never opens IO-Link; real flow comes from the dashboard hook.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="log MFP CAN (and optional simulated flow) until Ctrl-C/STOP")
    r.add_argument("--out", default="/home/pi/tank_test_logs")
    r.add_argument("--label", default="")
    r.add_argument("--interface", default="socketcan", help="socketcan | slcan | gs_usb | virtual")
    r.add_argument("--channel", default="can0", help="can0, vcan0, /dev/ttyACM0, ...")
    r.add_argument("--bitrate", type=int, default=250000)
    r.add_argument("--duration", type=float, default=0, help="seconds, 0 = until stopped")
    r.add_argument("--sim-flow", action="store_true", help="feed a mock Picomag at 12 Hz (PC testing)")
    m = sub.add_parser("mark", help="drop a marker into the running logger (any mode)")
    m.add_argument("--out", default="/home/pi/tank_test_logs")
    m.add_argument("label", nargs="?", default="MARK")
    s = sub.add_parser("stop", help="ask the running logger to stop (any mode)")
    s.add_argument("--out", default="/home/pi/tank_test_logs")
    a = ap.parse_args(argv)

    if a.cmd in ("mark", "stop"):
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, a.cmd.upper()), "w") as f:
            f.write(getattr(a, "label", "") or "")
        print(f"requested {a.cmd} via {a.out}/{a.cmd.upper()}")
        return 0

    lg = TankTestLogger(a.out, can_interface=a.interface, can_channel=a.channel,
                        can_bitrate=a.bitrate)
    run_dir = lg.start(a.label)
    print(f"logging to {run_dir}  (SIGUSR1 = marker, Ctrl-C/SIGTERM = stop)", flush=True)
    done = threading.Event()
    signal.signal(signal.SIGUSR1, lambda *_: lg.mark("SIGUSR1"))
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    signal.signal(signal.SIGINT, lambda *_: done.set())
    t0 = time.monotonic()
    tot = 0.0
    while not done.is_set() and lg.running:
        if a.sim_flow:
            flow = 6.0  # L/s
            tot += flow / 12.0
            lg.submit_flow(time.monotonic(), time.time(), tot, flow, 68.0, True)
        if a.duration and time.monotonic() - t0 >= a.duration:
            break
        done.wait(1 / 12.0)
    lg.stop()
    print(f"stopped: rows={lg.rows} can_frames={lg.can_frames} dropped={lg.dropped} "
          f"error={lg.error!r}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
