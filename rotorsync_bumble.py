#!/usr/bin/env python3
"""
Rotorsync BLE GATT Server using Bumble (bypasses BlueZ)
With live sensor reading via BlueZ (separate adapter)
Sends commands to dashboard via localhost socket
Auto-recovery for sensor connections
Exposes requested/actual gallons from dashboard
"""
import asyncio
import json
import logging
import subprocess
import socket
import time

logging.basicConfig(level=logging.INFO)

from bumble.device import Device
from bumble.host import Host
from bumble.transport.hci_socket import open_hci_socket_transport
from bumble.gatt import Service, Characteristic, CharacteristicValue
from bumble.core import UUID, AdvertisingData

# Configuration - Use MAC addresses to find adapters dynamically
GATT_ADAPTER_MAC = '00:01:95:9C:C7:F5'  # CSR/Sena for GATT - phones connect here
SENSOR_ADAPTER_MAC = '08:BE:AC:44:C5:58'  # Realtek for sensors

# Socket connection to dashboard
DASHBOARD_HOST = '127.0.0.1'
DASHBOARD_PORT = 9999

BMS_MAC = 'A5:C2:37:2B:32:91'
MOPEKA1_MAC_SUFFIX = '0F:37:A5'
MOPEKA2_MAC_SUFFIX = 'F7:D0:22'

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
                        sensor_data['mopeka1'] = decoded
                        print(f'Mopeka1: {decoded}', flush=True)
                        mopeka_found = True
                    elif MOPEKA2_MAC_SUFFIX in addr.upper():
                        sensor_data['mopeka2'] = decoded
                        print(f'Mopeka2: {decoded}', flush=True)
                        mopeka_found = True
            
            if mopeka_found:
                mopeka_failures = 0
            else:
                mopeka_failures += 1
                if mopeka_failures > 1:
                    print(f'Mopeka not found ({mopeka_failures}/{MAX_CONSECUTIVE_FAILURES})', flush=True)
                    
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

    service = Service(SERVICE_UUID, [bms_char, mopeka1_char, mopeka2_char, pump_char, gallons_char, requested_char, actual_char, history_char, batchmix_char])
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
    print('  def2: Mopeka1 (read)    - {"level_mm": x, "quality": y}', flush=True)
    print('  def3: Mopeka2 (read)    - {"level_mm": x, "quality": y}', flush=True)
    print('  def4: Pump (write)      - "PS" to stop pump', flush=True)
    print('  def5: Gallons (write)   - "+1", "-1", "+10", "-10"', flush=True)
    print('  def6: Requested (read)  - requested gallons', flush=True)
    print('  def7: Actual (read)     - actual gallons', flush=True)
    print('  def8: History (read)    - last 5 fills', flush=True)
    print('  def9: BatchMix (write)  - JSON batch mix data from iPad', flush=True)

    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
