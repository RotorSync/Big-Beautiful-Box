"""
Mopeka sensor reading converter: mm -> gallons

Converts raw Mopeka ultrasonic distance readings (mm) into gallons
using per-sensor height offsets and a calibration lookup table.

The Mopeka sensor reads the height of liquid from the bottom of the tank (in mm).
The calibration table maps "inches from top of tank" to gallons.
So we convert: mm -> inches from bottom -> inches from top -> interpolate gallons.
"""

import csv
import os

# Max tank height in inches (empty tank = sensor reads 0, top of calibration table)
# This comes from the first row of the calibration CSV (empty = max distance from top)
MAX_TANK_HEIGHT_IN = 56.73228346456693

# Calibration table: list of (inches_from_top, gallons) sorted by inches_from_top descending
# Loaded from CSV at startup
_calibration_table = []

# Sensor offsets: dict of mopeka_mac_suffix -> height_offset_inches
_sensor_offsets = {}

# BLE MAC to sensor ID mapping (set by the caller for this specific Pi/trailer)
# Maps the actual BLE MAC suffix seen by the scanner to the Mopeka app ID in the CSV
_ble_mac_to_sensor_id = {}

# Data directory path (set by init(), used by reload())
_data_dir = None


def load_calibration(calibration_csv_path):
    """Load the calibration lookup table from CSV.

    CSV format: Tank Level (in), Gallons, Tank Size (gal)
    Where 'Tank Level (in)' is distance from TOP of tank.
    """
    global _calibration_table, MAX_TANK_HEIGHT_IN
    _calibration_table = []

    with open(calibration_csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                inches_from_top = float(row['Tank Level (in)'])
                gallons = float(row['Gallons'])
                _calibration_table.append((inches_from_top, gallons))
            except (ValueError, KeyError):
                continue

    # Sort by inches_from_top descending (empty tank first)
    _calibration_table.sort(key=lambda x: x[0], reverse=True)

    if _calibration_table:
        MAX_TANK_HEIGHT_IN = _calibration_table[0][0]

    print(f'Loaded {len(_calibration_table)} calibration points, max height: {MAX_TANK_HEIGHT_IN:.2f} in', flush=True)


def load_sensor_offsets(sensor_csv_path):
    """Load per-sensor height offsets from CSV.

    CSV has columns: Man, Trailer, Tank, Center Sump?, Height Offset,
                     Mopeka Name in app, Mopeka ID, MQTT Topic for app, Added to app

    We key by the last 3 octets of the Mopeka BLE MAC (the ID column).
    """
    global _sensor_offsets
    _sensor_offsets = {}

    with open(sensor_csv_path, 'r') as f:
        # Skip blank rows at top of file
        lines = f.readlines()
        # Find the header row (first row with 'Mopeka ID' in it)
        header_idx = 0
        for i, line in enumerate(lines):
            if 'Mopeka ID' in line:
                header_idx = i
                break

        import io
        reader = csv.DictReader(io.StringIO(''.join(lines[header_idx:])))
        for row in reader:
            try:
                mac_suffix = row.get('Mopeka ID', '').strip()
                offset_str = row.get('Height Offset', '').strip()
                if mac_suffix and offset_str and mac_suffix != '---------------':
                    _sensor_offsets[mac_suffix.upper()] = float(offset_str)
            except (ValueError, KeyError):
                continue

    print(f'Loaded {len(_sensor_offsets)} sensor offsets', flush=True)


def _interpolate_gallons(inches_from_top):
    """Interpolate gallons from the calibration table given inches from top.

    Returns gallons (clamped to 0 - max tank size).
    """
    if not _calibration_table:
        return 0.0

    # Off the top end (above empty mark) = 0 gallons
    if inches_from_top >= _calibration_table[0][0]:
        return 0.0

    # Off the bottom end (below full mark) = max gallons
    if inches_from_top <= _calibration_table[-1][0]:
        return _calibration_table[-1][1]

    # Find the two bracketing points and interpolate
    for i in range(len(_calibration_table) - 1):
        top_in, top_gal = _calibration_table[i]
        bot_in, bot_gal = _calibration_table[i + 1]

        if bot_in <= inches_from_top <= top_in:
            # Linear interpolation
            if top_in == bot_in:
                return top_gal
            ratio = (top_in - inches_from_top) / (top_in - bot_in)
            return top_gal + ratio * (bot_gal - top_gal)

    return 0.0


def mm_to_gallons(level_mm, sensor_mac_suffix=None):
    """Convert a Mopeka reading (mm from bottom) to gallons.

    Args:
        level_mm: Raw level reading in mm (height of liquid from bottom)
        sensor_mac_suffix: Last 3 octets of BLE MAC for offset lookup (e.g. '0F:37:A5')

    Returns:
        dict with:
            - gallons: float, estimated gallons in tank
            - level_in: float, compensated level in inches from bottom
            - level_from_top_in: float, distance from top in inches (for calibration lookup)
            - offset_in: float, height offset applied
    """
    # Convert mm to inches
    level_in = level_mm / 25.4

    # Apply height offset if available
    offset_in = 0.0
    if sensor_mac_suffix:
        mac = sensor_mac_suffix.upper()
        # Try direct lookup first, then check BLE MAC mapping
        if mac in _sensor_offsets:
            offset_in = _sensor_offsets[mac]
        elif mac in _ble_mac_to_sensor_id:
            mapped_id = _ble_mac_to_sensor_id[mac]
            offset_in = _sensor_offsets.get(mapped_id, 0.0)

    compensated_in = level_in + offset_in

    # Convert from "inches from bottom" to "inches from top"
    inches_from_top = MAX_TANK_HEIGHT_IN - compensated_in

    # Clamp - can't be negative (sensor reading above tank)
    inches_from_top = max(0.0, inches_from_top)

    # Lookup gallons
    gallons = _interpolate_gallons(inches_from_top)

    return {
        'gallons': round(gallons, 1),
        'level_in': round(compensated_in, 2),
        'level_from_top_in': round(inches_from_top, 2),
        'offset_in': offset_in,
    }


def set_ble_mac_mapping(mapping):
    """Set BLE MAC suffix to Mopeka sensor ID mapping.

    Args:
        mapping: dict of BLE_MAC_SUFFIX -> MOPEKA_APP_ID
                 e.g. {'0F:37:A5': '3A:28:34', 'F7:D0:22': '96:BB:5D'}
    """
    global _ble_mac_to_sensor_id
    _ble_mac_to_sensor_id = {k.upper(): v.upper() for k, v in mapping.items()}
    print(f'Set {len(_ble_mac_to_sensor_id)} BLE MAC -> sensor ID mappings', flush=True)


def init(data_dir=None, ble_mac_mapping=None):
    """Initialize the converter by loading calibration data.

    Args:
        data_dir: Directory containing the CSV files.
                  Defaults to 'mopeka/' relative to the BBB root.
        ble_mac_mapping: Optional dict mapping BLE MAC suffixes to Mopeka app IDs.
                         If None, tries to load from mopeka_config.json in data_dir.
    """
    global _data_dir

    if data_dir is None:
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'mopeka')

    _data_dir = data_dir

    cal_path = os.path.join(data_dir, 'calibration-points-1070gal-tank.csv')
    sensor_path = os.path.join(data_dir, 'mopeka-sensor-details.csv')
    config_path = os.path.join(data_dir, 'mopeka_config.json')

    if os.path.exists(cal_path):
        load_calibration(cal_path)
    else:
        print(f'WARNING: Calibration file not found: {cal_path}', flush=True)

    if os.path.exists(sensor_path):
        load_sensor_offsets(sensor_path)
    else:
        print(f'WARNING: Sensor details file not found: {sensor_path}', flush=True)

    # Load BLE MAC mapping from parameter or config file
    if ble_mac_mapping:
        set_ble_mac_mapping(ble_mac_mapping)
    elif os.path.exists(config_path):
        import json
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            if 'ble_mac_mapping' in config:
                set_ble_mac_mapping(config['ble_mac_mapping'])
            print(f'Loaded mopeka config: trailer {config.get("trailer", "?")}', flush=True)
        except Exception as e:
            print(f'WARNING: Failed to load mopeka config: {e}', flush=True)


def reload():
    """Reload calibration data and sensor offsets from CSV files.
    Called by rotorsync_bumble.py after any CSV mutation or trailer change."""
    if _data_dir is None:
        init()
    else:
        cal_path = os.path.join(_data_dir, 'calibration-points-1070gal-tank.csv')
        sensor_path = os.path.join(_data_dir, 'mopeka-sensor-details.csv')
        if os.path.exists(cal_path):
            load_calibration(cal_path)
        if os.path.exists(sensor_path):
            load_sensor_offsets(sensor_path)
    print('mopeka_converter: reloaded', flush=True)
