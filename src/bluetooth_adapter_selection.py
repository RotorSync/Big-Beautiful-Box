"""Bluetooth adapter discovery and role selection for TrailerSync."""

from pathlib import Path
import subprocess


GATT_USB_IDS = {
    ('2c0a', '8761'),  # Realtek Bluetooth Radio used for iPad GATT.
}

SENSOR_USB_IDS = {
    ('0b05', '1bf6'),  # ASUS/StarTech Bluetooth Controller used for BMS/Mopeka scanning.
}


def normalize_mac(mac):
    if not mac:
        return ''
    return str(mac).strip().upper()


def _read_text(path):
    try:
        return Path(path).read_text(encoding='utf-8').strip()
    except Exception:
        return ''


def _usb_parent(adapter_path):
    try:
        current = (Path(adapter_path) / 'device').resolve()
    except Exception:
        return None

    for parent in (current, *current.parents):
        if (parent / 'idVendor').exists() and (parent / 'idProduct').exists():
            return parent
    return None


def hci_macs_from_hciconfig():
    try:
        result = subprocess.run(
            ['hciconfig', '-a'],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return {}

    hci_macs = {}
    current_hci = None
    for line in result.stdout.splitlines():
        if line.startswith('hci'):
            current_hci = line.split(':', 1)[0]
        elif current_hci and 'BD Address:' in line:
            parts = line.strip().split()
            try:
                hci_macs[current_hci] = normalize_mac(parts[parts.index('Address:') + 1])
            except (ValueError, IndexError):
                continue
    return hci_macs


def list_bluetooth_adapters(bluetooth_root='/sys/class/bluetooth'):
    """Return current HCI adapters with MAC and USB identity metadata."""
    adapters = []
    hci_macs = hci_macs_from_hciconfig()
    for adapter_path in sorted(Path(bluetooth_root).glob('hci*')):
        usb_parent = _usb_parent(adapter_path)
        vendor_id = _read_text(usb_parent / 'idVendor').lower() if usb_parent else ''
        product_id = _read_text(usb_parent / 'idProduct').lower() if usb_parent else ''
        hci = adapter_path.name
        adapters.append({
            'hci': hci,
            'mac': normalize_mac(_read_text(adapter_path / 'address')) or hci_macs.get(hci, ''),
            'name': _read_text(adapter_path / 'name'),
            'device_path': str((adapter_path / 'device').resolve()) if (adapter_path / 'device').exists() else '',
            'usb_path': str(usb_parent) if usb_parent else '',
            'vendor_id': vendor_id,
            'product_id': product_id,
            'manufacturer': _read_text(usb_parent / 'manufacturer') if usb_parent else '',
            'product': _read_text(usb_parent / 'product') if usb_parent else '',
            'serial': _read_text(usb_parent / 'serial') if usb_parent else '',
        })
    return adapters


def _by_mac(adapters, mac):
    wanted = normalize_mac(mac)
    if not wanted:
        return None
    return next((adapter for adapter in adapters if adapter.get('mac') == wanted), None)


def _role_score(adapter, role):
    usb_id = (
        str(adapter.get('vendor_id', '')).lower(),
        str(adapter.get('product_id', '')).lower(),
    )

    if role == 'gatt':
        if usb_id in GATT_USB_IDS:
            return 100
    elif role == 'sensor':
        if usb_id in SENSOR_USB_IDS:
            return 100
    return 0


def _best_for_role(adapters, role):
    scored = [(adapter, _role_score(adapter, role)) for adapter in adapters]
    scored = [(adapter, score) for adapter, score in scored if score > 0]
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[1], item[0].get('hci', '')))
    if len(scored) > 1 and scored[0][1] == scored[1][1]:
        return None
    return scored[0][0]


def select_adapters(adapters, saved_gatt_mac='', saved_sensor_mac=''):
    """Select adapters by known USB chip role, with saved MACs as a last resort."""
    sensor = _best_for_role(adapters, 'sensor')
    gatt_candidates = [
        adapter for adapter in adapters
        if not sensor or adapter.get('hci') != sensor.get('hci')
    ]
    gatt = _best_for_role(gatt_candidates, 'gatt')

    if not gatt:
        saved_gatt = _by_mac(adapters, saved_gatt_mac)
        if saved_gatt and (not sensor or saved_gatt.get('hci') != sensor.get('hci')):
            gatt = saved_gatt

    if not sensor:
        saved_sensor = _by_mac(adapters, saved_sensor_mac)
        if saved_sensor and (not gatt or saved_sensor.get('hci') != gatt.get('hci')):
            sensor = saved_sensor

    if gatt and sensor and gatt.get('hci') == sensor.get('hci'):
        sensor = None

    used_usb_role = bool(gatt and _role_score(gatt, 'gatt') > 0) or bool(
        sensor and _role_score(sensor, 'sensor') > 0
    )
    return gatt, sensor, used_usb_role
