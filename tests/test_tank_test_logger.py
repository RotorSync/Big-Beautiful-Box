"""Tank-test logger: decode, off-by-default, non-blocking handoff, outputs.
No python-can required (fake bus), so this runs in CI as-is."""
import ast
import csv
import os
import struct
import time
import types
from pathlib import Path

import config
from src import mfp_can_decode as mfp
from src.tank_test_logger import TankTestLogger, maybe_create_from_config

ROOT = Path(__file__).resolve().parents[1]


class FakeMsg:
    def __init__(self, arb, data, ext=True):
        self.arbitration_id, self.data, self.is_extended_id = arb, data, ext
        self.is_error_frame = False


class FakeBus:
    def __init__(self, frames):
        self.frames = list(frames)

    def recv(self, timeout=None):
        if self.frames:
            return self.frames.pop(0)
        time.sleep(0.01)
        return None

    def shutdown(self):
        pass


def test_level_decode_matches_mfp_wire_layout():
    # mfp_wire_level: u16 vol*10, u16 level*1000, i16 flow*10, status&0xFF, seq
    d = struct.pack("<HHhBB", 4567, 23456, -15, 0xC1, 7)
    name, sa, f = mfp.decode(0x18FF1010, d)
    assert (name, sa) == ("LEVEL", 0x10)
    assert f["level_vol_gal"] == 456.7 and f["level_in"] == 23.456
    assert f["level_flow_gpm"] == -1.5 and f["seq"] == 7
    assert f["status_text"] == "OK|SG_DEFAULT|NO_ATTITUDE"


def test_sentinels_and_other_frames():
    _, _, f = mfp.decode(0x18FF1010, mfp.encode_level(None, 10.0, None, 1, 0))
    assert f["level_vol_gal"] is None and f["level_flow_gpm"] is None
    _, _, a = mfp.decode(0x18FF1210, mfp.encode_attitude(None, 1.25, -3.5, 2.0))
    assert a["pitch_deg"] is None and a["roll_deg"] == 1.25 and a["mfp_temp_c"] == -3.5
    _, _, h = mfp.decode(0x18FF1510, bytes([2, 0xFF, 0x10, 0x05, 3, 0, 1, 9]))
    assert h["hall_code_text"] == "STRIP_LOST" and h["hall_active"] and h["hall_excluded"] and not h["hall_test"]
    assert h["hall_count"] == 3 and h["hall_sensor"] == 0xFF
    _, _, b = mfp.decode(0x18FF1410, bytes([98, 2, 0x10, 0x01, 3, 17, 130, 0]))
    assert b["uptime_min"] == 0x110 and b["tec"] == 130
    assert mfp.decode(0x18EF1010, bytes(8)) is None          # config service: not telemetry
    assert mfp.decode(0x18FF1010, bytes(4))[2]["error"] == "dlc=4"


def test_logger_off_by_default(monkeypatch):
    monkeypatch.delenv("BBB_TANK_TEST_LOGGER", raising=False)
    assert config.TANK_TEST_LOGGER_ENABLED is False
    assert maybe_create_from_config(config) is None
    lg = TankTestLogger("/nonexistent", can_interface=None)
    assert lg.submit_flow(1, 2, 3, 4) is False             # not started: no-op


def test_full_queue_drops_and_counts_never_blocks(tmp_path):
    lg = TankTestLogger(str(tmp_path), can_interface=None, queue_max=3)
    lg.running = True                                      # writer not started
    t = time.perf_counter()
    res = [lg.submit_flow(i, i, 0, 0) for i in range(1000)]
    assert time.perf_counter() - t < 0.5
    assert res.count(True) == 3 and lg.dropped == 997


def test_run_outputs(tmp_path):
    frames = [FakeMsg(0x18FF1010, mfp.encode_level(300.0, 20.0, None, 0xC1, 5)),
              FakeMsg(0x18FF1110, mfp.encode_fast(301.0, 20.4, 0.05)),
              FakeMsg(0x123, b"\x01\x02", ext=False)]
    lg = TankTestLogger(str(tmp_path), can_bus=FakeBus(frames), can_channel="can0")
    run = lg.start("unit")
    time.sleep(0.2)
    lg.submit_flow(time.monotonic(), time.time(), 10.0, 1.0, 65.3, True)
    lg.mark("half")
    (tmp_path / "MARK").write_text("filemark")
    time.sleep(0.3)
    lg.submit_flow(time.monotonic(), time.time(), 11.0, 1.0, 65.3, False)  # stale: heartbeat-gated
    lg.stop()
    rows = list(csv.DictReader(open(os.path.join(run, "flow_mfp.csv"))))
    events = [r["event"] for r in rows if r["event"]]
    assert events[0] == "START:unit" and "MARK:half" in events and "MARK:filemark" in events
    assert events[-1] == "STOP"
    data = [r for r in rows if not r["event"]]
    assert len(data) == 1
    r = data[0]
    assert r["totalizer_l"] == "10.0000" and r["flow_gpm"] == "15.850"
    assert r["level_in"] == "20.000" and r["level_seq"] == "5" and r["mfp_sa"] == "0x10"
    assert float(r["level_age_s"]) >= 0 and r["z_null_in_raw"] == "20.400"
    log = open(os.path.join(run, "can_raw.log")).read().splitlines()
    assert len(log) == 3 and log[0].endswith(" can0 18FF1010#" + frames[0].data.hex().upper())
    assert log[2].endswith(" can0 123#0102")
    marks = open(os.path.join(run, "markers.txt")).read()
    assert "START:unit" in marks and "STOP" in marks
    assert os.path.exists(os.path.join(run, "run_info.json"))


def test_dashboard_hook_is_after_shutoff_decision_and_guarded():
    src = (ROOT / "dashboard.py").read_text()
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "flow_control_loop")
    body = ast.get_source_segment(src, fn)
    i_dec = body.index("_flow_control_process_sample(")
    i_hook = body.index("tank_test_logger.submit_flow(")
    assert i_dec < i_hook
    assert "if tank_test_logger is not None:" in body
    assert "iolhat" not in (ROOT / "src" / "tank_test_logger.py").read_text()


def test_align_script(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("align", ROOT / "tools" / "tank_test_align.py")
    align = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(align)
    table = tmp_path / "t.csv"
    table.write_text("level_in,gallons\n0,0\n100,1000\n")
    from src.tank_test_logger import CSV_FIELDS
    rows = []
    for k in range(20):                      # 10 gal/s metered; MFP reads +2 %
        t = float(k)
        tot_gal = 10.0 * t
        level = (100 + tot_gal) * 1.02 / 10.0
        r = {f: "" for f in CSV_FIELDS}
        r.update(mono_s=t, totalizer_gal=tot_gal, level_in=level, level_age_s=0.0,
                 level_seq=k, level_vol_gal="", mfp_status_text="OK")
        rows.append(r)
    p = tmp_path / "flow_mfp.csv"
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    assert align.main([str(p), "--table", str(table), "--initial-gal", "100", "--out", str(tmp_path)]) == 0
    out = list(csv.DictReader(open(tmp_path / "align_error.csv")))
    assert abs(float(out[-1]["error_gal"]) - 0.02 * 290) < 0.05
