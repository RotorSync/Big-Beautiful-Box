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
GATT_SELF_ADV_MISSING_SECONDS = 600
GATT_CONNECTION_PROOF_STALE_SECONDS = 300
GATT_STALE_RECOVERY_MIN_INTERVAL_SECONDS = 900
GATT_ADAPTER_MAC = 'E8:EA:6A:BD:E7:4F'
GATT_DEVICE_PATH_FILE = Path('/home/pi/rotorsync_gatt_device_path')
GATT_ADVERTISING_READY_FILE = Path('/home/pi/rotorsync_gatt_advertising_ready.json')
GATT_CLIENT_SEEN_FILE = Path('/home/pi/rotorsync_gatt_client_seen')
GATT_SELF_ADV_SEEN_FILE = Path('/home/pi/rotorsync_gatt_self_adv_seen.json')
GATT_CONNECTION_STATE_FILE = Path('/home/pi/rotorsync_gatt_connections.json')

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


def read_gatt_connection_state(advertising_started_at):
    """Return active GATT connection state when it belongs to this run."""
    payload = read_json_file(GATT_CONNECTION_STATE_FILE)
    if not isinstance(payload, dict):
        return None, None
    try:
        timestamp = float(payload.get('timestamp') or 0)
        if advertising_started_at and timestamp < advertising_started_at:
            return None, None
        return max(0, int(payload.get('count') or 0)), timestamp
    except Exception as e:
        logging.error(f'GATT connection state parse error: {e}')
        return None, None


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
    if connection_count and connection_count > 0 and now is None:
        return None
    if (
        now is not None
        and has_fresh_controller_proof(now, connection_count, connection_state_at)
    ):
        return None
    return f'{stale_client_reason}; {stale_self_adv_reason}'


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
    last_stale_recovery_restart_at = 0
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
            connection_count, connection_state_at = read_gatt_connection_state(
                advertising_started_at
            )
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
            stale_recovery_reason = stale_gatt_recovery_reason(
                stale_client_reason,
                stale_self_adv_reason,
                connection_count,
                now=now,
                connection_state_at=connection_state_at,
            )
            if (stale_client_reason or stale_self_adv_reason) and now - last_stale_client_log_at >= 60:
                logging.info(
                    'GATT stale check: '
                    f'client={stale_client_reason or "ok"}, '
                    f'self_adv={stale_self_adv_reason or "ok"}, '
                    f'connections={connection_count if connection_count is not None else "unknown"}'
                )
                last_stale_client_log_at = now
            if (
                stale_recovery_reason
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
                stale_recovery_reason = None

            hci_changed = False
            if last_known_hci and current_hci and current_hci != last_known_hci:
                logging.warning(f'GATT adapter moved from {last_known_hci} to {current_hci}')
                hci_changed = True

            if service_ok and adapter_ok and not hci_changed and not stale_recovery_reason:
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
                if stale_recovery_reason:
                    reason.append(stale_recovery_reason)

                logging.warning(
                    f'Rotorsync issue ({consecutive_failures}/{FAIL_THRESHOLD}): {", ".join(reason)}'
                )

                if hci_changed or consecutive_failures >= FAIL_THRESHOLD:
                    if stale_recovery_reason:
                        last_stale_recovery_restart_at = now
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
