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


def test_write_timestamp_file_round_trips(tmp_path):
    path = tmp_path / 'restart_seen'

    assert rotorsync_watchdog.write_timestamp_file(path, 123.4567) is True

    assert rotorsync_watchdog.read_timestamp_file(path) == 123.457


def test_connected_discoverability_recovery_defaults_are_field_bounded():
    assert rotorsync_watchdog.GATT_CONNECTED_DISCOVERABILITY_RECOVERY_ENABLED is True
    assert rotorsync_watchdog.GATT_CONNECTED_DISCOVERABILITY_STALE_SECONDS == 75
    assert rotorsync_watchdog.GATT_STALE_RECOVERY_MIN_INTERVAL_SECONDS == 180


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
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_SELF_ADV_MISSING_SECONDS', 120)

    reason = rotorsync_watchdog.stale_gatt_self_adv_reason(
        now=250,
        advertising_started_at=100,
        self_adv_seen_at=None,
    )

    assert reason == 'self-scan has not seen GATT advert since advertising started 150s ago'


def test_self_adv_status_reports_pending_current_session_proof(monkeypatch):
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_SELF_ADV_MISSING_SECONDS', 120)

    status = rotorsync_watchdog.gatt_self_adv_status(
        now=180,
        advertising_started_at=100,
        self_adv_seen_at=90,
    )

    assert status == 'pending/no current proof age=80s'


def test_self_adv_status_only_reports_ok_for_current_session_seen(monkeypatch):
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_SELF_ADV_STALE_SECONDS', 90)

    status = rotorsync_watchdog.gatt_self_adv_status(
        now=180,
        advertising_started_at=100,
        self_adv_seen_at=170,
    )

    assert status == 'ok'


def test_self_adv_status_reports_missing_current_session_proof(monkeypatch):
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_SELF_ADV_MISSING_SECONDS', 120)

    status = rotorsync_watchdog.gatt_self_adv_status(
        now=250,
        advertising_started_at=100,
        self_adv_seen_at=90,
    )

    assert status == 'no current proof for 150s'


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


def test_stale_recovery_allows_unknown_connections_without_fresh_controller_proof():
    assert rotorsync_watchdog.stale_gatt_recovery_reason(
        'no GATT client reads for 130s',
        'self-scan has not seen GATT advert for 100s',
        None,
        now=500,
        connection_state_at=None,
    ) == 'no GATT client reads for 130s; self-scan has not seen GATT advert for 100s'


def test_stale_recovery_keeps_fresh_connected_controller_when_reads_are_stale(monkeypatch):
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_CONNECTION_PROOF_STALE_SECONDS', 300)

    assert rotorsync_watchdog.stale_gatt_recovery_reason(
        'no GATT client reads for 130s',
        'self-scan has not seen GATT advert for 100s',
        1,
        now=500,
        connection_state_at=250,
    ) is None


def test_fresh_controller_proof_still_requires_recent_connection_state(monkeypatch):
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_CONNECTION_PROOF_STALE_SECONDS', 300)

    assert rotorsync_watchdog.has_fresh_controller_proof(
        now=500,
        connection_count=1,
        connection_state_at=250,
    )


def test_stale_recovery_does_not_run_with_recent_reads_even_if_connected(monkeypatch):
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_CONNECTION_PROOF_STALE_SECONDS', 300)

    assert rotorsync_watchdog.stale_gatt_recovery_reason(
        'no GATT client reads for 130s',
        None,
        1,
        now=500,
        connection_state_at=250,
    ) is None


def test_stale_recovery_allows_stale_connected_controller_proof(monkeypatch):
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_CONNECTION_PROOF_STALE_SECONDS', 300)

    assert rotorsync_watchdog.stale_gatt_recovery_reason(
        'no GATT client reads for 130s',
        'self-scan has not seen GATT advert for 100s',
        1,
        now=500,
        connection_state_at=100,
    ) == 'no GATT client reads for 130s; self-scan has not seen GATT advert for 100s'


def test_stale_recovery_combines_stale_signals_with_zero_connections():
    assert rotorsync_watchdog.stale_gatt_recovery_reason(
        'no GATT client reads for 130s',
        'self-scan has not seen GATT advert for 100s',
        0,
    ) == 'no GATT client reads for 130s; self-scan has not seen GATT advert for 100s'


def test_connected_discoverability_keeps_one_fresh_reader_connected(monkeypatch):
    monkeypatch.setattr(
        rotorsync_watchdog,
        'GATT_CONNECTED_DISCOVERABILITY_STALE_SECONDS',
        180,
    )

    reason = rotorsync_watchdog.connected_discoverability_recovery_reason(
        now=400,
        advertising_started_at=100,
        self_adv_seen_at=150,
        connection_payload={
            'count': 1,
            'timestamp': 350,
            'client_details': [
                {'id': 'ipad', 'last_seen': 395, 'role': 'pilot'},
            ],
        },
    )

    assert reason is None


