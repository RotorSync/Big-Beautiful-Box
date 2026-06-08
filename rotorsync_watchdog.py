#!/usr/bin/env python3
"""
Rotorsync BLE Watchdog

Tracks the Bumble-owned GATT adapter by sysfs device path so the watchdog does
not touch the live controller with hciconfig while it is in use.
"""
import logging
import json
import subprocess
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

CHECK_INTERVAL = 10
FAIL_THRESHOLD = 2
GATT_CLIENT_STALE_SECONDS = 120
GATT_SELF_ADV_STALE_SECONDS = 90
GATT_SELF_ADV_MISSING_SECONDS = 120
GATT_CONNECTED_DISCOVERABILITY_RECOVERY_ENABLED = False
GATT_CONNECTED_DISCOVERABILITY_STALE_SECONDS = 180
GATT_CONNECTED_CLIENT_DETAIL_STALE_SECONDS = 45
GATT_CONNECTION_PROOF_STALE_SECONDS = 300
GATT_STALE_RECOVERY_MIN_INTERVAL_SECONDS = 900
GATT_ADAPTER_MAC = 'E8:EA:6A:BD:E7:4F'
GATT_DEVICE_PATH_FILE = Path('/home/pi/rotorsync_gatt_device_path')
GATT_ADVERTISING_READY_FILE = Path('/home/pi/rotorsync_gatt_advertising_ready.json')
GATT_CLIENT_SEEN_FILE = Path('/home/pi/rotorsync_gatt_client_seen')
GATT_SELF_ADV_SEEN_FILE = Path('/home/pi/rotorsync_gatt_self_adv_seen.json')
GATT_CONNECTION_STATE_FILE = Path('/home/pi/rotorsync_gatt_connections.json')
GATT_STALE_RECOVERY_RESTART_FILE = Path('/home/pi/rotorsync_gatt_stale_recovery_restart')

last_known_hci = None


def check_service_running():
    """Check if rotorsync service is active."""
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', 'rotorsync'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() == 'active'
    except Exception as e:
        logging.error(f'Service check error: {e}')
        return False


def get_adapter_device_path(adapter):
    """Resolve the physical sysfs device path for an adapter."""
    if not adapter:
        return None
    try:
        return Path('/sys/class/bluetooth', adapter, 'device').resolve()
    except FileNotFoundError:
        return None
    except Exception:
        return None


def find_adapter_by_device_path(expected_device_path):
    """Find the current hci index for a physical Bluetooth interface."""
    if not expected_device_path:
        return None
    for adapter_path in sorted(Path('/sys/class/bluetooth').glob('hci*')):
        try:
            if (adapter_path / 'device').resolve() == expected_device_path:
                return adapter_path.name
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return None


def read_expected_device_path():
    """Read the persisted GATT adapter sysfs path written by rotorsync."""
    try:
        text = GATT_DEVICE_PATH_FILE.read_text(encoding='utf-8').strip()
        return Path(text) if text else None
    except FileNotFoundError:
        return None
    except Exception as e:
        logging.error(f'Expected device path read error: {e}')
        return None


def read_timestamp_file(path):
    """Read a plain or JSON timestamp written by rotorsync_bumble."""
    try:
        text = path.read_text(encoding='utf-8').strip()
        if not text:
            return None
        if text.startswith('{'):
            payload = json.loads(text)
            return float(payload.get('timestamp'))
        return float(text)
    except FileNotFoundError:
        return None
    except Exception as e:
        logging.error(f'Timestamp read error for {path}: {e}')
        return None


def write_timestamp_file(path, timestamp):
    """Write a tiny timestamp file atomically."""
    try:
        tmp_path = path.with_name(f'{path.name}.tmp')
        tmp_path.write_text(f'{timestamp:.3f}\n', encoding='utf-8')
        tmp_path.replace(path)
        return True
    except Exception as e:
        logging.error(f'Timestamp write error for {path}: {e}')
        return False


