"""Tests for rotorlink.config trailer/mDNS name resolution, incl. the
unconfigured fallback (an unassigned box must advertise a clear, unique
'TrailerSync-Unconfigured-<serial>' rather than a bare hostname)."""
import json

from rotorlink import config


def test_unconfigured_name_strips_trailersync_prefix(monkeypatch):
    monkeypatch.setattr(config.socket, "gethostname", lambda: "trailersync-sn007")
    assert config.unconfigured_name() == "TrailerSync-Unconfigured-sn007"


def test_unconfigured_name_uses_full_host_when_no_prefix(monkeypatch):
    monkeypatch.setattr(config.socket, "gethostname", lambda: "pi-box-42")
    assert config.unconfigured_name() == "TrailerSync-Unconfigured-pi-box-42"


def test_trailer_name_falls_back_to_unconfigured_when_unassigned(monkeypatch, tmp_path):
    # No BLE name file, no mopeka display_name -> unconfigured marker.
    monkeypatch.setattr(config, "BLE_NAME_FILE", str(tmp_path / "missing-ble.json"))
    monkeypatch.setattr(config, "MOPEKA_CONFIG_PATH", str(tmp_path / "missing-mopeka.json"))
    monkeypatch.setattr(config.socket, "gethostname", lambda: "trailersync-sn007")
    assert config.trailer_name() == "TrailerSync-Unconfigured-sn007"


def test_trailer_name_prefers_assigned_ble_name(monkeypatch, tmp_path):
    ble = tmp_path / "ble.json"
    ble.write_text(json.dumps({"name": "TrailerSync-TR7"}))
    monkeypatch.setattr(config, "BLE_NAME_FILE", str(ble))
    assert config.trailer_name() == "TrailerSync-TR7"
