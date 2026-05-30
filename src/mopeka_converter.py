"""
Mopeka sensor reading converter: mm -> gallons

Converts raw Mopeka ultrasonic readings (mm) into gallons using per-sensor
height offsets and a calibration lookup table.

The calibration table's "Tank Level (in)" column is used directly for the
lookup axis, so the compensated sensor height is interpolated against that
table without inverting it against the tank maximum height.
"""

import csv
import os

# Max tank height in inches (empty tank = sensor reads 0, top of calibration table)
# This comes from the first row of the calibration CSV (empty = max distance from top)
MAX_TANK_HEIGHT_IN = 56.73228346456693

# Calibration table: list of (inches_from_top, gallons) sorted by inches_from_top descending
# Loaded from CSV at startup
_calibration_table = []

# Optional per-tank calibration profiles. Missing profiles fall back to the
# shared calibration table above.
_calibration_profiles = {}
_sensor_calibration_profiles = {}

# Sensor offsets: dict of mopeka_mac_suffix -> height_offset_inches
_sensor_offsets = {}

# BLE MAC to sensor ID mapping (set by the caller for this specific Pi/trailer)
# Maps the actual BLE MAC suffix seen by the scanner to the Mopeka app ID in the CSV
_ble_mac_to_sensor_id = {}

# Data directory path (set by init(), used by reload())
_data_dir = None


def _read_calibration_table(calibration_csv_path):
    table = []
    with open(calibration_csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                inches_from_top = float(row['Tank Level (in)'])
                gallons = float(row['Gallons'])
                table.append((inches_from_top, gallons))
            except (ValueError, KeyError):
                continue

    table.sort(key=lambda x: x[0], reverse=True)
    return table


def load_calibration(calibration_csv_path):
    """Load the calibration lookup table from CSV.

    CSV format: Tank Level (in), Gallons, Tank Size (gal)
    Where 'Tank Level (in)' is distance from TOP of tank.
    """
    global _calibration_table, MAX_TANK_HEIGHT_IN
    _calibration_table = _read_calibration_table(calibration_csv_path)

    if _calibration_table:
        MAX_TANK_HEIGHT_IN = _calibration_table[0][0]

    print(f'Loaded {len(_calibration_table)} calibration points, max height: {MAX_TANK_HEIGHT_IN:.2f} in', flush=True)


def _normalize_tank_name(value):
    tank = str(value or '').strip().lower()
    if tank.startswith('front'):
        return 'front'
    if tank.startswith('back'):
        return 'back'
    return ''


def _safe_profile_key(value):
    key = str(value or '').strip().lower().replace('_', '-')
    safe = []
    for ch in key:
        if ch.isalnum() or ch == '-':
            safe.append(ch)
    return ''.join(safe)


def _profile_key_for(mode, trailer, tank):
    tank = _normalize_tank_name(tank)
    if not tank:
        return ''

    mode = str(mode or 'fleet').strip().lower()
    if mode != 'customer' and trailer not in (None, ''):
        return _safe_profile_key(f'trailer-{trailer}-{tank}')

    return f'customer-{tank}'


def _add_sensor_profile(sensor_id, profile_key):
    sensor_id = str(sensor_id or '').strip().upper()
    profile_key = _safe_profile_key(profile_key)
    if not sensor_id or sensor_id == '---------------' or not profile_key:
        return
    _sensor_calibration_profiles.setdefault(sensor_id, [])
    if profile_key not in _sensor_calibration_profiles[sensor_id]:
        _sensor_calibration_profiles[sensor_id].append(profile_key)


def load_calibration_profiles(data_dir):
    """Load optional per-tank calibration profile CSVs."""
    global _calibration_profiles
    _calibration_profiles = {}

    profile_dir = os.path.join(data_dir, 'calibrations')
    if os.path.isdir(profile_dir):
        for name in sorted(os.listdir(profile_dir)):
            if not name.lower().endswith('.csv'):
                continue
            key = _safe_profile_key(os.path.splitext(name)[0])
            if not key:
                continue
            path = os.path.join(profile_dir, name)
            try:
                table = _read_calibration_table(path)
                if table:
                    _calibration_profiles[key] = table
            except Exception as exc:
                print(f'WARNING: Failed to load calibration profile {name}: {exc}', flush=True)

    # Backward-compatible root-level customer profile names.
    legacy_profiles = {
        'customer-front': 'calibration-front.csv',
        'customer-back': 'calibration-back.csv',
    }
    for key, name in legacy_profiles.items():
        if key in _calibration_profiles:
            continue
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):
            continue
        try:
            table = _read_calibration_table(path)
            if table:
                _calibration_profiles[key] = table
        except Exception as exc:
            print(f'WARNING: Failed to load calibration profile {name}: {exc}', flush=True)

    if _calibration_profiles:
        print(f'Loaded {len(_calibration_profiles)} calibration profiles', flush=True)


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


def load_sensor_calibration_profiles(sensor_csv_path, config=None):
    """Map sensor IDs to optional calibration profile keys."""
    global _sensor_calibration_profiles
    _sensor_calibration_profiles = {}

    config = config or {}
    mode = str(config.get('box_mode') or 'fleet').strip().lower()
    trailer = config.get('assigned_trailer', config.get('trailer'))
    front_id = config.get('front_id')
    back_id = config.get('back_id')
    manual_sensor_ids = {
        str(sensor_id or '').strip().upper()
        for sensor_id in (front_id, back_id)
        if str(sensor_id or '').strip()
    }

    _add_sensor_profile(front_id, _profile_key_for(mode, trailer, 'front'))
    _add_sensor_profile(back_id, _profile_key_for(mode, trailer, 'back'))

    try:
        with open(sensor_csv_path, 'r') as f:
            lines = f.readlines()
    except FileNotFoundError:
        return

    header_idx = 0
    for i, line in enumerate(lines):
        if 'Mopeka ID' in line:
            header_idx = i
            break

    import io
    reader = csv.DictReader(io.StringIO(''.join(lines[header_idx:])))
    for row in reader:
        sensor_id = row.get('Mopeka ID', '').strip()
        tank = _normalize_tank_name(row.get('Tank', ''))
        trailer_num = row.get('Trailer', '').strip()
        if mode == 'customer' and sensor_id.upper() in manual_sensor_ids:
            continue
        if sensor_id and tank and trailer_num:
            _add_sensor_profile(sensor_id, _profile_key_for('fleet', trailer_num, tank))


