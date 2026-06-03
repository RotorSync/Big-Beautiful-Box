import json

import rotorsync_watchdog


def test_plain_timestamp_file(tmp_path):
    path = tmp_path / 'client_seen'
    path.write_text('123.456\n', encoding='utf-8')

    assert rotorsync_watchdog.read_timestamp_file(path) == 123.456


def test_json_timestamp_file(tmp_path):
    path = tmp_path / 'advertising_ready.json'
    path.write_text(json.dumps({'timestamp': 456.789}), encoding='utf-8')

    assert rotorsync_watchdog.read_timestamp_file(path) == 456.789


def test_stale_gatt_client_detects_no_reads_after_advertising(monkeypatch):
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_CLIENT_STALE_SECONDS', 120)

    reason = rotorsync_watchdog.stale_gatt_client_reason(
        now=250,
        advertising_started_at=100,
        client_seen_at=None,
    )

    assert reason == 'no GATT client reads since advertising started 150s ago'


def test_stale_gatt_client_detects_stopped_reads(monkeypatch):
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_CLIENT_STALE_SECONDS', 120)

    reason = rotorsync_watchdog.stale_gatt_client_reason(
        now=300,
        advertising_started_at=100,
        client_seen_at=170,
    )

    assert reason == 'no GATT client reads for 130s'


def test_stale_gatt_client_allows_recent_reads(monkeypatch):
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_CLIENT_STALE_SECONDS', 120)

    reason = rotorsync_watchdog.stale_gatt_client_reason(
        now=220,
        advertising_started_at=100,
        client_seen_at=170,
    )

    assert reason is None


def test_stale_gatt_client_ignores_missing_ready_timestamp():
    assert rotorsync_watchdog.stale_gatt_client_reason(
        now=300,
        advertising_started_at=None,
        client_seen_at=None,
    ) is None
