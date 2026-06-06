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


def test_stale_self_adv_detects_missing_after_grace(monkeypatch):
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_SELF_ADV_MISSING_SECONDS', 600)

    reason = rotorsync_watchdog.stale_gatt_self_adv_reason(
        now=800,
        advertising_started_at=100,
        self_adv_seen_at=None,
    )

    assert reason == 'self-scan has not seen GATT advert since advertising started 700s ago'


def test_stale_self_adv_allows_recent_seen(monkeypatch):
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_SELF_ADV_STALE_SECONDS', 90)

    reason = rotorsync_watchdog.stale_gatt_self_adv_reason(
        now=250,
        advertising_started_at=100,
        self_adv_seen_at=200,
    )

    assert reason is None


def test_stale_self_adv_detects_stopped_seen(monkeypatch):
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_SELF_ADV_STALE_SECONDS', 90)

    reason = rotorsync_watchdog.stale_gatt_self_adv_reason(
        now=300,
        advertising_started_at=100,
        self_adv_seen_at=200,
    )

    assert reason == 'self-scan has not seen GATT advert for 100s'


def test_stale_recovery_requires_client_and_self_scan_stale():
    assert rotorsync_watchdog.stale_gatt_recovery_reason(
        'no GATT client reads for 130s',
        None,
        0,
    ) is None
    assert rotorsync_watchdog.stale_gatt_recovery_reason(
        None,
        'self-scan has not seen GATT advert for 100s',
        0,
    ) is None


def test_stale_recovery_requires_zero_known_connections():
    assert rotorsync_watchdog.stale_gatt_recovery_reason(
        'no GATT client reads for 130s',
        'self-scan has not seen GATT advert for 100s',
        None,
    ) is None
    assert rotorsync_watchdog.stale_gatt_recovery_reason(
        'no GATT client reads for 130s',
        'self-scan has not seen GATT advert for 100s',
        1,
    ) is None


def test_stale_recovery_combines_stale_signals_with_zero_connections():
    assert rotorsync_watchdog.stale_gatt_recovery_reason(
        'no GATT client reads for 130s',
        'self-scan has not seen GATT advert for 100s',
        0,
    ) == 'no GATT client reads for 130s; self-scan has not seen GATT advert for 100s'
