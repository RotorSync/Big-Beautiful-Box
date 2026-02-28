#!/usr/bin/env python3
"""
Rotorsync BLE GATT Server using Bumble (bypasses BlueZ)
With live sensor reading via BlueZ (separate adapter)
Sends commands to dashboard via localhost socket
Auto-recovery for sensor connections
Exposes requested/actual gallons from dashboard

"""
import asyncio
import csv
import json
import logging
import os
import subprocess
import socket
import time

logging.basicConfig(level=logging.INFO)

from bumble.device import Device
from bumble.host import Host
from bumble.transport.hci_socket import open_hci_socket_transport
from bumble.gatt import Service, Characteristic, CharacteristicValue
from bumble.core import UUID, AdvertisingData
# Mopeka gallon conversion
from src.mopeka_converter import mm_to_gallons, init as mopeka_init, reload as mopeka_reload

# Configuration - Use MAC addresses to find adapters dynamically
GATT_ADAPTER_MAC = '00:01:95:9C:C7:F5'  # CSR/Sena for GATT - phones connect here
SENSOR_ADAPTER_MAC = 'BC:FC:E7:2D:86:7B'  # Realtek for sensors

# Socket connection to dashboard
DASHBOARD_HOST = '127.0.0.1'
DASHBOARD_PORT = 9999

BMS_MAC = 'A5:C2:37:2B:32:91'
MOPEKA1_MAC_SUFFIX = ''  # Set by trailer selection (defa) or restored from mopeka_config.json
MOPEKA2_MAC_SUFFIX = ''
# Timing configuration
SCAN_TIMEOUT = 5
BMS_TIMEOUT = 8
SCAN_INTERVAL = 15
BMS_READ_INTERVAL = 2
STATUS_POLL_INTERVAL = 0.2  # Poll dashboard for status every 2 seconds

# Recovery settings
MAX_CONSECUTIVE_FAILURES = 5
ADAPTER_RESET_COOLDOWN = 30

# UUIDs
SERVICE_UUID = UUID('12345678-1234-5678-1234-56789abcdef0')
BMS_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdef1')
MOPEKA1_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdef2')
MOPEKA2_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdef3')
PUMP_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdef4')
GALLONS_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdef5')
REQUESTED_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdef6')  # Requested gallons
ACTUAL_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdef7')     # Actual gallons
HISTORY_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdef8')    # Last 5 fills
BATCHMIX_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdef9')  # Batch mix data from iPad
TRAILER_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdefa')   # Trailer selection
CONFIG_CMD_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdefb') # Config command write
CONFIG_DATA_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdefc') # Config data read

# File paths for mopeka data
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MOPEKA_DIR = os.path.join(SCRIPT_DIR, 'mopeka')
SENSOR_CSV_PATH = os.path.join(MOPEKA_DIR, 'mopeka-sensor-details.csv')
CALIBRATION_CSV_PATH = os.path.join(MOPEKA_DIR, 'calibration-points-1070gal-tank.csv')
MOPEKA_CONFIG_PATH = os.path.join(MOPEKA_DIR, 'mopeka_config.json')

# Sensor data with timestamps
sensor_data = {
    'bms': {'voltage': 0, 'soc': 0, 'last_update': 0},
    'mopeka1': {'level_mm': 0, 'quality': 0, 'last_update': 0},
    'mopeka2': {'level_mm': 0, 'quality': 0, 'last_update': 0}
}

# Dashboard status
dashboard_status = {
    'requested': 0.0,
    'actual': 0.0,
    'mode': 'fill',
    'history': '',
    'last_update': 0
}

# Config command state
config_response = '{"ok":false,"error":"No command issued"}'
config_response_pages = []  # Pre-computed pages for paginated responses

# Chunked config command buffer
config_cmd_chunks = {}
config_cmd_chunk_timeout = 30

