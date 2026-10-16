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
    ap.add_argument("--table", required=True)
    ap.add_argument("--initial-gal", type=float, default=None)
    ap.add_argument("--level-col", default="level_in")
    ap.add_argument("--ullage-height", type=float, default=None)
    ap.add_argument("--max-level-age", type=float, default=2.5, help="s; staler LEVEL is skipped")
    ap.add_argument("--bin-gal", type=float, default=50.0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = a.out or os.path.dirname(os.path.abspath(a.csv))
    os.makedirs(out, exist_ok=True)

    table = load_table(a.table, a.ullage_height)
    tx, ty = [p[0] for p in table], [p[1] for p in table]

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


if __name__ == "__main__":
    sys.exit(main())