def _interpolate_gallons(inches_from_top, table=None):
    """Interpolate gallons from the calibration table given inches from top.

    Returns gallons (clamped to 0 - max tank size).
    """
    if table is None:
        table = _calibration_table

    if not table:
        return 0.0

    # Off the top end (above empty mark) = 0 gallons
    if inches_from_top >= table[0][0]:
        return 0.0

    # Off the bottom end (below full mark) = max gallons
    if inches_from_top <= table[-1][0]:
        return table[-1][1]

    # Find the two bracketing points and interpolate
    for i in range(len(table) - 1):
        top_in, top_gal = table[i]
        bot_in, bot_gal = table[i + 1]

        if bot_in <= inches_from_top <= top_in:
            # Linear interpolation
            if top_in == bot_in:
                return top_gal
            ratio = (top_in - inches_from_top) / (top_in - bot_in)
            return top_gal + ratio * (bot_gal - top_gal)

    return 0.0


def _sensor_id_for_mac(sensor_mac_suffix):
    if not sensor_mac_suffix:
        return ''

    mac = str(sensor_mac_suffix).strip().upper()
    return _ble_mac_to_sensor_id.get(mac, mac)


def _calibration_table_for_sensor(sensor_mac_suffix):
    sensor_id = _sensor_id_for_mac(sensor_mac_suffix)
    for profile_key in _sensor_calibration_profiles.get(sensor_id, []):
        table = _calibration_profiles.get(profile_key)
        if table:
            return table
    return _calibration_table


def mm_to_gallons(level_mm, sensor_mac_suffix=None):
    """Convert a Mopeka reading (mm) to gallons.

    Args:
        level_mm: Raw level reading in mm (height of liquid from bottom)
        sensor_mac_suffix: Last 3 octets of BLE MAC for offset lookup (e.g. '0F:37:A5')

    Returns:
        dict with:
            - gallons: float, estimated gallons in tank
            - level_in: float, compensated level in inches used for lookup
            - level_from_top_in: float, retained for compatibility with existing logs/consumers
            - offset_in: float, height offset applied
    """
    # Convert mm to inches
    level_in = level_mm / 25.4

    # Apply height offset if available
    offset_in = 0.0
    if sensor_mac_suffix:
        mac = str(sensor_mac_suffix).strip().upper()
        # Try direct lookup first, then check BLE MAC mapping
        if mac in _sensor_offsets:
            offset_in = _sensor_offsets[mac]
        elif mac in _ble_mac_to_sensor_id:
            mapped_id = _ble_mac_to_sensor_id[mac]
            offset_in = _sensor_offsets.get(mapped_id, 0.0)

    compensated_in = level_in + offset_in

    # The calibration table uses the same height axis as the compensated sensor
    # reading, so use the height directly for interpolation.
    lookup_height_in = max(0.0, compensated_in)

    # Lookup gallons
    gallons = _interpolate_gallons(
        lookup_height_in,
        _calibration_table_for_sensor(sensor_mac_suffix),
    )

    return {
        'gallons': round(gallons, 1),
        'level_in': round(compensated_in, 2),
        'level_from_top_in': round(lookup_height_in, 2),
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

    config = {}
    if os.path.exists(config_path):
        import json
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
        except Exception as e:
            print(f'WARNING: Failed to load mopeka config: {e}', flush=True)

    if os.path.exists(cal_path):
        load_calibration(cal_path)
    else:
        print(f'WARNING: Calibration file not found: {cal_path}', flush=True)

    load_calibration_profiles(data_dir)

    if os.path.exists(sensor_path):
        load_sensor_offsets(sensor_path)
        load_sensor_calibration_profiles(sensor_path, config)
    else:
        print(f'WARNING: Sensor details file not found: {sensor_path}', flush=True)

    # Load BLE MAC mapping from parameter or config file
    if ble_mac_mapping:
        set_ble_mac_mapping(ble_mac_mapping)
    elif config:
        if 'ble_mac_mapping' in config:
            set_ble_mac_mapping(config['ble_mac_mapping'])
        print(f'Loaded mopeka config: trailer {config.get("trailer", "?")}', flush=True)


def reload():
    """Reload calibration data and sensor offsets from CSV files.
    Called by rotorsync_bumble.py after any CSV mutation or trailer change."""
    if _data_dir is None:
        init()
    else:
        cal_path = os.path.join(_data_dir, 'calibration-points-1070gal-tank.csv')
        sensor_path = os.path.join(_data_dir, 'mopeka-sensor-details.csv')
        config_path = os.path.join(_data_dir, 'mopeka_config.json')
        config = {}
        if os.path.exists(config_path):
            import json
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
            except Exception as e:
                print(f'WARNING: Failed to load mopeka config: {e}', flush=True)
        if os.path.exists(cal_path):
            load_calibration(cal_path)
        load_calibration_profiles(_data_dir)
        if os.path.exists(sensor_path):
            load_sensor_offsets(sensor_path)
            load_sensor_calibration_profiles(sensor_path, config)
    print('mopeka_converter: reloaded', flush=True)
