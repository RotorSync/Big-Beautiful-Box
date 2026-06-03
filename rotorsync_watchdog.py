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
GATT_CLIENT_RESTART_COOLDOWN_SECONDS = 600
GATT_ADAPTER_MAC = 'E8:EA:6A:BD:E7:4F'
GATT_DEVICE_PATH_FILE = Path('/home/pi/rotorsync_gatt_device_path')
GATT_ADVERTISING_READY_FILE = Path('/home/pi/rotorsync_gatt_advertising_ready.json')
GATT_CLIENT_SEEN_FILE = Path('/home/pi/rotorsync_gatt_client_seen')

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
    last_gatt_client_restart_at = 0
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
            stale_client_reason = stale_gatt_client_reason(
                now,
                advertising_started_at,
                client_seen_at,
            )
            if stale_client_reason and (
                now - last_gatt_client_restart_at < GATT_CLIENT_RESTART_COOLDOWN_SECONDS
            ):
                remaining = GATT_CLIENT_RESTART_COOLDOWN_SECONDS - (
                    now - last_gatt_client_restart_at
                )
                logging.info(
                    f'Suppressing stale GATT client restart for {remaining:.0f}s: '
                    f'{stale_client_reason}'
                )
                stale_client_reason = None

            hci_changed = False
            if last_known_hci and current_hci and current_hci != last_known_hci:
                logging.warning(f'GATT adapter moved from {last_known_hci} to {current_hci}')
                hci_changed = True

            if service_ok and adapter_ok and not hci_changed and not stale_client_reason:
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
                if stale_client_reason:
                    reason.append(stale_client_reason)

                logging.warning(
                    f'Rotorsync issue ({consecutive_failures}/{FAIL_THRESHOLD}): {", ".join(reason)}'
                )

                if hci_changed or consecutive_failures >= FAIL_THRESHOLD:
                    if stale_client_reason:
                        last_gatt_client_restart_at = now
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
