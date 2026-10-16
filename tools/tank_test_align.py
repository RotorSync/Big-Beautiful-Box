#!/usr/bin/env python3
"""Offline: align Picomag total gallons with MFP level through a tank table.

    python3 tools/tank_test_align.py RUN_DIR/flow_mfp.csv --table tank.csv \
        [--initial-gal G] [--level-col level_in] [--ullage-height H] [--out DIR]

For every distinct MFP LEVEL frame (deduplicated by level_seq) the frame's
receive time is mono_s - level_age_s; the Picomag totalizer is linearly
interpolated to THAT instant (same monotonic clock), so the 1 Hz LEVEL rate
and the 9-12 Hz Picomag rate do not alias.

  mfp_table_gal  = tank_table(level_in)          (your table, CSV level,gallons)
  meter_gal      = initial_gal + (totalizer_gal - totalizer_gal_at_start)
  error_gal      = mfp_table_gal - meter_gal     (+ = MFP reads high)
initial_gal defaults to the MFP's own first reading (so error starts at 0 and
the test measures accumulated error vs metered volume). Pass --initial-gal if
the starting volume is known independently (e.g. empty tank = 0).
The probe's own corrected volume (level_vol_gal) is compared too.
--ullage-height H converts a distance-from-top table (e.g. Mopeka) into height.
Outputs: align_error.csv, align_summary.txt, align_error.png (if matplotlib).

BUILD A TANK TABLE from a fill run (no table needed yet):

    python3 tools/tank_test_align.py RUN_DIR/flow_mfp.csv --build-table tank.csv \
        [--initial-gal 0] [--bin-in 0.5] [--min-per-bin 2] [--allow-not-ok]

Pairs every LEVEL frame's level_in with the metered volume at that instant
(initial_gal + totalizer delta; default initial 0 = fill from empty), bins by
level, takes the median level and median gallons per bin, and forces gallons
to be non-decreasing in level (pool-adjacent-violators). Frames whose status
lacks the OK bit are skipped unless --allow-not-ok. Output CSV:
level_in,gallons,n  -- usable directly as --table for later runs.
"""
import argparse
import bisect
import csv
import os
import statistics
import sys


def load_table(path, ullage_height=None):
    pts = []
    with open(path) as f:
        for row in csv.reader(f):
            try:
                lv, gal = float(row[0]), float(row[1])
            except (ValueError, IndexError):
                continue
            if ullage_height is not None:
                lv = ullage_height - lv
            pts.append((lv, gal))
    pts.sort()
    if len(pts) < 2:
        raise SystemExit(f"tank table {path}: need >= 2 numeric rows")
    return pts


def interp_xy(xs, ys, x):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_right(xs, x)
    return ys[i - 1] + (ys[i] - ys[i - 1]) * (x - xs[i - 1]) / (xs[i] - xs[i - 1])


