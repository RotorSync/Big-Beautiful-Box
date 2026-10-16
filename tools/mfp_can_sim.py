#!/usr/bin/env python3
"""Simulated MFP probe on a python-can bus (vcan0, or python-can 'virtual').

Emits the beta-firmware frame set at the beta rates (DESIGN/bring-up record):
LEVEL 1 Hz, FAST 5 Hz, ATTITUDE 1 Hz, HEALTH_A/B 0.2 Hz, plus one HALL_FAULT
raise+clear, from a caller-supplied true-volume function and tank table.
Also replays a candump -l log (`replay FILE`) onto a bus at original pacing.

PC/bench use only. Never run against a field bus.
"""
import argparse
import bisect
import math
import os
import random
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from src import mfp_can_decode as mfp  # noqa: E402


def load_table(path):
    """CSV tank table -> sorted list of (level_in, gallons). First two numeric
    columns; header optional."""
    pts = []
    with open(path) as f:
        for line in f:
            parts = [p.strip() for p in line.split(",")]
            try:
                pts.append((float(parts[0]), float(parts[1])))
            except (ValueError, IndexError):
                continue
    pts.sort()
    return pts


def interp(pts, x, xi=0, yi=1):
    xs = [p[xi] for p in pts]
    ys = [p[yi] for p in pts]
    if xs[0] > xs[-1]:
        xs, ys = xs[::-1], ys[::-1]
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_right(xs, x)
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


class MfpSim:
    def __init__(self, bus, table, volume_fn, sa=0x10, gain_err=0.01, offset_gal=0.0,
                 noise_in=0.02, hall_fault_at=None, seed=1):
        self.bus, self.table, self.volume_fn, self.sa = bus, table, volume_fn, sa
        self.gain_err, self.offset_gal, self.noise_in = gain_err, offset_gal, noise_in
        self.hall_fault_at = hall_fault_at
        self.rng = random.Random(seed)
        self.stop_evt = threading.Event()
        self.sent = 0
        self._t = None

    def _send(self, base, data):
        import can
        self.bus.send(can.Message(arbitration_id=base | self.sa, data=data, is_extended_id=True))
        self.sent += 1

    def _level_in(self, gal_true):
        # Probe reading: tank-table inverse of a slightly wrong volume.
        gal_meas = gal_true * (1 + self.gain_err) + self.offset_gal
        lvl = interp(self.table, gal_meas, xi=1, yi=0)
        return max(0.0, lvl + self.rng.gauss(0, self.noise_in))

    def run(self):
        t0 = time.monotonic()
        k = 0
        seq = 0
        hall_state = 0
        while not self.stop_evt.is_set():
            t = time.monotonic() - t0
            gal = self.volume_fn(t)
            lvl = self._level_in(gal)
            vol_probe = interp(self.table, lvl)
            self._send(mfp.ID_FAST, mfp.encode_fast(vol_probe, lvl + 0.35, 0.05))
            if k % 5 == 0:
                st = 0x01 | 0x40 | 0x80  # OK | SG_DEFAULT | NO_ATTITUDE (bench beta)
                if hall_state == 1:
                    st = (st & ~0x01) | 0x08
                self._send(mfp.ID_LEVEL, mfp.encode_level(vol_probe, lvl, None, st, seq))
                seq = (seq + 1) & 0xFF
                self._send(mfp.ID_ATTITUDE, mfp.encode_attitude(None, None, None, None))
            if k % 25 == 0:
                self._send(mfp.ID_HEALTH_A, bytes([0x08 if hall_state == 1 else 0, 0, 0, 0, 0, 0, 0, seq]))
                self._send(mfp.ID_HEALTH_B, bytes([98, 2, int(t // 60) & 0xFF, 0, 3, 17, 0, 0]))
            if self.hall_fault_at is not None and hall_state == 0 and t >= self.hall_fault_at:
                hall_state = 1
                self._send(mfp.ID_HALL_FAULT, bytes([1, 4, 0x04, 0x05, 1, 0, 1, 3]))
            elif hall_state == 1 and self.hall_fault_at is not None and t >= self.hall_fault_at + 3:
                hall_state = 2
                self._send(mfp.ID_HALL_FAULT, bytes([1, 4, 0x04, 0x04, 1, 0, 0, 0]))
            k += 1
            nxt = t0 + k * 0.2
            self.stop_evt.wait(max(0.0, nxt - time.monotonic()))

    def start(self):
        self._t = threading.Thread(target=self.run, name="mfp-sim", daemon=True)
        self._t.start()

    def stop(self):
        self.stop_evt.set()
        if self._t:
            self._t.join(2)


def replay(bus, path, speed=1.0):
    """Replay a candump -l log onto bus at original pacing."""
    import can
    prev = None
    n = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line.startswith("("):
                continue
            ts = float(line[1:line.index(")")])
            frame = line.split()[-1]
            cid, data = frame.split("#")
            if prev is not None:
                time.sleep(max(0.0, (ts - prev) / speed))
            prev = ts
            bus.send(can.Message(arbitration_id=int(cid, 16), data=bytes.fromhex(data),
                                 is_extended_id=len(cid) > 3))
            n += 1
    return n


def main():
    import can
    ap = argparse.ArgumentParser()
    ap.add_argument("--interface", default="socketcan")
    ap.add_argument("--channel", default="vcan0")
    ap.add_argument("--sa", type=lambda s: int(s, 0), default=0x10)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fill")
    f.add_argument("--table", required=True)
    f.add_argument("--gpm", type=float, default=100.0)
    f.add_argument("--start-gal", type=float, default=50.0)
    f.add_argument("--seconds", type=float, default=60.0)
    r = sub.add_parser("replay")
    r.add_argument("file")
    a = ap.parse_args()
    bus = can.Bus(interface=a.interface, channel=a.channel)
    try:
        if a.cmd == "replay":
            print(f"replayed {replay(bus, a.file)} frames")
        else:
            sim = MfpSim(bus, load_table(a.table), lambda t: a.start_gal + a.gpm * t / 60.0, sa=a.sa)
            sim.start()
            time.sleep(a.seconds)
            sim.stop()
            print(f"sent {sim.sent} frames")
    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