def read_json_file(path):
    """Read a small JSON state file."""
    try:
        text = path.read_text(encoding='utf-8').strip()
        if not text:
            return None
        return json.loads(text)
    except FileNotFoundError:
        return None
    except Exception as e:
        logging.error(f'JSON read error for {path}: {e}')
        return None


def read_gatt_connection_payload(advertising_started_at):
    """Return active GATT connection payload when it belongs to this run."""
    payload = read_json_file(GATT_CONNECTION_STATE_FILE)
    if not isinstance(payload, dict):
        return None
    try:
        timestamp = float(payload.get('timestamp') or 0)
        if advertising_started_at and timestamp < advertising_started_at:
            return None
        payload['timestamp'] = timestamp
        payload['count'] = max(0, int(payload.get('count') or 0))
        return payload
    except Exception as e:
        logging.error(f'GATT connection state parse error: {e}')
        return None


def read_gatt_connection_state(advertising_started_at):
    """Return active GATT connection state when it belongs to this run."""
    payload = read_gatt_connection_payload(advertising_started_at)
    if not isinstance(payload, dict):
        return None, None
    return payload.get('count'), payload.get('timestamp')


def read_gatt_connection_count(advertising_started_at):
    """Return active GATT connection count when the state belongs to this run."""
    count, _timestamp = read_gatt_connection_state(advertising_started_at)
    return count


def stale_gatt_client_reason(now, advertising_started_at, client_seen_at):
    """Return a restart reason when advertising is ready but no client is reading it."""
    if not advertising_started_at:
        return None

    if client_seen_at and client_seen_at >= advertising_started_at:
        stale_seconds = now - client_seen_at
        if stale_seconds > GATT_CLIENT_STALE_SECONDS:
            return f'no GATT client reads for {stale_seconds:.0f}s'
        return None

    age_seconds = now - advertising_started_at
    if age_seconds > GATT_CLIENT_STALE_SECONDS:
        return f'no GATT client reads since advertising started {age_seconds:.0f}s ago'
    return None


def stale_gatt_self_adv_reason(now, advertising_started_at, self_adv_seen_at):
    """Return a reason when the sensor adapter stops seeing our GATT advert."""
    if not advertising_started_at:
        return None

    if self_adv_seen_at and self_adv_seen_at >= advertising_started_at:
        stale_seconds = now - self_adv_seen_at
        if stale_seconds > GATT_SELF_ADV_STALE_SECONDS:
            return f'self-scan has not seen GATT advert for {stale_seconds:.0f}s'
        return None

    age_seconds = now - advertising_started_at
    if age_seconds > GATT_SELF_ADV_MISSING_SECONDS:
        return f'self-scan has not seen GATT advert since advertising started {age_seconds:.0f}s ago'
    return None


def gatt_self_adv_status(now, advertising_started_at, self_adv_seen_at):
    """Return an honest log status for current-session self-advert proof."""
    if not advertising_started_at:
        return 'not ready'

    if self_adv_seen_at and self_adv_seen_at >= advertising_started_at:
        stale_seconds = now - self_adv_seen_at
        if stale_seconds > GATT_SELF_ADV_STALE_SECONDS:
            return f'stale for {stale_seconds:.0f}s'
        return 'ok'

    age_seconds = now - advertising_started_at
    if age_seconds > GATT_SELF_ADV_MISSING_SECONDS:
        return f'no current proof for {age_seconds:.0f}s'
    return f'pending/no current proof age={age_seconds:.0f}s'


def has_fresh_controller_proof(now, connection_count, connection_state_at):
    """Return true only when connection bookkeeping is fresh enough to trust."""
    if connection_count is None or connection_count <= 0:
        return False
    if not connection_state_at:
        return False
    return now - connection_state_at <= GATT_CONNECTION_PROOF_STALE_SECONDS


def stale_gatt_recovery_reason(
    stale_client_reason,
    stale_self_adv_reason,
    connection_count,
    *,
    now=None,
    connection_state_at=None,
):
    """Combine weak signals into a conservative advertising-wedge recovery reason."""
    if not stale_client_reason or not stale_self_adv_reason:
        return None
    return f'{stale_client_reason}; {stale_self_adv_reason}'