def f_or_none(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--table", default=None, help="tank table CSV (level_in,gallons)")
    ap.add_argument("--build-table", default=None, metavar="OUT_CSV",
                    help="build a tank table from this fill run instead of comparing")
    ap.add_argument("--bin-in", type=float, default=0.5, help="build-table level bin width, in")
    ap.add_argument("--min-per-bin", type=int, default=2)
    ap.add_argument("--allow-not-ok", action="store_true")
    ap.add_argument("--initial-gal", type=float, default=None)
    ap.add_argument("--level-col", default="level_in")
    ap.add_argument("--ullage-height", type=float, default=None)
    ap.add_argument("--max-level-age", type=float, default=2.5, help="s; staler LEVEL is skipped")
    ap.add_argument("--bin-gal", type=float, default=50.0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = a.out or os.path.dirname(os.path.abspath(a.csv))
    os.makedirs(out, exist_ok=True)

    if not a.table and not a.build_table:
        ap.error("need --table (compare) or --build-table OUT (create)")

    flow_t, flow_g, levels, seen = [], [], [], set()
    with open(a.csv) as f:
        for r in csv.DictReader(f):
            if r["event"]:
                continue
            t, g = f_or_none(r["mono_s"]), f_or_none(r["totalizer_gal"])
            if t is not None and g is not None:
                flow_t.append(t)
                flow_g.append(g)
            lv, age, seq = f_or_none(r.get(a.level_col)), f_or_none(r["level_age_s"]), r["level_seq"]
            if lv is None or age is None or seq == "" or age > a.max_level_age:
                continue
            ft = round(t - age, 3)
            key = (seq, round(ft, 1))
            if key in seen:
                continue
            seen.add(key)
            levels.append((ft, lv, f_or_none(r["level_vol_gal"]), r["mfp_status_text"]))
    if len(flow_t) < 2 or not levels:
        raise SystemExit("not enough flow rows or LEVEL frames in the CSV")
    levels = [x for x in levels if flow_t[0] <= x[0] <= flow_t[-1]]
    levels.sort()
    tot0 = interp_xy(flow_t, flow_g, levels[0][0])
    if a.build_table:
        return build_table(a, levels, flow_t, flow_g, tot0)
    table = load_table(a.table, a.ullage_height)
    tx, ty = [p[0] for p in table], [p[1] for p in table]
    init = a.initial_gal if a.initial_gal is not None else interp_xy(tx, ty, levels[0][1])

    rows = []
    for ft, lv, probe_gal, st in levels:
        meter = init + interp_xy(flow_t, flow_g, ft) - tot0
        tab = interp_xy(tx, ty, lv)
        rows.append({"mono_s": f"{ft:.3f}", "level_in": f"{lv:.3f}", "meter_gal": f"{meter:.2f}",
                     "mfp_table_gal": f"{tab:.2f}", "error_gal": f"{tab - meter:.2f}",
                     "error_pct_of_meter": f"{100 * (tab - meter) / meter:.3f}" if meter > 1 else "",
                     "probe_vol_gal": "" if probe_gal is None else f"{probe_gal:.1f}",
                     "probe_error_gal": "" if probe_gal is None else f"{probe_gal - meter:.2f}",
                     "status": st})
    path = os.path.join(out, "align_error.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    errs = [float(r["error_gal"]) for r in rows]
    meters = [float(r["meter_gal"]) for r in rows]
    lines = [f"LEVEL frames aligned: {len(rows)}", f"initial_gal: {init:.2f} "
             f"({'given' if a.initial_gal is not None else 'from first MFP reading'})",
             f"metered span: {meters[0]:.1f} -> {meters[-1]:.1f} gal ({meters[-1] - meters[0]:.1f} gal)",
             f"error_gal (table - meter): mean {statistics.mean(errs):+.2f}  "
             f"min {min(errs):+.2f}  max {max(errs):+.2f}  final {errs[-1]:+.2f}"]
    if meters[-1] - meters[0] > 1:
        lines.append(f"final error as % of metered delivery: "
                     f"{100 * errs[-1] / (meters[-1] - meters[0]):+.2f} %")
    lines.append(f"\nerror by metered volume (bin {a.bin_gal:g} gal):")
    bins = {}
    for m, e in zip(meters, errs):
        bins.setdefault(int(m // a.bin_gal), []).append(e)
    for b in sorted(bins):
        v = bins[b]
        lines.append(f"  {b * a.bin_gal:7.0f}-{(b + 1) * a.bin_gal:<7.0f} n={len(v):4d} "
                     f"mean {statistics.mean(v):+7.2f}  max|e| {max(abs(x) for x in v):6.2f}")
    txt = "\n".join(lines)
    with open(os.path.join(out, "align_summary.txt"), "w") as f:
        f.write(txt + "\n")
    print(txt)
    print(f"\nwrote {path}")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.plot(meters, errs, ".-", label="tank table(level_in) - meter")
        pe = [(float(r["meter_gal"]), float(r["probe_error_gal"])) for r in rows if r["probe_error_gal"]]
        if pe:
            ax.plot([p[0] for p in pe], [p[1] for p in pe], ".", alpha=0.5, label="probe level_vol_gal - meter")
        ax.axhline(0, color="k", lw=0.5)
        ax.set_xlabel("metered volume in tank (gal)")
        ax.set_ylabel("error (gal)")
        ax.set_title("MFP vs Picomag tank fill")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(out, "align_error.png"), dpi=110)
        print(f"wrote {os.path.join(out, 'align_error.png')}")
    except ImportError:
        print("matplotlib not installed; no plot")
    return 0


def _pava(ys, ws):
    """Weighted pool-adjacent-violators: non-decreasing fit of ys."""
    blocks = []  # [mean, weight, count]
    for y, w in zip(ys, ws):
        blocks.append([y, w, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            y2, w2, c2 = blocks.pop()
            y1, w1, c1 = blocks.pop()
            blocks.append([(y1 * w1 + y2 * w2) / (w1 + w2), w1 + w2, c1 + c2])
    out = []
    for y, _w, c in blocks:
        out.extend([y] * c)
    return out


def build_table(a, levels, flow_t, flow_g, tot0):
    init = a.initial_gal if a.initial_gal is not None else 0.0
    pairs, skipped = [], 0
    for ft, lv, _probe, st in levels:
        if not a.allow_not_ok and "OK" not in (st or "").split("|"):
            skipped += 1
            continue
        pairs.append((lv, init + interp_xy(flow_t, flow_g, ft) - tot0))
    bins = {}
    for lv, g in pairs:
        bins.setdefault(int(lv // a.bin_in), []).append((lv, g))
    rows = []
    for b in sorted(bins):
        v = bins[b]
        if len(v) < a.min_per_bin:
            continue
        rows.append([statistics.median(x[0] for x in v), statistics.median(x[1] for x in v), len(v)])
    if len(rows) < 2:
        raise SystemExit(f"only {len(rows)} usable level bins (pairs={len(pairs)}, "
                         f"skipped not-OK={skipped}); lower --min-per-bin/--bin-in or check the run")
    fixed = _pava([r[1] for r in rows], [r[2] for r in rows])
    adjusted = sum(1 for r, f in zip(rows, fixed) if abs(r[1] - f) > 1e-9)
    with open(a.build_table, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["level_in", "gallons", "n"])
        for r, g in zip(rows, fixed):
            w.writerow([f"{r[0]:.3f}", f"{g:.2f}", r[2]])
    print(f"tank table: {len(rows)} bins of {a.bin_in:g} in, level {rows[0][0]:.2f}-{rows[-1][0]:.2f} in, "
          f"{fixed[0]:.1f}-{fixed[-1]:.1f} gal (initial {init:g} gal); LEVEL pairs {len(pairs)}, "
          f"skipped not-OK {skipped}, monotonic-adjusted bins {adjusted}")
    print(f"wrote {a.build_table}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