def send_dashboard_command(cmd):
    """Send command to dashboard via socket"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect((DASHBOARD_HOST, DASHBOARD_PORT))
            s.send(f'{cmd}\n'.encode())
            response = s.recv(1024).decode().strip()
            print(f'Dashboard command: {cmd} -> {response}', flush=True)
            return response
    except Exception as e:
        print(f'Dashboard command error: {e}', flush=True)
        return None

def query_fill_history():
    """Query dashboard for last 5 fills"""
    global dashboard_status
    response = send_dashboard_command('HISTORY')
    if response and response.startswith('HIST:'):
        dashboard_status['history'] = response[5:]
        return True
    return False

def query_dashboard_status():
    """Query dashboard for current requested/actual gallons"""
    global dashboard_status
    response = send_dashboard_command('STATUS')
    if response and response.startswith('REQ:'):
        try:
            # Parse "REQ:10.0|ACT:5.5|MODE:fill"
            parts = response.split('|')
            for part in parts:
                if part.startswith('REQ:'):
                    dashboard_status['requested'] = float(part[4:])
                elif part.startswith('ACT:'):
                    dashboard_status['actual'] = float(part[4:])
                elif part.startswith('MODE:'):
                    dashboard_status['mode'] = part[5:]
            dashboard_status['last_update'] = time.time()
            return True
        except Exception as e:
            print(f'Status parse error: {e}', flush=True)
    return False

def find_adapter_by_mac(mac):
    """Find hci index by MAC address"""
    result = subprocess.run(['hciconfig', '-a'], capture_output=True, text=True)
    current_hci = None
    for line in result.stdout.split('\n'):
        if line.startswith('hci'):
            current_hci = line.split(':')[0]
        if mac.upper() in line.upper():
            return current_hci
    return None

def reset_adapter(adapter):
    """Reset Bluetooth adapter"""
    print(f'Resetting adapter {adapter}...', flush=True)
    try:
        subprocess.run(['hciconfig', adapter, 'down'], capture_output=True, timeout=5)
        time.sleep(1)
        subprocess.run(['hciconfig', adapter, 'up'], capture_output=True, timeout=5)
        time.sleep(2)
        print(f'Adapter {adapter} reset complete', flush=True)
        return True
    except Exception as e:
        print(f'Adapter reset error: {e}', flush=True)
        return False

def make_read_handler(data_key):
    def read_value(connection):
        data = sensor_data[data_key].copy()
        data.pop('last_update', None)
        value = json.dumps(data)
        print(f'ReadValue {data_key}: {value}', flush=True)
        return bytes(value, 'utf-8')
    return read_value

def make_history_read_handler():
    def read_value(connection):
        value = dashboard_status['history']
        print(f'ReadValue history: {value[:50]}...', flush=True)
        return bytes(value, 'utf-8')
    return read_value

def make_dashboard_read_handler(field):
    def read_value(connection):
        value = str(dashboard_status[field])
        print(f'ReadValue {field}: {value}', flush=True)
        return bytes(value, 'utf-8')
    return read_value

def pump_write_handler(connection, value):
    """Handle pump control writes. Write '1' or 'PS' to stop pump."""
    cmd = value.decode('utf-8').strip().upper()
    print(f'Pump write: {cmd}', flush=True)
    if cmd in ['1', 'PS', 'STOP']:
        send_dashboard_command('PS')
    return

def gallons_write_handler(connection, value):
    """Handle gallons adjustment. Write '+1', '-1', '+10', '-10'."""
    cmd = value.decode('utf-8').strip()
    print(f'Gallons write: {cmd}', flush=True)

    if cmd in ['+1', '-1', '+10', '-10']:
        send_dashboard_command(cmd)
        # Update local status after change
        asyncio.get_event_loop().call_soon(query_dashboard_status)
    return

# Chunked data buffer for batch mix (handles large payloads)
batchmix_chunks = {}
batchmix_chunk_timeout = 30  # Seconds before incomplete chunks expire

def batchmix_write_handler(connection, value):
    """Handle batch mix data from iPad. Supports chunked or single-write JSON.

    Chunked format: CHUNK:X/Y:data
      - X = chunk number (1-based)
      - Y = total chunks
      - data = partial JSON

    Single write: raw JSON (if data fits in one BLE write)
    """
    global batchmix_chunks

    try:
        data_str = value.decode('utf-8').strip()

        # Check if this is chunked data
        if data_str.startswith('CHUNK:'):
            # Parse chunk header: CHUNK:X/Y:data
            parts = data_str.split(':', 2)
            if len(parts) >= 3:
                chunk_info = parts[1]  # "X/Y"
                chunk_data = parts[2]  # The actual data

                chunk_num, total_chunks = map(int, chunk_info.split('/'))

                print(f'BatchMix chunk {chunk_num}/{total_chunks} ({len(chunk_data)} bytes)', flush=True)

                # Use single global buffer (simplified - one sender at a time)
                # Reset buffer if total_chunks changed (new transmission)
                if 'total' not in batchmix_chunks or batchmix_chunks['total'] != total_chunks:
                    batchmix_chunks = {
                        'chunks': {},
                        'total': total_chunks,
                        'timestamp': time.time()
                    }

                # Store this chunk
                batchmix_chunks['chunks'][chunk_num] = chunk_data
                batchmix_chunks['timestamp'] = time.time()

                print(f'BatchMix: have {len(batchmix_chunks["chunks"])}/{total_chunks} chunks', flush=True)

                # Check if we have all chunks
                if len(batchmix_chunks['chunks']) == total_chunks:
                    # Assemble complete JSON
                    assembled = ''
                    for i in range(1, total_chunks + 1):
                        if i in batchmix_chunks['chunks']:
                            assembled += batchmix_chunks['chunks'][i]
                        else:
                            print(f'BatchMix: missing chunk {i}!', flush=True)
                            return

                    print(f'BatchMix complete: {len(assembled)} bytes from {total_chunks} chunks', flush=True)

                    # Strip newlines for socket transmission
                    compact_json = assembled.replace('\n', '').replace('\r', '')

                    # Validate the data before sending
                    try:
                        import json
                        data = json.loads(compact_json)
                        product_count = data.get('product_count', 0)
                        products = data.get('products', [])
                        actual_count = len(products)

                        if product_count != actual_count:
                            error_msg = f"Product count mismatch: expected {product_count}, got {actual_count}"
                            print(f'BatchMix ERROR: {error_msg}', flush=True)
                            send_dashboard_command(f'BATCHMIX_ERROR:{error_msg}')
                        else:
                            print(f'BatchMix validated: {actual_count} products', flush=True)
                            send_dashboard_command(f'BATCHMIX:{compact_json}')
                    except json.JSONDecodeError as je:
                        error_msg = f"Invalid JSON: {je}"
                        print(f'BatchMix ERROR: {error_msg}', flush=True)
                        send_dashboard_command(f'BATCHMIX_ERROR:{error_msg}')

                    # Clear buffer
                    batchmix_chunks = {}

        else:
            # Single write (no chunking) - data fits in one BLE write
            print(f'BatchMix single write: {len(data_str)} bytes', flush=True)
            # Strip newlines for socket transmission
            compact_json = data_str.replace('\n', '').replace('\r', '')

            # Validate the data before sending
            try:
                import json
                data = json.loads(compact_json)
                product_count = data.get('product_count', 0)
                products = data.get('products', [])
                actual_count = len(products)

                if product_count != actual_count:
                    error_msg = f"Product count mismatch: expected {product_count}, got {actual_count}"
                    print(f'BatchMix ERROR: {error_msg}', flush=True)
                    send_dashboard_command(f'BATCHMIX_ERROR:{error_msg}')
                else:
                    print(f'BatchMix validated: {actual_count} products', flush=True)
                    send_dashboard_command(f'BATCHMIX:{compact_json}')
            except json.JSONDecodeError as je:
                error_msg = f"Invalid JSON: {je}"
                print(f'BatchMix ERROR: {error_msg}', flush=True)
                send_dashboard_command(f'BATCHMIX_ERROR:{error_msg}')

    except Exception as e:
        print(f'BatchMix error: {e}', flush=True)
    return


# =============================================================================
# CSV + Config Helper Functions
# =============================================================================

SENSOR_CSV_HEADER = ['Man', 'Trailer', 'Tank', 'Center Sump?', 'Height Offset',
                     'Mopeka Name in app', 'Mopeka ID', 'MQTT Topic for app', 'Added to app']

def load_sensor_csv():
    """Parse sensor CSV. 4 blank preamble rows, header on row 5.
    Man column only on Front rows - carry forward for Back rows."""
    sensors = []
    try:
        with open(SENSOR_CSV_PATH, 'r', newline='') as f:
            reader = csv.reader(f)
            for _ in range(4):
                next(reader)
            header = next(reader)
            current_man = ''
            for row in reader:
                if not row or len(row) < 2 or not row[1].strip():
                    continue
                d = {}
                for i, h in enumerate(header):
                    d[h.strip()] = row[i].strip() if i < len(row) else ''
                if d.get('Man'):
                    current_man = d['Man']
                else:
                    d['Man'] = current_man
                sensors.append(d)
    except FileNotFoundError:
        print(f'Sensor CSV not found: {SENSOR_CSV_PATH}', flush=True)
    return sensors


def save_sensor_csv(sensors):
    """Write sensors back to CSV preserving format (4 blank rows, header, data)."""
    with open(SENSOR_CSV_PATH, 'w', newline='') as f:
        writer = csv.writer(f)
        for _ in range(4):
            writer.writerow([''] * len(SENSOR_CSV_HEADER))
        writer.writerow(SENSOR_CSV_HEADER)
        last_man = ''
        for s in sensors:
            row = []
            for h in SENSOR_CSV_HEADER:
                val = s.get(h, '')
                if h == 'Man':
                    if val == last_man:
                        row.append('')
                    else:
                        row.append(val)
                        last_man = val
                else:
                    row.append(val)
            writer.writerow(row)


def load_calibration_csv():
    """Load calibration CSV. Standard header, no preamble."""
    points = []
    try:
        with open(CALIBRATION_CSV_PATH, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                points.append({
                    'tank_level_in': float(row['Tank Level (in)']),
                    'gallons': float(row['Gallons']),
                    'tank_size': float(row['Tank Size (gal)'])
                })
    except FileNotFoundError:
        print(f'Calibration CSV not found: {CALIBRATION_CSV_PATH}', flush=True)
    return points


def save_calibration_csv(points):
    """Write calibration points back to CSV, sorted descending by tank level."""
    points.sort(key=lambda p: p['tank_level_in'], reverse=True)
    with open(CALIBRATION_CSV_PATH, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Tank Level (in)', 'Gallons', 'Tank Size (gal)'])
        for p in points:
            writer.writerow([p['tank_level_in'], p['gallons'], p['tank_size']])


def load_config():
    """Load mopeka_config.json."""
    try:
        with open(MOPEKA_CONFIG_PATH, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {'trailer': None}


def save_config(cfg):
    """Write mopeka_config.json."""
    os.makedirs(os.path.dirname(MOPEKA_CONFIG_PATH), exist_ok=True)
    with open(MOPEKA_CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)


# =============================================================================
# Trailer Selection Logic
# =============================================================================

def apply_trailer(trailer_num):
    """Look up trailer in sensor CSV, set MOPEKA1/2_MAC_SUFFIX globals,
    save config, reload mopeka_converter, return trailer info dict."""
    global MOPEKA1_MAC_SUFFIX, MOPEKA2_MAC_SUFFIX

    sensors = load_sensor_csv()
    trailer_sensors = [s for s in sensors if str(s.get('Trailer')) == str(trailer_num)]

    if not trailer_sensors:
        return None

    front = next((s for s in trailer_sensors if s.get('Tank') == 'Front'), None)
    back = next((s for s in trailer_sensors if s.get('Tank') == 'Back'), None)
    man = trailer_sensors[0].get('Man', '')

    front_id = front['Mopeka ID'] if front else '---------------'
    back_id = back['Mopeka ID'] if back else '---------------'

    def parse_offset(sensor):
        if sensor and sensor.get('Height Offset'):
            try:
                return float(sensor['Height Offset'])
            except ValueError:
                pass
        return 0.0

    front_offset = parse_offset(front)
    back_offset = parse_offset(back)

    # Update globals for the scanner
    if front_id != '---------------':
        MOPEKA1_MAC_SUFFIX = front_id
    if back_id != '---------------':
        MOPEKA2_MAC_SUFFIX = back_id

    # Persist to config
    cfg = {
        'trailer': trailer_num,
        'front_id': front_id,
        'back_id': back_id
    }
    save_config(cfg)

    # Reload mopeka_converter so offsets match new sensors
    try:
        mopeka_reload()
    except Exception as e:
        print(f'mopeka_converter reload: {e}', flush=True)

    info = {
        'trailer': trailer_num,
        'man': man,
        'front': {'id': front_id, 'offset': front_offset},
        'back': {'id': back_id, 'offset': back_offset}
    }

    print(f'Applied trailer {trailer_num}: front={front_id} back={back_id}', flush=True)
    return info


def restore_trailer_config():
    """Restore trailer config on startup from mopeka_config.json."""
    cfg = load_config()
    trailer_num = cfg.get('trailer')
    if trailer_num is not None:
        result = apply_trailer(trailer_num)
        if result:
            print(f'Restored trailer {trailer_num} from config', flush=True)
        else:
            print(f'Failed to restore trailer {trailer_num}', flush=True)


# =============================================================================
# Pagination
# =============================================================================

def paginate_response(items, page_size_bytes=450):
    """Split items into pages that fit in BLE reads (~512 byte limit).
    Returns list of page JSON strings."""
    if not items:
        return [json.dumps({'page': 1, 'total_pages': 1, 'total_items': 0, 'items': []})]

    pages_items = []
    current_page = []
    overhead = 60
    current_size = overhead

    for item in items:
        item_json = json.dumps(item, separators=(',', ':'))
        item_size = len(item_json) + 1
        if current_size + item_size > page_size_bytes and current_page:
            pages_items.append(current_page)
            current_page = []
            current_size = overhead
        current_page.append(item)
        current_size += item_size

    if current_page:
        pages_items.append(current_page)

    total_pages = len(pages_items)
    total_items = len(items)

    result = []
    for i, page_items in enumerate(pages_items):
        result.append(json.dumps({
            'page': i + 1,
            'total_pages': total_pages,
            'total_items': total_items,
            'items': page_items
        }, separators=(',', ':')))

    return result


# =============================================================================
# Command Processor
# =============================================================================

def process_config_command(cmd_str):
    """Parse JSON command, dispatch by op field. Sets config_response."""
    global config_response, config_response_pages

    try:
        cmd = json.loads(cmd_str)
    except json.JSONDecodeError as e:
        config_response = json.dumps({'ok': False, 'error': f'Invalid JSON: {e}'})
        config_response_pages = []
        return

    op = cmd.get('op', '')
    print(f'Config command: {op}', flush=True)

    try:
        if op == 'LIST_TRAILERS':
            _cmd_list_trailers()
        elif op == 'LIST_SENSORS':
            _cmd_list_sensors(cmd)
        elif op == 'ADD_SENSOR':
            _cmd_add_sensor(cmd)
        elif op == 'UPDATE_SENSOR':
            _cmd_update_sensor(cmd)
        elif op == 'DELETE_SENSOR':
            _cmd_delete_sensor(cmd)
        elif op == 'LIST_CALIBRATION':
            _cmd_list_calibration()
        elif op == 'ADD_CALIBRATION':
            _cmd_add_calibration(cmd)
        elif op == 'UPDATE_CALIBRATION':
            _cmd_update_calibration(cmd)
        elif op == 'DELETE_CALIBRATION':
            _cmd_delete_calibration(cmd)
        elif op == 'PAGE':
            _cmd_page(cmd)
        else:
            config_response = json.dumps({'ok': False, 'error': f'Unknown op: {op}'})
            config_response_pages = []
    except Exception as e:
        print(f'Config command error: {e}', flush=True)
        config_response = json.dumps({'ok': False, 'error': str(e)})
        config_response_pages = []


def _cmd_list_trailers():
    global config_response, config_response_pages
    sensors = load_sensor_csv()

    trailers = {}
    for s in sensors:
        t = s.get('Trailer', '')
        if t not in trailers:
            trailers[t] = {'trailer': int(t) if t.isdigit() else t, 'man': s.get('Man', '')}
        tank = s.get('Tank', '')
        mid = s.get('Mopeka ID', '')
        if tank == 'Front':
            trailers[t]['front'] = mid
        elif tank == 'Back':
            trailers[t]['back'] = mid

    items = sorted(trailers.values(), key=lambda x: x.get('trailer', 0))
    pages = paginate_response(items)
    config_response_pages = pages
    config_response = pages[0] if pages else json.dumps({'page': 1, 'total_pages': 1, 'total_items': 0, 'items': []})


def _cmd_list_sensors(cmd):
    global config_response, config_response_pages
    sensors = load_sensor_csv()

    trailer_filter = cmd.get('trailer')
    if trailer_filter is not None:
        sensors = [s for s in sensors if str(s.get('Trailer')) == str(trailer_filter)]

    items = []
    for s in sensors:
        item = {
            'man': s.get('Man', ''),
            'trailer': int(s['Trailer']) if s.get('Trailer', '').isdigit() else s.get('Trailer', ''),
            'tank': s.get('Tank', ''),
            'id': s.get('Mopeka ID', ''),
            'offset': s.get('Height Offset', ''),
            'name': s.get('Mopeka Name in app', ''),
        }
        items.append(item)

    pages = paginate_response(items)
    config_response_pages = pages
    config_response = pages[0] if pages else json.dumps({'page': 1, 'total_pages': 1, 'total_items': 0, 'items': []})


def _cmd_add_sensor(cmd):
    global config_response, config_response_pages
    data = cmd.get('data')
    if not data:
        config_response = json.dumps({'ok': False, 'error': 'Missing data field'})
        config_response_pages = []
        return

    sensors = load_sensor_csv()

    new_sensor = {}
    field_map = {
        'man': 'Man', 'trailer': 'Trailer', 'tank': 'Tank',
        'center_sump': 'Center Sump?', 'height_offset': 'Height Offset',
        'name': 'Mopeka Name in app', 'id': 'Mopeka ID',
        'mqtt_topic': 'MQTT Topic for app', 'added_to_app': 'Added to app'
    }
    for json_key, csv_key in field_map.items():
        if json_key in data:
            new_sensor[csv_key] = str(data[json_key])

    if 'Mopeka ID' not in new_sensor or 'Trailer' not in new_sensor or 'Tank' not in new_sensor:
        config_response = json.dumps({'ok': False, 'error': 'Required: id, trailer, tank'})
        config_response_pages = []
        return

    sensors.append(new_sensor)
    tank_order = {'Front': 0, 'Back': 1}
    sensors.sort(key=lambda s: (int(s['Trailer']) if s.get('Trailer', '').isdigit() else 999,
                                tank_order.get(s.get('Tank', ''), 2)))
    save_sensor_csv(sensors)
    _reload_converter()

    config_response = json.dumps({'ok': True, 'op': 'ADD_SENSOR', 'id': new_sensor.get('Mopeka ID', '')})
    config_response_pages = []


def _cmd_update_sensor(cmd):
    global config_response, config_response_pages
    sensor_id = cmd.get('id')
    data = cmd.get('data')
    if not sensor_id or not data:
        config_response = json.dumps({'ok': False, 'error': 'Required: id, data'})
        config_response_pages = []
        return

    sensors = load_sensor_csv()
    found = False
    field_map = {
        'man': 'Man', 'trailer': 'Trailer', 'tank': 'Tank',
        'center_sump': 'Center Sump?', 'height_offset': 'Height Offset',
        'name': 'Mopeka Name in app', 'id': 'Mopeka ID',
        'mqtt_topic': 'MQTT Topic for app', 'added_to_app': 'Added to app'
    }
    for s in sensors:
        if s.get('Mopeka ID') == sensor_id:
            for json_key, csv_key in field_map.items():
                if json_key in data:
                    s[csv_key] = str(data[json_key])
            found = True
            break

    if not found:
        config_response = json.dumps({'ok': False, 'error': f'Sensor {sensor_id} not found'})
        config_response_pages = []
        return

    save_sensor_csv(sensors)
    _reload_converter()

    config_response = json.dumps({'ok': True, 'op': 'UPDATE_SENSOR', 'id': sensor_id})
    config_response_pages = []


def _cmd_delete_sensor(cmd):
    global config_response, config_response_pages
    sensor_id = cmd.get('id')
    if not sensor_id:
        config_response = json.dumps({'ok': False, 'error': 'Required: id'})
        config_response_pages = []
        return

    sensors = load_sensor_csv()
    original_len = len(sensors)
    sensors = [s for s in sensors if s.get('Mopeka ID') != sensor_id]

    if len(sensors) == original_len:
        config_response = json.dumps({'ok': False, 'error': f'Sensor {sensor_id} not found'})
        config_response_pages = []
        return

    save_sensor_csv(sensors)
    _reload_converter()

    config_response = json.dumps({'ok': True, 'op': 'DELETE_SENSOR', 'id': sensor_id})
    config_response_pages = []


def _cmd_list_calibration():
    global config_response, config_response_pages
    points = load_calibration_csv()

    items = []
    for i, p in enumerate(points):
        items.append({
            'index': i,
            'tank_level_in': p['tank_level_in'],
            'gallons': p['gallons'],
            'tank_size': p['tank_size']
        })

    pages = paginate_response(items)
    config_response_pages = pages
    config_response = pages[0] if pages else json.dumps({'page': 1, 'total_pages': 1, 'total_items': 0, 'items': []})


def _cmd_add_calibration(cmd):
    global config_response, config_response_pages
    data = cmd.get('data')
    if not data:
        config_response = json.dumps({'ok': False, 'error': 'Missing data field'})
        config_response_pages = []
        return

    if 'tank_level_in' not in data or 'gallons' not in data:
        config_response = json.dumps({'ok': False, 'error': 'Required: tank_level_in, gallons'})
        config_response_pages = []
        return

    points = load_calibration_csv()
    new_point = {
        'tank_level_in': float(data['tank_level_in']),
        'gallons': float(data['gallons']),
        'tank_size': float(data.get('tank_size', 1070.0))
    }
    points.append(new_point)
    save_calibration_csv(points)
    _reload_converter()

    config_response = json.dumps({'ok': True, 'op': 'ADD_CALIBRATION'})
    config_response_pages = []


def _cmd_update_calibration(cmd):
    global config_response, config_response_pages
    index = cmd.get('index')
    data = cmd.get('data')
    if index is None or not data:
        config_response = json.dumps({'ok': False, 'error': 'Required: index, data'})
        config_response_pages = []
        return

    points = load_calibration_csv()
    if index < 0 or index >= len(points):
        config_response = json.dumps({'ok': False, 'error': f'Index {index} out of range (0-{len(points)-1})'})
        config_response_pages = []
        return

    for key in ('tank_level_in', 'gallons', 'tank_size'):
        if key in data:
            points[index][key] = float(data[key])

    save_calibration_csv(points)
    _reload_converter()

    config_response = json.dumps({'ok': True, 'op': 'UPDATE_CALIBRATION', 'index': index})
    config_response_pages = []


def _cmd_delete_calibration(cmd):
    global config_response, config_response_pages
    index = cmd.get('index')
    if index is None:
        config_response = json.dumps({'ok': False, 'error': 'Required: index'})
        config_response_pages = []
        return

    points = load_calibration_csv()
    if index < 0 or index >= len(points):
        config_response = json.dumps({'ok': False, 'error': f'Index {index} out of range (0-{len(points)-1})'})
        config_response_pages = []
        return

    points.pop(index)
    save_calibration_csv(points)
    _reload_converter()

    config_response = json.dumps({'ok': True, 'op': 'DELETE_CALIBRATION', 'index': index})
    config_response_pages = []


def _cmd_page(cmd):
    global config_response
    page = cmd.get('page', 1)
    if not config_response_pages:
        config_response = json.dumps({'ok': False, 'error': 'No paginated data available'})
        return

    if page < 1 or page > len(config_response_pages):
        config_response = json.dumps({'ok': False, 'error': f'Page {page} out of range (1-{len(config_response_pages)})'})
        return

    config_response = config_response_pages[page - 1]


def _reload_converter():
    """Reload mopeka_converter after CSV changes."""
    try:
        mopeka_reload()
    except Exception as e:
        print(f'mopeka_converter reload: {e}', flush=True)


# =============================================================================
# BLE Characteristic Handlers for Trailer Config
# =============================================================================

def trailer_read_handler(connection):
    """Read current trailer config as JSON."""
    cfg = load_config()
    trailer_num = cfg.get('trailer')
    if trailer_num is None:
        value = json.dumps({'trailer': None})
        print(f'ReadValue trailer: {value}', flush=True)
        return value.encode('utf-8')

    sensors = load_sensor_csv()
    trailer_sensors = [s for s in sensors if str(s.get('Trailer')) == str(trailer_num)]

    front = next((s for s in trailer_sensors if s.get('Tank') == 'Front'), None)
    back = next((s for s in trailer_sensors if s.get('Tank') == 'Back'), None)
    man = trailer_sensors[0].get('Man', '') if trailer_sensors else ''

    def get_offset(sensor):
        if sensor and sensor.get('Height Offset'):
            try:
                return float(sensor['Height Offset'])
            except ValueError:
                pass
        return 0.0

    info = {
        'trailer': trailer_num,
        'man': man,
        'front': {
            'id': front['Mopeka ID'] if front else '---------------',
            'offset': get_offset(front)
        },
        'back': {
            'id': back['Mopeka ID'] if back else '---------------',
            'offset': get_offset(back)
        }
    }
    value = json.dumps(info, separators=(',', ':'))
    print(f'ReadValue trailer: {value}', flush=True)
    return value.encode('utf-8')


def trailer_write_handler(connection, value):
    """Write trailer number to select and configure sensors for that trailer."""
    trailer_str = value.decode('utf-8').strip()
    print(f'Trailer write: {trailer_str}', flush=True)
    try:
        trailer_num = int(trailer_str)
    except ValueError:
        print(f'Invalid trailer number: {trailer_str}', flush=True)
        return

    result = apply_trailer(trailer_num)
    if result is None:
        print(f'Trailer {trailer_num} not found in sensor CSV', flush=True)
    return


def config_cmd_write_handler(connection, value):
    """Handle config command writes. Supports chunked writes via CHUNK:X/Y:data pattern."""
    global config_cmd_chunks

    try:
        data_str = value.decode('utf-8').strip()

        if data_str.startswith('CHUNK:'):
            parts = data_str.split(':', 2)
            if len(parts) >= 3:
                chunk_info = parts[1]
                chunk_data = parts[2]
                chunk_num, total_chunks = map(int, chunk_info.split('/'))

                print(f'ConfigCmd chunk {chunk_num}/{total_chunks} ({len(chunk_data)} bytes)', flush=True)

                if 'total' not in config_cmd_chunks or config_cmd_chunks['total'] != total_chunks:
                    config_cmd_chunks = {
                        'chunks': {},
                        'total': total_chunks,
                        'timestamp': time.time()
                    }

                config_cmd_chunks['chunks'][chunk_num] = chunk_data
                config_cmd_chunks['timestamp'] = time.time()

                if len(config_cmd_chunks['chunks']) == total_chunks:
                    assembled = ''
                    for i in range(1, total_chunks + 1):
                        if i in config_cmd_chunks['chunks']:
                            assembled += config_cmd_chunks['chunks'][i]
                        else:
                            print(f'ConfigCmd: missing chunk {i}!', flush=True)
                            return
                    config_cmd_chunks = {}
                    process_config_command(assembled)
        else:
            process_config_command(data_str)

    except Exception as e:
        print(f'ConfigCmd error: {e}', flush=True)
    return


def config_data_read_handler(connection):
    """Read the response from the last config command."""
    value = config_response
    print(f'ReadValue config_data: {value[:80]}...', flush=True)
    return value.encode('utf-8')


def decode_mopeka(data):
    temp_c = (data[2] & 0x7F) - 40
    tank_raw = data[3] | ((data[4] & 0x3F) << 8)
    quality = (data[4] >> 6) & 0x03
    level_mm = tank_raw * (0.573045 - 0.002822 * temp_c - 0.00000535 * temp_c * temp_c)
    return {'level_mm': round(level_mm, 1), 'quality': quality}

def jbd_cmd(func):
    frame = [0xDD, 0xA5, func, 0x00]
    crc = sum([-b for b in frame[2:4]]) & 0xFFFF
    return bytes(frame + [crc >> 8, crc & 0xFF, 0x77])

async def poll_dashboard_status():
    """Periodically poll dashboard for status"""
    poll_count = 0
    while True:
        try:
            query_dashboard_status()
            # Poll history every 50 cycles (~10 seconds at 0.2s interval)
            poll_count += 1
            if poll_count >= 50:
                query_fill_history()
                poll_count = 0
        except Exception as e:
            print(f'Status poll error: {e}', flush=True)
        await asyncio.sleep(STATUS_POLL_INTERVAL)

async def read_sensors(sensor_adapter):
    global sensor_data
    try:
        from bleak import BleakScanner, BleakClient
    except ImportError:
        print("bleak not available for sensor reading")
        return

    print(f"Sensor reader started on {sensor_adapter}", flush=True)
    print(f"Scan interval: {SCAN_INTERVAL}s, BMS every {BMS_READ_INTERVAL} cycles", flush=True)
    print(f"Auto-recovery after {MAX_CONSECUTIVE_FAILURES} failures", flush=True)

    cycle_count = 0
    mopeka_failures = 0
    bms_failures = 0
    last_adapter_reset = 0

    while True:
        cycle_count += 1
        current_time = time.time()

        # Check if adapter needs reset
        if (mopeka_failures >= MAX_CONSECUTIVE_FAILURES or bms_failures >= MAX_CONSECUTIVE_FAILURES):
            if current_time - last_adapter_reset > ADAPTER_RESET_COOLDOWN:
                print(f'Too many failures, resetting adapter...', flush=True)
                new_adapter = find_adapter_by_mac(SENSOR_ADAPTER_MAC)
                if new_adapter and new_adapter != sensor_adapter:
                    print(f'Adapter changed from {sensor_adapter} to {new_adapter}', flush=True)
                    sensor_adapter = new_adapter
                reset_adapter(sensor_adapter)
                mopeka_failures = 0
                bms_failures = 0
                last_adapter_reset = current_time
                await asyncio.sleep(5)
                continue

        # Scan for Mopeka sensors
        mopeka_found = False
        try:
            devices = await BleakScanner.discover(
                timeout=SCAN_TIMEOUT,
                adapter=sensor_adapter,
                return_adv=True
            )
            for addr, (dev, adv) in devices.items():
                if adv.manufacturer_data and 89 in adv.manufacturer_data:
                    data = adv.manufacturer_data[89]
                    decoded = decode_mopeka(data)
                    decoded['last_update'] = current_time
                    if MOPEKA1_MAC_SUFFIX in addr.upper():
                        # Convert mm to gallons
                        conversion = mm_to_gallons(decoded["level_mm"], MOPEKA1_MAC_SUFFIX)
                        decoded.update(conversion)
                        sensor_data['mopeka1'] = decoded
                        print(f'Mopeka1: {decoded}', flush=True)
                        mopeka_found = True
                    elif MOPEKA2_MAC_SUFFIX in addr.upper():
                        # Convert mm to gallons
                        conversion = mm_to_gallons(decoded["level_mm"], MOPEKA2_MAC_SUFFIX)
                        decoded.update(conversion)
                        sensor_data['mopeka2'] = decoded
                        print(f'Mopeka2: {decoded}', flush=True)
                        mopeka_found = True

            if mopeka_found:
                mopeka_failures = 0
                # Send tank levels to dashboard
                m1_gal = sensor_data["mopeka1"].get("gallons", 0)
                m2_gal = sensor_data["mopeka2"].get("gallons", 0)
                m1_q = sensor_data["mopeka1"].get("quality", 0)
                m2_q = sensor_data["mopeka2"].get("quality", 0)
                send_dashboard_command(f"MOPEKA:{m1_gal:.0f}|{m2_gal:.0f}|{m1_q}|{m2_q}")
            else:
                mopeka_failures += 1
                if mopeka_failures > 1:
                    print(f'Mopeka not found ({mopeka_failures}/{MAX_CONSECUTIVE_FAILURES})', flush=True)
                    send_dashboard_command('MOPEKA_OFFLINE')

        except Exception as e:
            mopeka_failures += 1
            print(f'Mopeka error ({mopeka_failures}/{MAX_CONSECUTIVE_FAILURES}): {e}', flush=True)

        # Read BMS less frequently
        if cycle_count % BMS_READ_INTERVAL == 0:
            try:
                response = bytearray()
                def handler(s, d): response.extend(d)
                async with BleakClient(BMS_MAC, adapter=sensor_adapter, timeout=BMS_TIMEOUT) as client:
                    await client.start_notify('0000ff01-0000-1000-8000-00805f9b34fb', handler)
                    await client.write_gatt_char('0000ff02-0000-1000-8000-00805f9b34fb', jbd_cmd(0x03))
                    await asyncio.sleep(1.5)
                    if len(response) > 20:
                        d = response[4:]
                        sensor_data['bms'] = {
                            'voltage': round(int.from_bytes(d[0:2], 'big') * 0.01, 2),
                            'soc': d[19],
                            'last_update': current_time
                        }
                        print(f'BMS: {sensor_data["bms"]}', flush=True)
                        bms_failures = 0
                    else:
                        bms_failures += 1
            except Exception as e:
                bms_failures += 1
                print(f'BMS error ({bms_failures}/{MAX_CONSECUTIVE_FAILURES}): {e}', flush=True)

        await asyncio.sleep(SCAN_INTERVAL)

async def main():
    print('Starting Rotorsync GATT server (Bumble)...', flush=True)
    # Initialize Mopeka gallon converter
    mopeka_init()

    # Restore trailer config from last session
    restore_trailer_config()

    # Find adapters by MAC address
    gatt_adapter = find_adapter_by_mac(GATT_ADAPTER_MAC)
    sensor_adapter = find_adapter_by_mac(SENSOR_ADAPTER_MAC)

    if not gatt_adapter:
        print(f'ERROR: GATT adapter {GATT_ADAPTER_MAC} not found!', flush=True)
        return
    if not sensor_adapter:
        print(f'WARNING: Sensor adapter {SENSOR_ADAPTER_MAC} not found - sensors disabled', flush=True)

    gatt_adapter_index = int(gatt_adapter.replace('hci', ''))
    print(f'GATT adapter: {gatt_adapter} ({GATT_ADAPTER_MAC})', flush=True)
    if sensor_adapter:
        print(f'Sensor adapter: {sensor_adapter} ({SENSOR_ADAPTER_MAC})', flush=True)

    subprocess.run(['hciconfig', gatt_adapter, 'down'], capture_output=True)
    await asyncio.sleep(0.5)

    subprocess.run(['systemctl', 'start', 'bluetooth'], capture_output=True)
    await asyncio.sleep(1)
    if sensor_adapter:
        subprocess.run(['hciconfig', sensor_adapter, 'up'], capture_output=True)

    # Start background tasks
    if sensor_adapter:
        sensor_task = asyncio.create_task(read_sensors(sensor_adapter))
    status_task = asyncio.create_task(poll_dashboard_status())

    print(f'Opening HCI socket for {gatt_adapter}...', flush=True)
    hci_transport = await open_hci_socket_transport(gatt_adapter_index)

    host = Host(hci_transport.source, hci_transport.sink)
    device = Device(name='Rotorsync', host=host)

    # Create characteristics - READ for sensors
    bms_char = Characteristic(BMS_CHAR_UUID, Characteristic.Properties.READ,
        Characteristic.READABLE, CharacteristicValue(read=make_read_handler('bms')))
    mopeka1_char = Characteristic(MOPEKA1_CHAR_UUID, Characteristic.Properties.READ,
        Characteristic.READABLE, CharacteristicValue(read=make_read_handler('mopeka1')))
    mopeka2_char = Characteristic(MOPEKA2_CHAR_UUID, Characteristic.Properties.READ,
        Characteristic.READABLE, CharacteristicValue(read=make_read_handler('mopeka2')))

    # Create characteristics - WRITE for controls
    pump_char = Characteristic(PUMP_CHAR_UUID,
        Characteristic.Properties.WRITE | Characteristic.Properties.WRITE_WITHOUT_RESPONSE,
        Characteristic.WRITEABLE, CharacteristicValue(write=pump_write_handler))
    gallons_char = Characteristic(GALLONS_CHAR_UUID,
        Characteristic.Properties.WRITE | Characteristic.Properties.WRITE_WITHOUT_RESPONSE,
        Characteristic.WRITEABLE, CharacteristicValue(write=gallons_write_handler))

    # Create characteristics - READ for dashboard status
    requested_char = Characteristic(REQUESTED_CHAR_UUID, Characteristic.Properties.READ,
        Characteristic.READABLE, CharacteristicValue(read=make_dashboard_read_handler('requested')))
    actual_char = Characteristic(ACTUAL_CHAR_UUID, Characteristic.Properties.READ,
        Characteristic.READABLE, CharacteristicValue(read=make_dashboard_read_handler('actual')))

    history_char = Characteristic(HISTORY_CHAR_UUID, Characteristic.Properties.READ,
        Characteristic.READABLE, CharacteristicValue(read=make_history_read_handler()))

    # Create characteristic - WRITE for batch mix data from iPad
    batchmix_char = Characteristic(BATCHMIX_CHAR_UUID,
        Characteristic.Properties.WRITE | Characteristic.Properties.WRITE_WITHOUT_RESPONSE,
        Characteristic.WRITEABLE, CharacteristicValue(write=batchmix_write_handler))

    # Create characteristics - Trailer config
    trailer_char = Characteristic(TRAILER_CHAR_UUID,
        Characteristic.Properties.READ | Characteristic.Properties.WRITE | Characteristic.Properties.WRITE_WITHOUT_RESPONSE,
        Characteristic.READABLE | Characteristic.WRITEABLE,
        CharacteristicValue(read=trailer_read_handler, write=trailer_write_handler))

    config_cmd_char = Characteristic(CONFIG_CMD_CHAR_UUID,
        Characteristic.Properties.WRITE | Characteristic.Properties.WRITE_WITHOUT_RESPONSE,
        Characteristic.WRITEABLE, CharacteristicValue(write=config_cmd_write_handler))

    config_data_char = Characteristic(CONFIG_DATA_CHAR_UUID,
        Characteristic.Properties.READ,
        Characteristic.READABLE, CharacteristicValue(read=config_data_read_handler))

    service = Service(SERVICE_UUID, [bms_char, mopeka1_char, mopeka2_char, pump_char, gallons_char, requested_char, actual_char, history_char, batchmix_char, trailer_char, config_cmd_char, config_data_char])
    device.add_service(service)

    await device.power_on()
    print(f'Device address: {device.public_address}', flush=True)

    adv_data = AdvertisingData([
        (AdvertisingData.COMPLETE_LOCAL_NAME, b'Rotorsync'),
        (AdvertisingData.INCOMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS, bytes(SERVICE_UUID)),
    ])

    await device.start_advertising(advertising_data=bytes(adv_data), auto_restart=True)
    print('=== Rotorsync GATT Server Running ===', flush=True)
    print('Characteristics:', flush=True)
    print('  def1: BMS (read)        - {"voltage": x, "soc": y}', flush=True)
    print('  def2: Mopeka1 (read)    - {"level_mm": x, "quality": y, "gallons": z}', flush=True)
    print('  def3: Mopeka2 (read)    - {"level_mm": x, "quality": y, "gallons": z}', flush=True)
    print('  def4: Pump (write)      - "PS" to stop pump', flush=True)
    print('  def5: Gallons (write)   - "+1", "-1", "+10", "-10"', flush=True)
    print('  def6: Requested (read)  - requested gallons', flush=True)
    print('  def7: Actual (read)     - actual gallons', flush=True)
    print('  def8: History (read)    - last 5 fills', flush=True)
    print('  def9: BatchMix (write)  - JSON batch mix data from iPad', flush=True)
    print('  defa: Trailer (r/w)     - write trailer # to configure, read for current', flush=True)
    print('  defb: ConfigCmd (write) - JSON commands for sensor/calibration CRUD', flush=True)
    print('  defc: ConfigData (read) - response from last config command', flush=True)

    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
