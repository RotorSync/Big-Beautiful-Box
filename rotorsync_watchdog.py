#!/usr/bin/env python3
"""
Rotorsync BLE Watchdog - Detects adapter changes and restarts service
"""
import logging
import re
import subprocess
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

CHECK_INTERVAL = 10
FAIL_THRESHOLD = 2
GATT_ADAPTER_MAC = 'E8:EA:6A:BD:E7:4F'

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
        logging.error(f"Service check error: {e}")
        return False


def get_adapter_hci():
    """Get the HCI index for the GATT adapter by MAC address."""
    try:
        result = subprocess.run(
            ['hciconfig', '-a'],
            capture_output=True,
            text=True,
            timeout=10,
        )

        output = result.stdout + result.stderr
        lines = output.split('\n')

        current_hci = None
        for line in lines:
            hci_match = re.match(r'^(hci\d+):', line)
            if hci_match:
                current_hci = hci_match.group(1)

            if current_hci and GATT_ADAPTER_MAC in line:
                return current_hci

        return None
    except Exception as e:
        logging.error(f"HCI lookup error: {e}")
        return None


def check_adapter_up():
    """Check if GATT adapter is UP RUNNING."""
    try:
        result = subprocess.run(
            ['hciconfig', '-a'],
            capture_output=True,
            text=True,
            timeout=10,
        )

        output = result.stdout + result.stderr

        if GATT_ADAPTER_MAC in output and 'UP RUNNING' in output:
            lines = output.split('\n')
            found_mac = False
            for line in lines:
                if GATT_ADAPTER_MAC in line:
                    found_mac = True
                if found_mac and 'UP RUNNING' in line:
                    return True
                if found_mac and line.strip().startswith('hci') and GATT_ADAPTER_MAC not in line:
                    found_mac = False
            return True

        return False
    except Exception as e:
        logging.error(f"Adapter check error: {e}")
        return False


def restart_rotorsync():
    """Restart the rotorsync service."""
    logging.warning("Restarting rotorsync service...")
    try:
        subprocess.run(['systemctl', 'restart', 'rotorsync'], check=True, timeout=30)
        logging.info("Rotorsync restarted successfully")
        return True
    except Exception as e:
        logging.error(f"Restart failed: {e}")
        return False


def main():
    global last_known_hci

    logging.info("Rotorsync watchdog started (with HCI change detection)")
    logging.info(f"Check interval: {CHECK_INTERVAL}s, Fail threshold: {FAIL_THRESHOLD}")

    consecutive_failures = 0
    time.sleep(30)

    last_known_hci = get_adapter_hci()
    logging.info(f"Initial GATT adapter HCI: {last_known_hci}")

    while True:
        try:
            service_ok = check_service_running()
            adapter_ok = check_adapter_up()
            current_hci = get_adapter_hci()

            hci_changed = False
            if last_known_hci and current_hci and current_hci != last_known_hci:
                logging.warning(f"GATT adapter moved from {last_known_hci} to {current_hci}")
                hci_changed = True

            if service_ok and adapter_ok and not hci_changed:
                if consecutive_failures > 0:
                    logging.info("Rotorsync healthy")
                consecutive_failures = 0
                if current_hci:
                    last_known_hci = current_hci
            else:
                consecutive_failures += 1
                reason = []
                if not service_ok:
                    reason.append("service down")
                if not adapter_ok:
                    reason.append("adapter not UP")
                if hci_changed:
                    reason.append(f"HCI changed {last_known_hci}->{current_hci}")

                logging.warning(
                    f"Rotorsync issue ({consecutive_failures}/{FAIL_THRESHOLD}): {', '.join(reason)}"
                )

                if hci_changed or consecutive_failures >= FAIL_THRESHOLD:
                    restart_rotorsync()
                    consecutive_failures = 0
                    time.sleep(30)
                    last_known_hci = get_adapter_hci()
                    logging.info(f"After restart, GATT adapter at: {last_known_hci}")

        except Exception as e:
            logging.error(f"Watchdog error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
