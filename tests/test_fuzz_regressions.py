"""Regression tests from the Oct-28 atheris fuzz campaign (inputs in
tests/fuzz_regressions/). Every disk/network parser must degrade, never raise."""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_maintenance_auth import install_bumble_stubs


class _Shim:
    def setitem(self, mapping, key, value):
        mapping[key] = value


install_bumble_stubs(_Shim())
sys.path.insert(0, str(HERE.parent))
import rotorsync_bumble as rb  # noqa: E402
from rotorlink import config as rlcfg, config_handler as ch  # noqa: E402
from src import connection_registry as cr, maintenance_protocol as mp, mopeka_converter as mc  # noqa: E402

CORPUS = HERE / "fuzz_regressions"


def test_sensor_offsets_short_row_does_not_raise(tmp_path):
    mc.load_sensor_offsets(str(CORPUS / "mopeka_csv_shortrow"))
    p = tmp_path / "s.csv"
    p.write_text("Mopeka ID,Height Offset,Tank,Trailer\nABC\n")
    mc.load_sensor_offsets(str(p))
    mc.load_sensor_calibration_profiles(str(p), {})


def test_mopeka_csv_non_utf8_does_not_raise(tmp_path):
    p = tmp_path / "c.csv"
    p.write_bytes(b"\xc6Tank Level (in),Gallons\n\xff1,2\n")
    mc.load_calibration(str(p))
    mc.load_sensor_offsets(str(p))


def test_calibration_short_row(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("Tank Level (in),Gallons\n5\n4,100\n")
    mc.load_calibration(str(p))
    assert mc._calibration_table == [(4.0, 100.0)]


def test_sensor_csv_truncated_both_parsers(tmp_path, monkeypatch):
    for data in (b"", (CORPUS / "sensor_csv_empty").read_bytes(), b"\n\n", b"\xff\xfe\n\n\n\n"):
        p = tmp_path / "sensors.csv"
        p.write_bytes(data)
        monkeypatch.setattr(rlcfg, "SENSOR_CSV_PATH", str(p))
        monkeypatch.setattr(rb, "SENSOR_CSV_PATH", str(p))
        assert ch._load_sensor_csv() == []
        assert rb.load_sensor_csv() == []


def test_sensor_csv_still_parses_valid(tmp_path, monkeypatch):
    p = tmp_path / "sensors.csv"
    p.write_text("\n\n\n\nMan,Trailer,Tank\nBob,11,Front\n,11,Back\n")
    monkeypatch.setattr(rlcfg, "SENSOR_CSV_PATH", str(p))
    monkeypatch.setattr(rb, "SENSOR_CSV_PATH", str(p))
    for rows in (ch._load_sensor_csv(), rb.load_sensor_csv()):
        assert [r["Man"] for r in rows] == ["Bob", "Bob"]


def test_corrupt_mopeka_config_falls_back(tmp_path, monkeypatch):
    for data in (b"[1,2]", b"\xff\xfe{", b'{"trailer":', b"null"):
        p = tmp_path / "mopeka_config.json"
        p.write_bytes(data)
        monkeypatch.setattr(rb, "MOPEKA_CONFIG_PATH", str(p))
        monkeypatch.setattr(rlcfg, "MOPEKA_CONFIG_PATH", str(p))
        assert isinstance(rb.load_config(), dict)
        assert isinstance(ch._load_config(), dict)


def test_save_config_is_atomic(tmp_path, monkeypatch):
    p = tmp_path / "mopeka_config.json"
    monkeypatch.setattr(rb, "MOPEKA_CONFIG_PATH", str(p))
    rb.save_config({"trailer": "TR11"})
    assert json.loads(p.read_text()) == {"trailer": "TR11"}
    assert not (tmp_path / "mopeka_config.json.tmp").exists()


def test_connection_log_skips_bad_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_bytes(b'{"ts":5,"event":"a"}\n[1]\n{"ts":"x"}\n\xff\xfe\n{"ts":"7","event":"b"}\n{"ts":NaN}\n')
    out = cr.read_log_since(0, log_path=str(p))
    assert [e["event"] for e in out] == ["a", "b"]


def test_maintenance_decode_frame_non_utf8():
    import pytest
    with pytest.raises(mp.MaintenanceProtocolError):
        mp.decode_frame(b"\x8a")
    with pytest.raises(mp.MaintenanceProtocolError):
        mp.decode_frame("x" * (mp.MAX_FRAME_BYTES + 1))