def _connected_self_adv_stale_reason(now, advertising_started_at, self_adv_seen_at):
    if not advertising_started_at:
        return None

    if self_adv_seen_at and self_adv_seen_at >= advertising_started_at:
        stale_seconds = now - self_adv_seen_at
        if stale_seconds > GATT_CONNECTED_DISCOVERABILITY_STALE_SECONDS:
            return f'self-scan has not seen GATT advert for {stale_seconds:.0f}s'
        return None

    age_seconds = now - advertising_started_at
    if age_seconds > GATT_CONNECTED_DISCOVERABILITY_STALE_SECONDS:
        return f'self-scan has not seen GATT advert since advertising started {age_seconds:.0f}s ago'
    return None


def _stale_connected_client_details(now, connection_payload):
    if not isinstance(connection_payload, dict):
        return []

    details = connection_payload.get('client_details')
    if not isinstance(details, list):
        return []

    stale = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        try:
            last_seen = float(detail.get('last_seen') or 0)
        except Exception:
            last_seen = 0
        if not last_seen or now - last_seen > GATT_CONNECTED_CLIENT_DETAIL_STALE_SECONDS:
            stale.append(str(detail.get('id') or 'unknown'))
    return stale


def connected_discoverability_recovery_reason(
    now,
    advertising_started_at,
    self_adv_seen_at,
    connection_payload,
):
    """Recover when connected clients hide a wedged advert from new controllers."""
    self_adv_reason = _connected_self_adv_stale_reason(
        now,
        advertising_started_at,
        self_adv_seen_at,
    )
    if not self_adv_reason or not isinstance(connection_payload, dict):
        return None

    connection_count = int(connection_payload.get('count') or 0)
    if connection_count <= 0:
        return None

    if connection_count == 1:
        return (
            f'{self_adv_reason}; one controller remains connected, '
            'recovering discoverability'
        )

    stale_clients = _stale_connected_client_details(now, connection_payload)
    if stale_clients:
        return (
            f'{self_adv_reason}; {len(stale_clients)} stale connected '
            f'controller(s): {",".join(stale_clients[:3])}'
        )

    return None


def restart_rotorsync():
    """Restart the rotorsync service."""
    logging.warning('Restarting rotorsync service...')
    try:
        subprocess.run(['systemctl', 'restart', 'rotorsync'], check=True, timeout=30)
        logging.info('Rotorsync restarted successfully')
        return True
    except Exception as e:
        logging.error(f'Restart failed: {e}')
        return False


