import csv
import importlib
import sys
import time

import pytest

from src.mopeka_history import (
    MOPEKA_HISTORY_FIELDNAMES,
    MOPEKA_HISTORY_IDENTITY_FIELDNAMES,
    MOPEKA_HISTORY_LEGACY_FIELDNAMES,
    ensure_active_history_schema,
    history_identity_from_config,
)
from tests.test_maintenance_auth import install_bumble_stubs


@pytest.fixture
def bumble_module(monkeypatch):
    monkeypatch.setenv("BBB_MAINTENANCE_SECRET", "unit-test-secret")
    install_bumble_stubs(monkeypatch)
    sys.modules.pop("rotorsync_bumble", None)
    module = importlib.import_module("rotorsync_bumble")
    yield module
    sys.modules.pop("rotorsync_bumble", None)


def _reading(gallons):
    return {
        "gallons": gallons,
        "level_mm": gallons * 2,
        "level_in": gallons / 10,
        "quality": 3,
    }


def _identity(trailer, front, back):
    return {
        "box_mode": "fleet",
        "assigned_trailer": trailer,
        "trailer": trailer,
        "front_id": front,
        "back_id": back,
    }


def test_identity_falls_back_to_legacy_trailer_key_when_assignment_is_null():
    assert history_identity_from_config({
        "assigned_trailer": None,
        "trailer": "TR02",
        "front_id": "aa-bb-cc",
        "back_id": "11-22-33",
    }) == ("2", "AA:BB:CC", "11:22:33")


def test_a_to_b_to_a_starts_each_identity_and_ble_returns_only_current_a(
    bumble_module,
    monkeypatch,
    tmp_path,
):
    history_path = tmp_path / "mopeka_history.csv"
    current = _identity("TR01", "aa-bb-cc", "11-22-33")
    monkeypatch.setattr(bumble_module, "MOPEKA_HISTORY_LOG_PATH", str(history_path))
    monkeypatch.setattr(bumble_module, "load_config", lambda: dict(current))
    bumble_module._reset_mopeka_history_baseline()
    base = int(time.time()) - 30

    assert bumble_module.maybe_log_mopeka_history(
        base, _reading(10), _reading(11)
    )
    current.clear()
    current.update(_identity(2, "DD:EE:FF", "44:55:66"))
    assert bumble_module.maybe_log_mopeka_history(
        base + 1, _reading(20), _reading(21)
    )
    current.clear()
    current.update(_identity(1, "AA:BB:CC", "11:22:33"))
    assert bumble_module.maybe_log_mopeka_history(
        base + 2, _reading(12), _reading(13)
    )

    with history_path.open(newline="") as source:
        rows = list(csv.DictReader(source))
    assert [row["reason"] for row in rows] == ["start", "start", "start"]
    assert [row["trailer_id"] for row in rows] == ["1", "2", "1"]

    items = bumble_module._load_mopeka_history_items({"hours": 12})
    assert [item["fg"] for item in items] == [10.0, 12.0]
    assert all(item["r"] == "start" for item in items)


def test_first_reading_for_b_is_start_even_when_values_match_a(
    bumble_module,
    monkeypatch,
):
    current = _identity(1, "AA:BB:CC", "11:22:33")
    appended = []
    monkeypatch.setattr(bumble_module, "load_config", lambda: dict(current))
    monkeypatch.setattr(
        bumble_module,
        "_append_mopeka_history_row",
        lambda snapshot, reason: appended.append((snapshot, reason)),
    )
    bumble_module._reset_mopeka_history_baseline()

    assert bumble_module.maybe_log_mopeka_history(
        1000, _reading(50), _reading(60)
    )
    current.clear()
    current.update(_identity(2, "DD:EE:FF", "44:55:66"))
    assert bumble_module.maybe_log_mopeka_history(
        1001, _reading(50), _reading(60)
    )

    assert [reason for _, reason in appended] == ["start", "start"]
    assert appended[-1][0]["trailer_id"] == "2"


def test_cached_process_identity_avoids_per_scan_config_reads(
    bumble_module,
    monkeypatch,
):
    appended = []
    monkeypatch.setattr(
        bumble_module,
        "load_config",
        lambda: pytest.fail("sensor scan must reuse its process identity"),
    )
    monkeypatch.setattr(
        bumble_module,
        "_append_mopeka_history_row",
        lambda snapshot, reason: appended.append((snapshot, reason)),
    )
    bumble_module._reset_mopeka_history_baseline()

    assert bumble_module.maybe_log_mopeka_history(
        1000,
        _reading(50),
        _reading(60),
        identity=("2", "DD:EE:FF", "44:55:66"),
    )
    assert appended[0][1] == "start"


def test_active_legacy_csv_migration_preserves_rows_without_fabricated_identity(
    tmp_path,
):
    history_path = tmp_path / "mopeka_history.csv"
    legacy_row = [
        "2026-07-13 10:00:00",
        "periodic",
        "101.250",
        "99.500",
        "400.0",
        "390.0",
        "15.75",
        "15.35",
        "3",
        "2",
    ]
    with history_path.open("w", newline="") as destination:
        writer = csv.writer(destination)
        writer.writerow(MOPEKA_HISTORY_LEGACY_FIELDNAMES)
        writer.writerow(legacy_row)

    fieldnames = ensure_active_history_schema(history_path)

    assert fieldnames == list(MOPEKA_HISTORY_FIELDNAMES)
    with history_path.open(newline="") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 1
    assert [rows[0][name] for name in MOPEKA_HISTORY_LEGACY_FIELDNAMES] == legacy_row
    assert all(rows[0][name] == "" for name in MOPEKA_HISTORY_IDENTITY_FIELDNAMES)


def test_ble_reads_legacy_only_history_until_first_scoped_row(
    bumble_module,
    monkeypatch,
    tmp_path,
):
    history_path = tmp_path / "mopeka_history.csv"
    now = int(time.time()) - 10
    with history_path.open("w", newline="") as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=MOPEKA_HISTORY_LEGACY_FIELDNAMES,
        )
        writer.writeheader()
        writer.writerow({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "reason": "periodic",
            "front_gal": "77.0",
        })

    monkeypatch.setattr(bumble_module, "MOPEKA_HISTORY_LOG_PATH", str(history_path))
    monkeypatch.setattr(
        bumble_module,
        "load_config",
        lambda: _identity(9, "AA:BB:CC", "11:22:33"),
    )

    assert [
        item["fg"]
        for item in bumble_module._load_mopeka_history_items({"hours": 12})
    ] == [77.0]