def test_connected_discoverability_waits_for_longer_connected_grace(monkeypatch):
    monkeypatch.setattr(
        rotorsync_watchdog,
        'GATT_CONNECTED_DISCOVERABILITY_STALE_SECONDS',
        180,
    )

    assert rotorsync_watchdog.connected_discoverability_recovery_reason(
        now=300,
        advertising_started_at=100,
        self_adv_seen_at=150,
        connection_payload={
            'count': 1,
            'timestamp': 295,
            'client_details': [
                {'id': 'ipad', 'last_seen': 295, 'role': 'pilot'},
            ],
        },
    ) is None


def test_connected_discoverability_keeps_two_fresh_readers_connected(monkeypatch):
    monkeypatch.setattr(
        rotorsync_watchdog,
        'GATT_CONNECTED_DISCOVERABILITY_STALE_SECONDS',
        180,
    )
    monkeypatch.setattr(
        rotorsync_watchdog,
        'GATT_CONNECTED_CLIENT_DETAIL_STALE_SECONDS',
        45,
    )

    assert rotorsync_watchdog.connected_discoverability_recovery_reason(
        now=400,
        advertising_started_at=100,
        self_adv_seen_at=150,
        connection_payload={
            'count': 2,
            'timestamp': 395,
            'client_details': [
                {'id': 'ipad', 'last_seen': 395, 'role': 'pilot'},
                {'id': 'iphone', 'last_seen': 390, 'role': 'pilot'},
            ],
        },
    ) is None


def test_connected_discoverability_keeps_stale_extra_connection_with_fresh_state(monkeypatch):
    monkeypatch.setattr(
        rotorsync_watchdog,
        'GATT_CONNECTED_DISCOVERABILITY_STALE_SECONDS',
        180,
    )
    monkeypatch.setattr(
        rotorsync_watchdog,
        'GATT_CONNECTED_CLIENT_DETAIL_STALE_SECONDS',
        45,
    )

    reason = rotorsync_watchdog.connected_discoverability_recovery_reason(
        now=400,
        advertising_started_at=100,
        self_adv_seen_at=150,
        connection_payload={
            'count': 2,
            'timestamp': 395,
            'client_details': [
                {'id': 'ipad', 'last_seen': 395, 'role': 'pilot'},
                {'id': 'iphone', 'last_seen': 300, 'role': 'pilot'},
            ],
        },
    )

    assert reason is None


def test_connected_discoverability_recovers_stale_extra_connection_after_state_stales(monkeypatch):
    monkeypatch.setattr(
        rotorsync_watchdog,
        'GATT_CONNECTED_DISCOVERABILITY_STALE_SECONDS',
        180,
    )
    monkeypatch.setattr(
        rotorsync_watchdog,
        'GATT_CONNECTED_CLIENT_DETAIL_STALE_SECONDS',
        45,
    )
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_CONNECTION_PROOF_STALE_SECONDS', 300)

    reason = rotorsync_watchdog.connected_discoverability_recovery_reason(
        now=400,
        advertising_started_at=100,
        self_adv_seen_at=150,
        connection_payload={
            'count': 2,
            'timestamp': 50,
            'client_details': [
                {'id': 'ipad', 'last_seen': 395, 'role': 'pilot'},
                {'id': 'iphone', 'last_seen': 300, 'role': 'pilot'},
            ],
        },
    )

    assert reason == (
        'self-scan has not seen GATT advert for 250s; '
        '1 stale connected controller(s): iphone'
    )


def test_read_gatt_connection_state_ignores_state_from_previous_advertising_run(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / 'connections.json'
    path.write_text(
        json.dumps({'timestamp': 99, 'count': 1, 'reason': 'disconnect'}),
        encoding='utf-8',
    )
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_CONNECTION_STATE_FILE', path)

    assert rotorsync_watchdog.read_gatt_connection_state(100) == (None, None)


def test_read_gatt_connection_state_returns_fresh_count_and_timestamp(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / 'connections.json'
    path.write_text(
        json.dumps({'timestamp': 101.5, 'count': 1, 'reason': 'connect'}),
        encoding='utf-8',
    )
    monkeypatch.setattr(rotorsync_watchdog, 'GATT_CONNECTION_STATE_FILE', path)

    assert rotorsync_watchdog.read_gatt_connection_state(100) == (1, 101.5)
