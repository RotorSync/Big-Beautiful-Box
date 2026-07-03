"""Tests for src/connection_registry.py — the cross-server (BLE + RotorLink)
connection log/snapshot the app relays to the backend — and the location and
Loc-field additions to the shared fill-history parser."""
import json
import time

import pytest

from src import connection_registry as reg
from src.fill_history import item_from_line


# --- transport classification ------------------------------------------------

def test_ap_subnet_classified_as_wifi_ap():
    assert reg.classify_wifi_peer("10.42.0.17") == "wifi_ap"
    assert reg.classify_wifi_peer("192.168.68.184") == "wifi_lan"
    assert reg.classify_wifi_peer(None) == "wifi_lan"


# --- event log ----------------------------------------------------------------

def test_record_and_read_events(tmp_path):
    log = str(tmp_path / "log.jsonl")
    first = reg.record_event("connect", "ble", peer="AA:BB", log_path=log)
    time.sleep(0.01)
    second = reg.record_event(
        "hello", "wifi_ap", peer="10.42.0.2:5", role="pilot",
        name="Cody", user_id="u1", device="iPad", log_path=log,
    )
    assert first and second
    items = reg.read_log_since(0, log_path=log)
    assert [i["event"] for i in items] == ["connect", "hello"]
    assert items[1]["transport"] == "wifi_ap"
    assert items[1]["name"] == "Cody"
    assert "role" not in items[0]  # None fields are omitted

    # since filter is exclusive of already-seen events
    later = reg.read_log_since(items[0]["ts"], log_path=log)
    assert [i["event"] for i in later] == ["hello"]


def test_event_names_kept_single_line(tmp_path):
    log = str(tmp_path / "log.jsonl")
    reg.record_event("hello", "ble", name="Bad\nGuy|X", log_path=log)
    raw = (tmp_path / "log.jsonl").read_text()
    assert raw.count("\n") == 1
    assert json.loads(raw)["name"] == "Bad Guy|X"


def test_log_prunes_to_recent_lines(tmp_path, monkeypatch):
    log = str(tmp_path / "log.jsonl")
    monkeypatch.setattr(reg, "LOG_MAX_BYTES", 1)  # prune after every append
    monkeypatch.setattr(reg, "LOG_KEEP_LINES", 3)
    for i in range(30):
        reg.record_event("connect", "ble", peer=f"peer-{i}", log_path=log)
    items = reg.read_log_since(0, log_path=log)
    assert len(items) <= 3
    assert items[-1]["peer"] == "peer-29"


def test_read_log_missing_file(tmp_path):
    assert reg.read_log_since(0, log_path=str(tmp_path / "nope.jsonl")) == []


# --- live snapshots -------------------------------------------------------------

def test_snapshot_merge_and_stale(tmp_path):
    paths = {"ble": str(tmp_path / "ble.json"), "wifi": str(tmp_path / "wifi.json")}
    reg.write_snapshot("ble", [{"peer": "AA", "role": "pilot", "name": "Cody"}],
                       snapshot_path=paths["ble"])
    reg.write_snapshot("wifi", [{"peer": "10.42.0.2:5", "transport": "wifi_ap"}],
                       snapshot_path=paths["wifi"])
    merged = reg.read_connections(snapshot_paths=paths)
    assert {c["server"] for c in merged} == {"ble", "wifi"}
    assert not any(c.get("stale") for c in merged)

    # age the wifi snapshot beyond the stale window
    with open(paths["wifi"]) as f:
        snap = json.load(f)
    snap["updated"] = time.time() - 3600
    with open(paths["wifi"], "w") as f:
        json.dump(snap, f)
    merged = reg.read_connections(snapshot_paths=paths)
    wifi = [c for c in merged if c["server"] == "wifi"]
    assert wifi and wifi[0]["stale"] is True


# --- box health ---------------------------------------------------------------

def test_box_health_shape(tmp_path, monkeypatch):
    version = tmp_path / "VERSION"
    version.write_text("V2.26\n")
    adv = tmp_path / "adv.json"
    adv.write_text(json.dumps({"name": "TrailerSync-TR8", "timestamp": time.time() - 5}))
    monkeypatch.setattr(reg, "VERSION_PATH", str(version))
    monkeypatch.setattr(reg, "ADVERTISING_READY_PATH", str(adv))
    monkeypatch.setattr(reg, "_service_state", lambda unit: "active")

    health = reg.box_health()
    assert health["version"] == "V2.26"
    assert health["services"] == {"rotorsync": "active", "rotorlink": "active",
                                  "iol_dashboard": "active"}
    assert health["ble_name"] == "TrailerSync-TR8"
    assert 0 <= health["ble_advertising_age_s"] < 60
    assert health["uptime_s"] > 0


# --- fill-history Loc field -----------------------------------------------------

LOC_LINE = (
    "2026-07-03 09:15:22 | Requested: 60.000 gal | Actual: 65.040 gal"
    " | Diff: +5.040 gal | Auto shutoff | Temp: 71.2F"
    " | Loc: 41.123456,-95.654321,12.5 | Pilot: Cody"
)


def test_fill_item_carries_location():
    item = item_from_line(LOC_LINE)
    assert item["lat"] == 41.123456
    assert item["lon"] == -95.654321
    assert item["lac"] == 12.5
    assert item["pl"] == "Cody"


def test_fill_item_location_without_accuracy():
    item = item_from_line(LOC_LINE.replace(",12.5", ""))
    assert item["lat"] == 41.123456 and item["lac"] is None


def test_fill_item_no_location_is_none():
    item = item_from_line(LOC_LINE.replace(" | Loc: 41.123456,-95.654321,12.5", ""))
    assert item["lat"] is None and item["lon"] is None and item["lac"] is None


def test_fill_item_malformed_location_tolerated():
    item = item_from_line(LOC_LINE.replace("41.123456,-95.654321,12.5", "garbage"))
    assert item is not None and item["lat"] is None