def main():
    global last_known_hci

    logging.info('Rotorsync watchdog started (sysfs adapter tracking)')
    logging.info(f'Check interval: {CHECK_INTERVAL}s, Fail threshold: {FAIL_THRESHOLD}')

    consecutive_failures = 0
    last_stale_client_log_at = 0
    last_stale_recovery_restart_at = (
        read_timestamp_file(GATT_STALE_RECOVERY_RESTART_FILE) or 0
    )
    time.sleep(30)

    expected_device_path = read_expected_device_path()
    current_hci = find_adapter_by_device_path(expected_device_path)
    last_known_hci = current_hci
    logging.info(f'Initial GATT adapter HCI: {last_known_hci}')
    logging.info(f'Initial GATT device path: {expected_device_path}')

    while True:
        try:
            if expected_device_path is None:
                expected_device_path = read_expected_device_path()
                current_hci = find_adapter_by_device_path(expected_device_path)
                if current_hci:
                    last_known_hci = current_hci
                if expected_device_path is None:
                    logging.info('Waiting for GATT device path file...')
                    time.sleep(CHECK_INTERVAL)
                    continue

            service_ok = check_service_running()
            current_hci = find_adapter_by_device_path(expected_device_path)
            adapter_ok = current_hci is not None
            now = time.time()
            advertising_started_at = read_timestamp_file(GATT_ADVERTISING_READY_FILE)
            client_seen_at = read_timestamp_file(GATT_CLIENT_SEEN_FILE)
            self_adv_seen_at = read_timestamp_file(GATT_SELF_ADV_SEEN_FILE)
            connection_payload = read_gatt_connection_payload(advertising_started_at)
            if isinstance(connection_payload, dict):
                connection_count = connection_payload.get('count')
                connection_state_at = connection_payload.get('timestamp')
            else:
                connection_count, connection_state_at = None, None
            stale_client_reason = stale_gatt_client_reason(
                now,
                advertising_started_at,
                client_seen_at,
            )
            stale_self_adv_reason = stale_gatt_self_adv_reason(
                now,
                advertising_started_at,
                self_adv_seen_at,
            )
            self_adv_status = gatt_self_adv_status(
                now,
                advertising_started_at,
                self_adv_seen_at,
            )
            stale_recovery_reason = stale_gatt_recovery_reason(
                stale_client_reason,
                stale_self_adv_reason,
                connection_count,
                now=now,
                connection_state_at=connection_state_at,
            )
            connected_discoverability_reason = None
            if GATT_CONNECTED_DISCOVERABILITY_RECOVERY_ENABLED:
                connected_discoverability_reason = connected_discoverability_recovery_reason(
                    now,
                    advertising_started_at,
                    self_adv_seen_at,
                    connection_payload,
                )
            recovery_reason = stale_recovery_reason or connected_discoverability_reason
            if (stale_client_reason or stale_self_adv_reason) and now - last_stale_client_log_at >= 60:
                logging.info(
                    'GATT stale check: '
                    f'client={stale_client_reason or "ok"}, '
                    f'self_adv={stale_self_adv_reason or self_adv_status}, '
                    f'connections={connection_count if connection_count is not None else "unknown"}'
                )
                last_stale_client_log_at = now
            if (
                connected_discoverability_reason
                and now - last_stale_recovery_restart_at < GATT_STALE_RECOVERY_MIN_INTERVAL_SECONDS
            ):
                remaining = int(
                    GATT_STALE_RECOVERY_MIN_INTERVAL_SECONDS
                    - (now - last_stale_recovery_restart_at)
                )
                logging.info(
                    'GATT stale recovery suppressed by rate limit: '
                    f'{remaining}s remaining'
                )
                connected_discoverability_reason = None
                recovery_reason = stale_recovery_reason

            hci_changed = False
            if last_known_hci and current_hci and current_hci != last_known_hci:
                logging.warning(f'GATT adapter moved from {last_known_hci} to {current_hci}')
                hci_changed = True

            if service_ok and adapter_ok and not hci_changed and not recovery_reason:
                if consecutive_failures > 0:
                    logging.info('Rotorsync healthy')
                consecutive_failures = 0
                last_known_hci = current_hci
            else:
                consecutive_failures += 1
                reason = []
                if not service_ok:
                    reason.append('service down')
                if not adapter_ok:
                    reason.append('adapter missing')
                if hci_changed:
                    reason.append(f'HCI changed {last_known_hci}->{current_hci}')
                if recovery_reason:
                    reason.append(recovery_reason)

                logging.warning(
                    f'Rotorsync issue ({consecutive_failures}/{FAIL_THRESHOLD}): {", ".join(reason)}'
                )

                if hci_changed or consecutive_failures >= FAIL_THRESHOLD:
                    if connected_discoverability_reason:
                        last_stale_recovery_restart_at = now
                        write_timestamp_file(GATT_STALE_RECOVERY_RESTART_FILE, now)
                    restart_rotorsync()
                    consecutive_failures = 0
                    time.sleep(30)
                    expected_device_path = read_expected_device_path()
                    last_known_hci = find_adapter_by_device_path(expected_device_path)
                    logging.info(f'After restart, GATT adapter at: {last_known_hci}')
                    logging.info(f'After restart, GATT device path: {expected_device_path}')

        except Exception as e:
            logging.error(f'Watchdog error: {e}')

        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
