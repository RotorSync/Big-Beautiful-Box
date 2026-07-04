#!/usr/bin/env python3
"""
Rotorsync BLE GATT Server using Bumble (bypasses BlueZ)
With live sensor reading via BlueZ (separate adapter)
Sends commands to dashboard via localhost socket
Auto-recovery for sensor connections
Exposes requested/actual gallons from dashboard

"""
import asyncio
import base64
import contextlib
import csv
import ctypes
import ctypes.util
import hashlib
import hmac
import io
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import socket
import tarfile
import time
import zlib

logging.basicConfig(level=logging.INFO)

from bumble import hci
from bumble.device import Device, Peer
from bumble.host import Host
from bumble.transport.hci_socket import open_hci_socket_transport
from bumble.gatt import Service, Characteristic, CharacteristicValue
from bumble.core import UUID, AdvertisingData
# Mopeka gallon conversion
from src.mopeka_converter import mm_to_gallons, init as mopeka_init, reload as mopeka_reload
from src.bluetooth_adapter_selection import list_bluetooth_adapters, select_adapters
from src.batchmix_payload import batchmix_validation_error
from src import connection_registry
from src.fill_history import item_from_line as _shared_fill_item_from_line

# Configuration - Use MAC addresses to find adapters dynamically
GATT_ADAPTER_MAC = 'E8:EA:6A:BD:E7:4F'  # USB adapter used for RotorSync GATT server
SENSOR_ADAPTER_MAC = 'BC:FC:E7:2D:86:7B'  # USB adapter reserved for BMS/Mopeka sensor scanning

# Socket connection to dashboard
DASHBOARD_HOST = '127.0.0.1'
DASHBOARD_PORT = 9999

BMS_MAC = 'A5:C2:37:31:77:C0'
BMS_NAME = 'Unconfigured-BMS'
BMS_NOTIFY_UUID = UUID('0000ff01-0000-1000-8000-00805f9b34fb')
BMS_WRITE_UUID = UUID('0000ff02-0000-1000-8000-00805f9b34fb')
MOPEKA1_MAC_SUFFIX = ''  # Set by trailer selection (defa) or restored from mopeka_config.json
MOPEKA2_MAC_SUFFIX = ''
BMS_ENABLED = True
# Timing configuration
SCAN_TIMEOUT = 5
BMS_TIMEOUT = 8
SCAN_INTERVAL = 15
BMS_READ_INTERVAL = 20  # 20 * 15s scan interval = 5 minutes
STATUS_POLL_INTERVAL = 2.0  # Poll dashboard for status every 2 seconds
STATUS_HISTORY_POLL_INTERVAL = 10.0
STATUS_HISTORY_POLL_CYCLES = max(1, int(STATUS_HISTORY_POLL_INTERVAL / STATUS_POLL_INTERVAL))
LIVE_TELEMETRY_POLL_INTERVAL = 0.25
LIVE_TELEMETRY_MULTIPOINT_NOTIFY_INTERVAL = 0.75
LIVE_TELEMETRY_PILOT_PRIORITY_NOTIFY_INTERVAL = 0.75
LIVE_TELEMETRY_FAST_READ_WINDOW = 2.0
LIVE_TELEMETRY_ACTIVE_FLOW_THRESHOLD_GPM = 0.05
MOPEKA_HISTORY_LOG_PATH = '/home/pi/mopeka_history.csv'
FILL_HISTORY_LOG_PATH = '/home/pi/fill_history.log'
MOPEKA_HISTORY_BASELINE_INTERVAL = 300.0
MOPEKA_HISTORY_CHANGE_THRESHOLD_GAL = 2.0
HISTORY_RETENTION_SECONDS = 366 * 24 * 3600
HISTORY_MAX_FILE_BYTES = 8 * 1024 * 1024
HISTORY_PRUNE_INTERVAL = 3600.0
STARTUP_DASHBOARD_WAIT_SECONDS = 30
STARTUP_DASHBOARD_RETRY_INTERVAL = 0.5
GATT_ADVERTISING_RESUME_DELAY_SECONDS = 0.5
GATT_CONNECTED_ADVERTISING_VERIFY_INTERVAL_SECONDS = 2.0
GATT_CONNECTED_ADVERTISING_RETRY_INTERVAL_SECONDS = 1.0
GATT_INACTIVE_CONNECTION_PRUNE_SECONDS = 20.0
GATT_UNKNOWN_CLIENT_HELLO_GRACE_SECONDS = 8.0
# Time-sync from client hello: when the box boots with no network (no NTP since
# boot), a connecting RotorSync app can hand us the current wall-clock time so
# the box clock (and load timestamps) are right. An NTP-synced clock (chrony) is
# always more accurate than an iPad time delivered over BLE, so NTP wins: we only
# set the clock when the kernel reports it is NOT synchronized. One set per boot.
TIME_SYNC_LOG_PATH = '/home/pi/time_sync.log'
# Sanity window for an accepted hello time (epoch seconds, UTC):
# 2024-01-01 .. 2035-01-01. Anything outside is treated as garbage and ignored.
TIME_SYNC_MIN_EPOCH = 1704067200.0
TIME_SYNC_MAX_EPOCH = 2051222400.0
# If the clock IS already synced and a hello disagrees by more than this, log a
# discrepancy note (no clock change — NTP stays authoritative).
TIME_SYNC_DISCREPANCY_LOG_SECONDS = 120.0
# adjtimex status bit: clock is not synchronized.
_STA_UNSYNC = 0x0040
# adjtimex() return code TIME_ERROR (clock not synchronized / unusable).
_TIME_ERROR = 5

# Set True once we've applied a hello time this boot, so repeated hellos /
# reconnects don't keep nudging the clock.
pi_time_set_from_hello = False
GATT_CONNECTED_SELF_ADV_REFRESH_SECONDS = 30.0
GATT_CONNECTION_BOOKKEEPING_INTERVAL_SECONDS = 2.0
GATT_SENSOR_SETTLE_QUIET_SECONDS = 12.0
GATT_SENSOR_DEFER_LOG_INTERVAL_SECONDS = 10.0

# Recovery settings
MAX_CONSECUTIVE_FAILURES = 5
ADAPTER_RESET_COOLDOWN = 30
GATT_ADAPTER_CHECK_INTERVAL = 5
SENSOR_LOOP_HEARTBEAT_TIMEOUT = 120
SENSOR_ADAPTER_OPEN_TIMEOUT = 10
MOPEKA_SCAN_OPERATION_TIMEOUT = SCAN_TIMEOUT + 5
BMS_READ_OPERATION_TIMEOUT = (BMS_TIMEOUT * 2) + 12
HCI_SOCKET_OPEN_RETRY_ATTEMPTS = 12
HCI_SOCKET_OPEN_RETRY_DELAY_SECONDS = 2.0
CONFIG_RESPONSE_DIRECT_MAX_BYTES = 512
CONFIG_RESPONSE_CHUNK_SIZE = 360
CONFIG_RESPONSE_CHUNK_NOTIFY_DELAY_SECONDS = 0.005
CONFIG_RESPONSE_COMPRESSION = 'zlib'
GATT_ADVERTISING_RESUME_FAILURE_BACKOFF_SECONDS = 20

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
STATE_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdefd')      # Live iOS-friendly dashboard state
COMMAND_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdefe')    # JSON command channel for iOS app
CONFIG_NOTIFY_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdeff')  # Config response notify/read
MAINTENANCE_CONTROL_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdf00')
MAINTENANCE_STDIN_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdf01')
MAINTENANCE_STDOUT_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdf02')
LIVE_TELEMETRY_CHAR_UUID = UUID('12345678-1234-5678-1234-56789abcdf03')

# File paths for mopeka data
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MOPEKA_DIR = os.path.join(SCRIPT_DIR, 'mopeka')
SENSOR_CSV_PATH = os.path.join(MOPEKA_DIR, 'mopeka-sensor-details.csv')
CALIBRATION_CSV_PATH = os.path.join(MOPEKA_DIR, 'calibration-points-1070gal-tank.csv')
CALIBRATION_PROFILE_DIR = os.path.join(MOPEKA_DIR, 'calibrations')
MOPEKA_CONFIG_PATH = os.path.join(MOPEKA_DIR, 'mopeka_config.json')
WATCHDOG_LOG_PATH = '/home/pi/rotorsync_watchdog.log'
GATT_DEVICE_PATH_FILE = '/home/pi/rotorsync_gatt_device_path'
GATT_ADVERTISING_READY_FILE = '/home/pi/rotorsync_gatt_advertising_ready.json'
GATT_CLIENT_SEEN_FILE = '/home/pi/rotorsync_gatt_client_seen'
GATT_SELF_ADV_SEEN_FILE = '/home/pi/rotorsync_gatt_self_adv_seen.json'
GATT_CONNECTION_STATE_FILE = '/home/pi/rotorsync_gatt_connections.json'
GATT_SELF_ADV_ACTIVE_SCAN_STALE_SECONDS = 30
GATT_SELF_ADV_DEBUG_LOG_SECONDS = 300
DEFAULT_FLEET_BLE_NAME = 'TrailerSync-Unconfigured'
DEFAULT_CUSTOMER_BLE_NAME = 'TrailerSync-Customer'
MAINTENANCE_UPDATE_DIR = '/home/pi/rotorsync-maintenance-updates'
MAINTENANCE_REPO_DIR = '/home/pi/Big-Beautiful-Box'
MAINTENANCE_TMP_DIR = '/tmp/rotorsync-maintenance-update'
MAINTENANCE_UPDATE_ID_RE = re.compile(r'^[A-Za-z0-9._-]{1,96}$')
MAINTENANCE_STDOUT_TEXT_CHARS = 96
MAINTENANCE_STDOUT_NOTIFY_INTERVAL = 0.08
MAINTENANCE_STDOUT_NOTIFY_QUEUE_LIMIT = 200
MAINTENANCE_RUNTIME_PATHS = (
    'dashboard.py',
    'rotorsync_bumble.py',
    'rotorsync_watchdog.py',
    'start_iol_dashboard.sh',
    'VERSION',
    'config.py',
    'requirements.txt',
    'install.sh',
    'src',
    'deploy',
)
MAINTENANCE_SECRET_PATHS = (
    '/etc/rotorsync/maintenance.secret',
    '/home/pi/.rotorsync-maintenance-secret',
)
MAINTENANCE_DEVELOPMENT_SECRET = b'rotorsync-development-maintenance-secret'
MAINTENANCE_USER_SECRET_PATH = '/home/pi/.rotorsync-maintenance-secret'
MAINTENANCE_FRAME_SECRET_FIELDS = (
    'maintenance_secret_b64',
    'relay_secret_b64',
    'bbb_maintenance_secret_b64',
)
CURSOR_CONTROL_SETUP_SCRIPT = '/home/pi/Big-Beautiful-Box/deploy/setup-cursor-control.sh'

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
    'state': {},
    'state_json': '{}',
    'live_json': '{}',
    'last_update': 0
}

sensor_loop_heartbeat = 0.0
calibration_mtime_snapshot = None
last_calibration_reload_check = 0.0
connected_advertising_resume_succeeded = False
connected_advertising_next_retry_at = 0.0
connected_advertising_maintainer_task = None
gatt_connection_bookkeeping_task = None
active_gatt_connections = set()
active_gatt_connection_counts = {}
gatt_client_metadata_by_connection = {}
gatt_controller_changed_at = 0.0
last_sensor_defer_log_at = 0.0
gatt_self_advertisement_target = {'address': '', 'name': '', 'short_name': ''}
last_gatt_advertising_ready_at = 0.0
last_gatt_self_adv_seen_write = 0.0
last_gatt_self_adv_debug_log_at = 0.0
live_telemetry_notify_task = None
last_live_telemetry_client_read_at = 0.0
last_mopeka_history_log_at = 0.0
last_mopeka_history_snapshot = None
last_history_prune_at = 0.0


def _normalize_client_role(value):
    role = str(value or '').strip().lower().replace('-', '_').replace(' ', '_')
    if role in ('pilot', 'pilots'):
        return 'pilot'
    if role in ('ground', 'groundcrew', 'ground_crew', 'crew', 'groundcrew_member'):
        return 'ground_crew'
    if role in ('admin', 'administrator'):
        return 'admin'
    return 'unknown'


def _client_role_for_connection_key(connection_key):
    metadata = gatt_client_metadata_by_connection.get(connection_key) or {}
    return _normalize_client_role(metadata.get('role'))


def _gatt_connection_is_known_closed(connection):
    """Return true only when Bumble exposes a clearly closed connection state."""
    if connection is None:
        return False

    for attr in ('connected', 'is_connected'):
        value = getattr(connection, attr, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if isinstance(value, bool):
            return not value

    state = str(getattr(connection, 'state', '') or '').strip().lower()
    if state:
        if any(token in state for token in ('disconnect', 'closed', 'closing')):
            return True
        if any(token in state for token in ('connect', 'open', 'active')):
            return False

    return False


def _is_pilot_connected():
    for connection_key in active_gatt_connections:
        if _client_role_for_connection_key(connection_key) == 'pilot':
            return True
    return False


def _pilot_priority_active(state=None):
    state = state if isinstance(state, dict) else dashboard_status.get('state') or {}
    return (
        len(active_gatt_connections) > 1
        and _is_pilot_connected()
        and _state_live_telemetry_active(state)
    )


def mark_gatt_controller_changed(now=None):
    global gatt_controller_changed_at
    gatt_controller_changed_at = time.time() if now is None else float(now)


def gatt_sensor_defer_reason(kind, *, now=None):
    now = time.time() if now is None else float(now)
    if active_gatt_connections and gatt_controller_changed_at:
        age = now - gatt_controller_changed_at
        if age < GATT_SENSOR_SETTLE_QUIET_SECONDS:
            remaining = max(1, int(GATT_SENSOR_SETTLE_QUIET_SECONDS - age))
            return f'GATT controller settling ({remaining}s)'
    if kind == 'bms' and len(active_gatt_connections) > 1:
        return 'multipoint GATT active'
    return ''


def maybe_log_sensor_defer(kind, reason, *, now=None):
    global last_sensor_defer_log_at
    if not reason:
        return
    now = time.time() if now is None else float(now)
    if now - last_sensor_defer_log_at < GATT_SENSOR_DEFER_LOG_INTERVAL_SECONDS:
        return
    last_sensor_defer_log_at = now
    print(f'Deferred {kind} sensor scan: {reason}', flush=True)


def prune_inactive_gatt_connections(*, now=None, reason=''):
    """Drop stale bookkeeping entries when Bumble misses a disconnect event."""
    if len(active_gatt_connections) <= 1:
        return []

    now = time.time() if now is None else float(now)
    stale_peers = []
    for peer in list(active_gatt_connections):
        metadata = gatt_client_metadata_by_connection.get(peer) or {}
        if not _gatt_connection_is_known_closed(metadata.get('connection')):
            if metadata.get('connection') is not None:
                continue
        activity_at = float(
            metadata.get('last_seen')
            or metadata.get('connected_at')
            or 0
        )
        if not activity_at:
            continue
        if now - activity_at <= GATT_INACTIVE_CONNECTION_PRUNE_SECONDS:
            continue
        stale_peers.append((activity_at, str(peer), peer))

    stale_peers.sort()
    removable_count = max(0, len(active_gatt_connections) - 1)
    removed = []
    for _activity_at, _peer_text, peer in stale_peers[:removable_count]:
        active_gatt_connections.discard(peer)
        active_gatt_connection_counts.pop(peer, None)
        gatt_client_metadata_by_connection.pop(peer, None)
        removed.append(peer)

    if removed:
        print(
            'Pruned inactive GATT controller(s) from bookkeeping '
            f'({reason or "stale"}): {", ".join(sorted(removed))}',
            flush=True,
        )
        persist_gatt_connection_state(reason or 'prune_inactive')

    return removed


async def disconnect_unknown_gatt_client_if_no_hello(
    connection,
    peer,
    connected_at,
    *,
    grace_seconds=GATT_UNKNOWN_CLIENT_HELLO_GRACE_SECONDS,
):
    """Disconnect a client that opened GATT but never identified with client_hello."""
    await asyncio.sleep(grace_seconds)

    metadata = gatt_client_metadata_by_connection.get(peer) or {}
    if peer not in active_gatt_connections:
        return False
    if _normalize_client_role(metadata.get('role')) != 'unknown':
        return False
    if float(metadata.get('connected_at') or 0) != float(connected_at or 0):
        return False

    print(
        f'Disconnecting unclassified GATT client {peer}: no client_hello '
        f'within {grace_seconds:.0f}s',
        flush=True,
    )
    try:
        disconnect = getattr(connection, 'disconnect', None)
        if disconnect is not None:
            result = disconnect()
            if asyncio.iscoroutine(result):
                await result
    except Exception as e:
        print(
            f'Failed to disconnect unclassified GATT client {peer}: '
            f'{type(e).__name__}: {e}',
            flush=True,
        )

    if peer in active_gatt_connections:
        active_gatt_connections.discard(peer)
        active_gatt_connection_counts.pop(peer, None)
        gatt_client_metadata_by_connection.pop(peer, None)
        mark_gatt_controller_changed()
        persist_gatt_connection_state('unknown_client_no_hello')
    return True


class _Timex(ctypes.Structure):
    # struct timex (Linux). We only read .status plus the adjtimex() return code,
    # but the full layout must match so the kernel writes status to the right slot.
    _fields_ = [
        ("modes", ctypes.c_int), ("offset", ctypes.c_long), ("freq", ctypes.c_long),
        ("maxerror", ctypes.c_long), ("esterror", ctypes.c_long), ("status", ctypes.c_int),
        ("constant", ctypes.c_long), ("precision", ctypes.c_long), ("tolerance", ctypes.c_long),
        ("time_sec", ctypes.c_long), ("time_usec", ctypes.c_long), ("tick", ctypes.c_long),
        ("ppsfreq", ctypes.c_long), ("jitter", ctypes.c_long), ("shift", ctypes.c_int),
        ("stabil", ctypes.c_long), ("jitcnt", ctypes.c_long), ("calcnt", ctypes.c_long),
        ("errcnt", ctypes.c_long), ("stbcnt", ctypes.c_long), ("tai", ctypes.c_int),
        ("pad", ctypes.c_int * 11),
    ]


def _kernel_clock_is_synchronized():
    """True if the kernel considers the system clock NTP-synchronized.

    Daemon-agnostic (works with chrony/ntpd/timesyncd): reads adjtimex(). When no
    time source has disciplined the clock since boot, adjtimex returns TIME_ERROR
    and/or STA_UNSYNC is set. On any error reading the status we conservatively
    report False (treat clock as untrustworthy) so the hello path can help.
    """
    try:
        libc = ctypes.CDLL(ctypes.util.find_library('c') or 'libc.so.6', use_errno=True)
        t = _Timex()
        ret = libc.adjtimex(ctypes.byref(t))
        if ret < 0:
            return False
        if ret == _TIME_ERROR:
            return False
        return not bool(t.status & _STA_UNSYNC)
    except Exception as e:
        print(f'adjtimex check failed ({type(e).__name__}: {e}); '
              f'treating clock as unsynchronized', flush=True)
        return False


def _log_time_sync(line):
    stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    try:
        with open(TIME_SYNC_LOG_PATH, 'a') as f:
            f.write(f'{stamp} | {line}\n')
    except Exception as e:
        print(f'Failed to write time sync log: {type(e).__name__}: {e}', flush=True)
    print(f'[TIME SYNC] {line}', flush=True)


def _client_hello_identity(command, metadata):
    """Human-readable 'who gave us the time' label for the log."""
    name = str(command.get('name') or (metadata or {}).get('name') or '').strip()
    user_id = str(command.get('user_id') or command.get('uid')
                  or (metadata or {}).get('user_id') or '').strip()
    device = str(command.get('device') or (metadata or {}).get('device') or '').strip()
    role = _normalize_client_role(command.get('role') or command.get('r')
                                  or (metadata or {}).get('role'))
    who = name or 'Unknown user'
    extra = []
    if user_id:
        extra.append(f'id={user_id}')
    if device:
        extra.append(f'device={device}')
    extra.append(f'role={role}')
    return f'{who} ({", ".join(extra)})'


def _maybe_apply_hello_time(command, metadata):
    """Set the box clock from a client_hello time, but only when it makes sense.

    Rules (accuracy-first):
      * No/invalid/out-of-range time in the hello -> do nothing, no error.
      * Kernel clock already NTP-synchronized -> NTP is authoritative (sub-second,
        beats an iPad time delivered over BLE); do NOT touch the clock. If the
        hello disagrees by a large margin, log a discrepancy note for visibility.
      * Already set from a hello this boot -> do nothing.
      * Otherwise (clock unsynchronized since boot) -> set the clock and log it.
    """
    global pi_time_set_from_hello

    raw = command.get('time')
    if raw is None:
        raw = command.get('epoch')
    if raw is None:
        return  # App didn't send time; that's fine — ignore silently.

    try:
        epoch = float(raw)
    except (TypeError, ValueError):
        print(f'Ignoring client_hello time: not a number ({raw!r})', flush=True)
        return

    if not (TIME_SYNC_MIN_EPOCH <= epoch <= TIME_SYNC_MAX_EPOCH):
        print(f'Ignoring client_hello time {epoch}: outside sane range', flush=True)
        return

    now = time.time()
    delta = epoch - now  # how far the hello says we're off (provided - current)
    who = _client_hello_identity(command, metadata)

    if _kernel_clock_is_synchronized():
        # NTP wins. Only surface a note if the disagreement is large.
        if abs(delta) >= TIME_SYNC_DISCREPANCY_LOG_SECONDS:
            _log_time_sync(
                f'DISCREPANCY (clock kept, NTP authoritative): app time differs '
                f'by {delta:+.1f}s. App said '
                f'{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))} from {who}'
            )
        return

    if pi_time_set_from_hello:
        return  # one-shot per boot

    # Clock is not synchronized -> trust the app. Apply as close to receipt as
    # possible to keep it within ~a second.
    try:
        time.clock_settime(time.CLOCK_REALTIME, epoch)
    except PermissionError:
        _log_time_sync(
            f'FAILED to set clock (need root/CAP_SYS_TIME). Wanted '
            f'{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))} from {who}'
        )
        return
    except Exception as e:
        _log_time_sync(f'FAILED to set clock ({type(e).__name__}: {e}) from {who}')
        return

    pi_time_set_from_hello = True
    new_local = time.strftime('%A %Y-%m-%d %H:%M:%S %Z', time.localtime(epoch))
    _log_time_sync(
        f'Clock set to {new_local} (corrected {delta:+.1f}s, source=client_hello) '
        f'by {who}'
    )


def _record_client_hello(connection, command):
    connection_key = _connection_key(connection)
    now = time.time()
    role = _normalize_client_role(command.get('role') or command.get('r'))
    previous = gatt_client_metadata_by_connection.get(connection_key, {})
    gatt_client_metadata_by_connection[connection_key] = {
        **previous,
        'role': role,
        'user_id': str(command.get('user_id') or command.get('uid') or '')[:80],
        'name': str(command.get('name') or '')[:80],
        'device': str(command.get('device') or '')[:80],
        'last_seen': now,
        'connected_at': float(previous.get('connected_at') or now),
    }
    persist_gatt_connection_state('client_hello')
    connection_registry.record_event(
        'hello', 'ble', peer=connection_key, role=role,
        name=command.get('name'), user_id=command.get('user_id'),
        device=command.get('device'),
    )
    _record_client_loc(connection, command.get('loc'))
    # If this hello carries a wall-clock time and our box clock isn't
    # NTP-synchronized, use it to set the box clock (see _maybe_apply_hello_time).
    try:
        _maybe_apply_hello_time(command, gatt_client_metadata_by_connection.get(connection_key))
    except Exception as e:
        print(f'client_hello time handling error: {type(e).__name__}: {e}', flush=True)
    print(
        f'Client hello from {connection_key}: role={role}, '
        f'pilot_connected={_is_pilot_connected()}',
        flush=True,
    )
    query_dashboard_status()
    push_pilot_status_to_dashboard()


def _record_client_loc(connection, value):
    """Store a client's location update ({lat, lon[, acc]} — nested hello `loc`
    or a flat loc_update command); a pilot's location is forwarded to the
    dashboard (PILOT_LOC) so it can be stamped onto loads at fill time."""
    if not isinstance(value, dict):
        return
    try:
        lat = round(float(value.get('lat')), 6)
        lon = round(float(value.get('lon')), 6)
    except (TypeError, ValueError):
        return
    loc = {'lat': lat, 'lon': lon, 'ts': time.time()}
    try:
        if value.get('acc') is not None:
            loc['acc'] = round(float(value['acc']), 1)
    except (TypeError, ValueError):
        pass
    connection_key = _connection_key(connection)
    metadata = gatt_client_metadata_by_connection.get(connection_key)
    if metadata is None:
        return
    metadata['loc'] = loc
    if _normalize_client_role(metadata.get('role')) == 'pilot':
        parts = f"{lat},{lon}"
        if 'acc' in loc:
            parts += f",{loc['acc']}"
        send_dashboard_command(f'PILOT_LOC:{parts}')


_last_pushed_pilot_name = None


def _current_pilot_name():
    """Name of the connected client whose role is 'pilot' (most recent), or None."""
    best_name = None
    best_seen = -1.0
    for connection_key in active_gatt_connections:
        metadata = gatt_client_metadata_by_connection.get(connection_key) or {}
        if _normalize_client_role(metadata.get('role')) != 'pilot':
            continue
        seen = float(metadata.get('last_seen') or 0.0)
        if seen >= best_seen:
            best_seen = seen
            best_name = (metadata.get('name') or '').strip()
    return best_name or None


def _sanitize_pilot_name(name):
    return (name or '').replace('\n', ' ').replace('\r', ' ').replace('|', '/').strip()[:80]


def push_pilot_status_to_dashboard(force=False):
    """Tell the dashboard which role='pilot' client is connected.

    Emits PILOT_CONNECTED:<name> while a pilot is connected and
    PILOT_DISCONNECTED:<name> once the last pilot drops, so the dashboard can
    stamp the pilot onto recorded loads. Only emits on change.
    """
    global _last_pushed_pilot_name
    name = _current_pilot_name()
    if name == _last_pushed_pilot_name and not force:
        return
    previous = _last_pushed_pilot_name
    _last_pushed_pilot_name = name
    if name:
        send_dashboard_command(f'PILOT_CONNECTED:{_sanitize_pilot_name(name)}')
    elif previous:
        send_dashboard_command(f'PILOT_DISCONNECTED:{_sanitize_pilot_name(previous)}')


def _compact_fault_reason(value, limit=96):
    text = str(value or '').replace('\n', ' ').replace('\r', ' ').strip()
    if not text:
        return None
    return text[:limit]


def _float_if_finite(value, digits):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _flow_fault_summary_from_state(state):
    if not isinstance(state, dict):
        return False, None, None

    reason = _compact_fault_reason(state.get('flow_meter_fault_reason'))
    if state.get('negative_totalizer_fault'):
        return True, 'negative_totalizer', reason or 'Negative flow meter totalizer'
    if state.get('negative_flow_fault'):
        return True, 'negative_flow', reason or 'Negative flow meter'
    if state.get('positive_drift_fault'):
        return True, 'positive_drift', reason or 'Positive flow meter drift'
    if state.get('flow_fault_active'):
        return True, state.get('flow_fault_code') or 'flow_meter', reason
    if reason:
        return True, state.get('flow_fault_code') or 'flow_meter', reason
    return False, None, None


def _encode_ble_state_payload(state):
    """Encode the dashboard snapshot into a compact BLE/iOS-friendly JSON payload."""
    def put_if_present(target, key, value):
        if value is not None:
            target[key] = value

    def put_bool_if_non_default(target, key, value, default):
        if value is None:
            return
        bool_value = bool(value)
        if bool_value != default:
            target[key] = bool_value

    def compact_curve_value(value):
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        lowered = text.lower()
        if 'no pending' in lowered or lowered in ('none', '--'):
            return None
        match = re.search(r'[-+]?\d+(?:\.\d+)?', text)
        if match:
            return match.group(0)
        return text[:24]

    compact = {
        'ver': state.get('version'),
        'req': state.get('requested_gal'),
        'act': state.get('actual_gal'),
        'flow': state.get('flow_gpm'),
        'mode': state.get('mode'),
        'bc': max(1, len(active_gatt_connections)),
    }
    put_bool_if_non_default(compact, 'ov', state.get('override'), False)
    put_bool_if_non_default(compact, 'thumb', state.get('thumbs_visible'), False)
    put_bool_if_non_default(compact, 'pend', state.get('fill_pending'), False)
    put_bool_if_non_default(compact, 'confirm', state.get('can_confirm_fill'), False)
    put_bool_if_non_default(compact, 'green', state.get('colors_green'), False)
    put_bool_if_non_default(compact, 'latch', state.get('pump_stop_latched'), False)
    put_bool_if_non_default(compact, 'rs', state.get('relay_slowdown_alarm'), False)
    put_bool_if_non_default(compact, 'fm_ok', state.get('flow_meter_connected'), True)
    put_bool_if_non_default(compact, 'sb_ok', state.get('switch_box_connected'), True)
    put_bool_if_non_default(compact, 'pilot', _is_pilot_connected(), False)
    put_bool_if_non_default(compact, 'prio', _pilot_priority_active(state), False)
    put_if_present(compact, 'cc', compact_curve_value(state.get('current_curve')))
    put_if_present(compact, 'pc', compact_curve_value(state.get('pending_curve')))

    negative_totalizer_fault = bool(state.get('negative_totalizer_fault'))
    negative_flow_fault = bool(state.get('negative_flow_fault'))
    positive_drift_fault = bool(state.get('positive_drift_fault'))
    put_bool_if_non_default(compact, 'ntf', negative_totalizer_fault, False)
    put_bool_if_non_default(compact, 'nff', negative_flow_fault, False)
    put_bool_if_non_default(compact, 'pdf', positive_drift_fault, False)
    if negative_totalizer_fault:
        put_if_present(compact, 'ntg', _float_if_finite(state.get('negative_totalizer_gal'), 3))
    if negative_flow_fault:
        put_if_present(compact, 'nfg', _float_if_finite(state.get('negative_flow_gpm'), 2))
    if positive_drift_fault:
        put_if_present(compact, 'pdg', _float_if_finite(state.get('positive_drift_gal'), 3))
        put_if_present(compact, 'pfg', _float_if_finite(state.get('positive_drift_flow_gpm'), 2))

    flow_fault_active, flow_fault_code, flow_fault_reason = _flow_fault_summary_from_state(state)
    put_bool_if_non_default(compact, 'ff', flow_fault_active, False)
    put_if_present(compact, 'fc', flow_fault_code if flow_fault_active else None)
    put_if_present(compact, 'fmr', flow_fault_reason if flow_fault_active else None)
    return json.dumps(compact, separators=(',', ':'))


def _encode_live_telemetry_payload(
    requested,
    actual,
    flow,
    relay_slowdown_alarm=False,
    flow_fault_active=False,
    flow_fault_code=None,
    flow_fault_reason=None,
):
    payload = {
        'req': round(float(requested), 3),
        'act': round(float(actual), 3),
        'flow': round(float(flow), 2),
        'rs': bool(relay_slowdown_alarm),
        'ff': bool(flow_fault_active),
    }
    if flow_fault_active:
        if flow_fault_code:
            payload['fc'] = flow_fault_code
        reason = _compact_fault_reason(flow_fault_reason)
        if reason:
            payload['fmr'] = reason
    return json.dumps(payload, separators=(',', ':'))


def _state_flow_gpm(state):
    if not isinstance(state, dict):
        return 0.0
    try:
        return float(state.get('flow_gpm', state.get('flow', 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _state_live_telemetry_active(state):
    if not isinstance(state, dict):
        return False
    if bool(state.get('flow_fault_active')):
        return True
    if (
        state.get('negative_totalizer_fault')
        or state.get('negative_flow_fault')
        or state.get('positive_drift_fault')
    ):
        return True
    return abs(_state_flow_gpm(state)) > LIVE_TELEMETRY_ACTIVE_FLOW_THRESHOLD_GPM


def _state_notify_should_suppress_live_fields(controller_count, state):
    return (
        controller_count > 1
        and _state_live_telemetry_active(state)
    )


def _state_notify_compare_json(state_json, suppress_live_fields=False):
    if not suppress_live_fields:
        return state_json
    try:
        payload = json.loads(state_json)
    except Exception:
        return state_json
    if not isinstance(payload, dict):
        return state_json
    for key in ('act', 'flow', 'actual_gal', 'flow_gpm'):
        payload.pop(key, None)
    return json.dumps(payload, separators=(',', ':'), sort_keys=True)

# Config command state
config_response = '{"ok":false,"error":"No command issued"}'
config_response_pages = []  # Pre-computed pages for paginated responses
config_response_by_connection = {}
config_response_pages_by_connection = {}
config_response_pages_by_request = {}
config_response_read_index_by_connection = {}
config_notify_char = None
maintenance_stdout_char = None
ble_device = None
dashboard_ready = False
last_dashboard_error_log = 0.0
last_dashboard_error_message = None
last_gatt_client_seen_write = 0.0
control_command_queue = None
control_command_worker_task = None
control_command_seq = 0
maintenance_chunks = {}
maintenance_chunk_timeout = 30
maintenance_shell_process = None
maintenance_shell_reader_task = None
maintenance_active_session_id = None
maintenance_stdout_seq = 0
maintenance_last_stdout_payload = '{"type":"status","text":"Maintenance bridge idle","seq":0}'
maintenance_stdout_notify_queue = []
maintenance_stdout_notify_task = None
maintenance_updates = {}

# Chunked config command buffer
config_cmd_chunks = {}
config_cmd_chunk_timeout = 30

def _redact_dashboard_command(cmd):
    """Redact sensitive fields (like WiFi password) from command logs."""
    try:
        if cmd.startswith('WIFI_SET:'):
            payload = cmd.split(':', 1)[1]
            data = json.loads(payload)
            if isinstance(data, dict) and 'password' in data:
                data['password'] = '***'
            return f"WIFI_SET:{json.dumps(data, separators=(',', ':'))}"
    except Exception:
        pass
    return cmd


def _bounded_int(value, minimum, maximum, default=0):
    try:
        value = int(value)
    except Exception:
        return default
    return max(minimum, min(maximum, value))


def _bounded_float(value, minimum, maximum):
    try:
        value = float(value)
    except Exception:
        return None
    if not math.isfinite(value):
        return None
    return max(minimum, min(maximum, value))


def _enqueue_cursor_command(connection, source, action, **payload):
    payload['action'] = action
    _enqueue_control_actions(
        connection,
        source,
        f"MOUSE:{json.dumps(payload, separators=(',', ':'))}",
        refresh=False,
    )


def send_dashboard_command(cmd):
    """Send command to dashboard via socket"""
    global dashboard_ready, last_dashboard_error_log, last_dashboard_error_message
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect((DASHBOARD_HOST, DASHBOARD_PORT))
            s.send(f'{cmd}\n'.encode())
            response = s.recv(4096).decode().strip()
            dashboard_ready = True
            print(f"Dashboard command: {_redact_dashboard_command(cmd)} -> {response}", flush=True)
            return response
    except Exception as e:
        dashboard_ready = False
        message = f'Dashboard command error: {e}'
        now = time.time()
        if message != last_dashboard_error_message or now - last_dashboard_error_log >= 10:
            print(message, flush=True)
            last_dashboard_error_log = now
            last_dashboard_error_message = message
        return None


async def wait_for_dashboard_ready(timeout=STARTUP_DASHBOARD_WAIT_SECONDS):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if query_dashboard_status():
            return True
        await asyncio.sleep(STARTUP_DASHBOARD_RETRY_INTERVAL)
    return False

def query_fill_history():
    """Query dashboard for last 5 fills"""
    global dashboard_status
    response = send_dashboard_command('HISTORY')
    if response and response.startswith('HIST:'):
        dashboard_status['history'] = response[5:]
        return True
    return False

def query_dashboard_status():
    """Query dashboard for current state snapshot, with legacy STATUS fallback."""
    global dashboard_status
    response = send_dashboard_command('STATE_JSON')
    if response and response.startswith('STATE_JSON:'):
        try:
            payload = response.split(':', 1)[1]
            state = json.loads(payload)
            flow_fault_active, flow_fault_code, flow_fault_reason = _flow_fault_summary_from_state(state)
            dashboard_status['state'] = state
            dashboard_status['state_json'] = _encode_ble_state_payload(state)
            dashboard_status['live_json'] = _encode_live_telemetry_payload(
                state.get('requested_gal', 0.0),
                state.get('actual_gal', 0.0),
                state.get('flow_gpm', 0.0),
                state.get('relay_slowdown_alarm', False),
                flow_fault_active,
                flow_fault_code,
                flow_fault_reason,
            )
            dashboard_status['requested'] = float(state.get('requested_gal', 0.0))
            dashboard_status['actual'] = float(state.get('actual_gal', 0.0))
            dashboard_status['mode'] = str(state.get('mode', 'fill'))
            dashboard_status['last_update'] = time.time()
            return True
        except Exception as e:
            print(f'State JSON parse error: {e}', flush=True)

    response = send_dashboard_command('STATUS')
    if response and response.startswith('REQ:'):
        try:
            parts = response.split('|')
            for part in parts:
                if part.startswith('REQ:'):
                    dashboard_status['requested'] = float(part[4:])
                elif part.startswith('ACT:'):
                    dashboard_status['actual'] = float(part[4:])
                elif part.startswith('MODE:'):
                    dashboard_status['mode'] = part[5:]
            dashboard_status['state'] = {
                'requested_gal': dashboard_status['requested'],
                'actual_gal': dashboard_status['actual'],
                'mode': dashboard_status['mode'],
            }
            dashboard_status['state_json'] = _encode_ble_state_payload(
                dashboard_status['state']
            )
            dashboard_status['live_json'] = _encode_live_telemetry_payload(
                dashboard_status['requested'],
                dashboard_status['actual'],
                0.0,
                False,
            )
            dashboard_status['last_update'] = time.time()
            return True
        except Exception as e:
            print(f'Status parse error: {e}', flush=True)
    return False


def query_live_telemetry():
    """Query just actual gallons and flow from the dashboard for high-rate BLE."""
    response = send_dashboard_command('LIVE_TELEMETRY')
    if response and response.startswith('LIVE:'):
        try:
            payload = response.split(':', 1)[1]
            live = json.loads(payload)
            requested = float(live.get('req', dashboard_status.get('requested', 0.0)))
            actual = float(live.get('act', 0.0))
            flow = float(live.get('flow', 0.0))
            relay_slowdown_alarm = bool(
                live.get(
                    'rs',
                    (dashboard_status.get('state') or {}).get('relay_slowdown_alarm', False),
                )
            )
            cached_state = dashboard_status.get('state') or {}
            cached_fault_active, cached_fault_code, cached_fault_reason = _flow_fault_summary_from_state(cached_state)
            live_has_flow_fault = 'ff' in live
            flow_fault_active = bool(live.get('ff', cached_fault_active))
            flow_fault_code = live.get('fc', cached_fault_code)
            flow_fault_reason = live.get('fmr', cached_fault_reason)
            if live_has_flow_fault and not flow_fault_active:
                flow_fault_code = None
                flow_fault_reason = None
            dashboard_status['requested'] = requested
            dashboard_status['actual'] = actual
            state = cached_state
            if isinstance(state, dict):
                state['requested_gal'] = requested
                state['actual_gal'] = actual
                state['flow_gpm'] = flow
                state['relay_slowdown_alarm'] = relay_slowdown_alarm
                state['flow_fault_active'] = flow_fault_active
                state['flow_fault_code'] = flow_fault_code
                state['flow_meter_fault_reason'] = flow_fault_reason or ''
                if live_has_flow_fault:
                    state['negative_totalizer_fault'] = flow_fault_active and flow_fault_code == 'negative_totalizer'
                    state['negative_flow_fault'] = flow_fault_active and flow_fault_code == 'negative_flow'
                    state['positive_drift_fault'] = flow_fault_active and flow_fault_code == 'positive_drift'
                    if not flow_fault_active:
                        state['negative_totalizer_gal'] = 0.0
                        state['negative_flow_gpm'] = 0.0
                        state['positive_drift_gal'] = 0.0
                        state['positive_drift_flow_gpm'] = 0.0
                dashboard_status['state'] = state
            dashboard_status['live_json'] = _encode_live_telemetry_payload(
                requested,
                actual,
                flow,
                relay_slowdown_alarm,
                flow_fault_active,
                flow_fault_code,
                flow_fault_reason,
            )
            dashboard_status['last_update'] = time.time()
            return True
        except Exception as e:
            print(f'Live telemetry parse error: {e}', flush=True)

    state = dashboard_status.get('state') or {}
    requested = state.get('requested_gal', dashboard_status.get('requested', 0.0))
    actual = state.get('actual_gal', dashboard_status.get('actual', 0.0))
    flow = state.get('flow_gpm', 0.0)
    flow_fault_active, flow_fault_code, flow_fault_reason = _flow_fault_summary_from_state(state)
    dashboard_status['live_json'] = _encode_live_telemetry_payload(
        requested,
        actual,
        flow,
        state.get('relay_slowdown_alarm', False) if isinstance(state, dict) else False,
        flow_fault_active,
        flow_fault_code,
        flow_fault_reason,
    )
    return False


def _extract_jbd_frame(buffer, expected_function=None):
    """Extract the first complete JBD frame from a notification buffer."""
    start = buffer.find(b'\xDD')
    while start != -1:
        if len(buffer) - start < 7:
            return None

        function = buffer[start + 1]
        data_len = buffer[start + 3]
        frame_len = 4 + data_len + 3
        end = start + frame_len
        if len(buffer) < end:
            return None

        frame = bytes(buffer[start:end])
        del buffer[:end]
        if frame[-1] != 0x77:
            start = buffer.find(b'\xDD')
            continue
        if expected_function is not None and function != expected_function:
            start = buffer.find(b'\xDD')
            continue
        return frame

    return None

def find_adapter_by_mac(mac):
    """Find hci index by MAC address"""
    mac = mac.upper()
    result = subprocess.run(['hciconfig', '-a'], capture_output=True, text=True)
    current_hci = None
    for line in result.stdout.split('\n'):
        if line.startswith('hci'):
            current_hci = line.split(':')[0]
        if mac in line.upper():
            return current_hci

    return None


def _format_adapter(adapter):
    if not adapter:
        return 'none'
    usb_id = f"{adapter.get('vendor_id', '')}:{adapter.get('product_id', '')}"
    desc = ' '.join(
        part for part in (adapter.get('manufacturer'), adapter.get('product'))
        if part
    )
    return f"{adapter.get('hci')} {adapter.get('mac')} {usb_id} {desc}".strip()


def select_runtime_adapters():
    """Resolve GATT and sensor HCI adapters by known USB chip role."""
    global GATT_ADAPTER_MAC, SENSOR_ADAPTER_MAC

    adapters = list_bluetooth_adapters()
    if adapters:
        print('Detected Bluetooth adapters:', flush=True)
        for adapter in adapters:
            print(f'  {_format_adapter(adapter)}', flush=True)

    gatt_adapter, sensor_adapter, used_usb_role = select_adapters(
        adapters,
        GATT_ADAPTER_MAC,
        SENSOR_ADAPTER_MAC,
    )

    if used_usb_role:
        print('Bluetooth adapter roles selected by USB chip identity', flush=True)

    if gatt_adapter and gatt_adapter.get('mac'):
        GATT_ADAPTER_MAC = gatt_adapter['mac']
    if sensor_adapter and sensor_adapter.get('mac'):
        SENSOR_ADAPTER_MAC = sensor_adapter['mac']

    return (
        gatt_adapter['hci'] if gatt_adapter else None,
        sensor_adapter['hci'] if sensor_adapter else None,
    )


def get_adapter_device_path(adapter):
    """Return the resolved sysfs device path for an adapter."""
    try:
        return Path('/sys/class/bluetooth', adapter, 'device').resolve()
    except FileNotFoundError:
        return None


def find_adapter_by_device_path(expected_device_path):
    """Find the current hci index for a physical Bluetooth USB interface."""
    if not expected_device_path:
        return None

    bluetooth_root = Path('/sys/class/bluetooth')
    for adapter_path in sorted(bluetooth_root.glob('hci*')):
        try:
            if (adapter_path / 'device').resolve() == expected_device_path:
                return adapter_path.name
        except FileNotFoundError:
            continue
        except Exception:
            continue

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


def prepare_adapter_for_hci_user_channel(adapter):
    """Put the adapter in the state required by Linux HCI_CHANNEL_USER."""
    if not adapter:
        return
    try:
        result = subprocess.run(
            ['hciconfig', adapter, 'down'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip()
            print(
                f'WARNING: unable to bring {adapter} down before Bumble open'
                f'{": " + detail if detail else ""}',
                flush=True,
            )
    except Exception as e:
        print(f'WARNING: unable to prepare {adapter} for Bumble open: {e!r}', flush=True)


def adapter_exists(adapter):
    """Return True if the named HCI adapter still exists."""
    return Path('/sys/class/bluetooth', adapter).exists()

def log_watchdog_event(reason):
    """Log watchdog-triggered restarts in one dedicated file."""
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    count = 1

    try:
        with open(WATCHDOG_LOG_PATH, 'r', encoding='utf-8') as f:
            count += sum(1 for _ in f)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f'Watchdog log read error: {e}', flush=True)

    line = f'{timestamp} - event {count}: {reason}'
    print(line, flush=True)

    try:
        with open(WATCHDOG_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception as e:
        print(f'Watchdog log write error: {e}', flush=True)


def persist_gatt_device_path(device_path):
    """Persist the current GATT adapter sysfs path for the watchdog."""
    try:
        path_str = str(device_path) if device_path else ''
        with open(GATT_DEVICE_PATH_FILE, 'w', encoding='utf-8') as f:
            f.write(path_str + '\n')
    except Exception as e:
        print(f'Failed to persist GATT device path: {e}', flush=True)


def _atomic_write_text(path, text):
    tmp_path = f'{path}.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp_path, path)


def _normalize_ble_address(address):
    return str(address or '').upper().split('/')[0].strip()


def persist_gatt_advertising_ready(ble_name, address):
    """Tell the watchdog that Bumble reached the advertising-ready point."""
    global gatt_self_advertisement_target, last_gatt_advertising_ready_at

    now = time.time()
    normalized_address = _normalize_ble_address(address)
    short_name = _compute_short_ble_advertising_name(ble_name)
    gatt_self_advertisement_target = {
        'address': normalized_address,
        'name': str(ble_name or ''),
        'short_name': short_name,
    }
    last_gatt_advertising_ready_at = now

    try:
        payload = {
            'timestamp': now,
            'pid': os.getpid(),
            'name': ble_name,
            'short_name': short_name,
            'address': str(address),
        }
        _atomic_write_text(
            GATT_ADVERTISING_READY_FILE,
            json.dumps(payload, separators=(',', ':')) + '\n',
        )
    except Exception as e:
        print(f'Failed to persist GATT advertising ready state: {e}', flush=True)


def persist_gatt_connection_state(reason=''):
    """Record active GATT connection count for watchdog decisions."""
    try:
        client_details = []
        registry_clients = []
        for peer in sorted(str(peer) for peer in active_gatt_connections):
            metadata = gatt_client_metadata_by_connection.get(peer) or {}
            client_details.append({
                'id': peer,
                'role': _normalize_client_role(metadata.get('role')),
                'connected_at': float(metadata.get('connected_at') or 0),
                'last_seen': float(metadata.get('last_seen') or 0),
            })
            entry = {
                'transport': 'ble',
                'peer': peer,
                'role': _normalize_client_role(metadata.get('role')),
                'name': metadata.get('name') or None,
                'user_id': metadata.get('user_id') or None,
                'device': metadata.get('device') or None,
                'connected_at': float(metadata.get('connected_at') or 0),
                'hello_at': float(metadata.get('last_seen') or 0) or None,
            }
            registry_clients.append({k: v for k, v in entry.items() if v is not None})
        payload = {
            'timestamp': time.time(),
            'pid': os.getpid(),
            'count': len(active_gatt_connections),
            'clients': sorted(str(peer) for peer in active_gatt_connections),
            'client_details': client_details,
            'reason': str(reason or '')[:80],
        }
        _atomic_write_text(
            GATT_CONNECTION_STATE_FILE,
            json.dumps(payload, separators=(',', ':')) + '\n',
        )
        # Same live view, in the cross-server registry the app relays upstream
        # (see src/connection_registry.py; rotorlink writes the 'wifi' half).
        connection_registry.write_snapshot('ble', registry_clients)
    except Exception as e:
        print(f'Failed to persist GATT connection state: {e}', flush=True)


def _advertisement_values(advertisement, ad_type):
    try:
        data = getattr(advertisement, 'data', None)
        if data is None or not hasattr(data, 'get_all'):
            return []
        return data.get_all(ad_type) or []
    except Exception:
        return []


def _decode_advertisement_text(value):
    if isinstance(value, bytes):
        try:
            return value.decode('utf-8', errors='ignore').strip('\x00')
        except Exception:
            return ''
    return str(value or '').strip()


def _advertisement_local_names(advertisement):
    names = []
    for ad_type in (
        getattr(AdvertisingData, 'COMPLETE_LOCAL_NAME', None),
        getattr(AdvertisingData, 'SHORTENED_LOCAL_NAME', None),
    ):
        if ad_type is None:
            continue
        for value in _advertisement_values(advertisement, ad_type):
            name = _decode_advertisement_text(value)
            if name:
                names.append(name)
    return names


def _advertisement_has_service_uuid(advertisement, service_uuid):
    service_bytes = bytes(service_uuid)
    for ad_type in (
        getattr(AdvertisingData, 'INCOMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS', None),
        getattr(AdvertisingData, 'COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS', None),
    ):
        if ad_type is None:
            continue
        for value in _advertisement_values(advertisement, ad_type):
            try:
                value_bytes = bytes(value)
            except Exception:
                continue
            if service_bytes in value_bytes:
                return True
    return False


def _advertisement_manufacturer_ids(advertisement):
    ids = []
    ad_type = getattr(AdvertisingData, 'MANUFACTURER_SPECIFIC_DATA', None)
    if ad_type is None:
        return ids
    for item in _advertisement_values(advertisement, ad_type):
        try:
            company_id = item[0]
        except Exception:
            continue
        ids.append(company_id)
    return ids[:6]


def _gatt_self_advertisement_debug_summary(advertisement):
    target_address = _normalize_ble_address(gatt_self_advertisement_target.get('address'))
    target_name = str(gatt_self_advertisement_target.get('name') or '')
    target_short_name = str(gatt_self_advertisement_target.get('short_name') or '')
    address = _normalize_ble_address(getattr(advertisement, 'address', ''))
    names = _advertisement_local_names(advertisement)
    address_match = bool(target_address and address == target_address)
    target_names = {name for name in (target_name, target_short_name) if name}
    name_match = any(name in names for name in target_names)
    return {
        'addr': address,
        'names': names[:3],
        'rssi': getattr(advertisement, 'rssi', None),
        'mfg': _advertisement_manufacturer_ids(advertisement),
        'svc': _advertisement_has_service_uuid(advertisement, SERVICE_UUID),
        'addr_match': address_match,
        'name_match': name_match,
    }


def _gatt_self_advertisement_matches(summary):
    return bool(
        summary.get('addr_match')
        or summary.get('name_match')
        or summary.get('svc')
    )


def _should_use_active_self_adv_scan(now):
    if active_gatt_connections:
        return False
    return now - last_gatt_self_adv_seen_write > GATT_SELF_ADV_ACTIVE_SCAN_STALE_SECONDS


def maybe_mark_gatt_self_advertisement_seen(advertisement, now=None):
    """Record proof that the sensor adapter heard this box's GATT advert."""
    global last_gatt_self_adv_seen_write

    target_address = _normalize_ble_address(gatt_self_advertisement_target.get('address'))
    target_name = str(gatt_self_advertisement_target.get('name') or '')
    target_short_name = str(gatt_self_advertisement_target.get('short_name') or '')
    if not target_address and not target_name and not target_short_name:
        return False

    now = time.time() if now is None else now
    address = _normalize_ble_address(getattr(advertisement, 'address', ''))
    names = _advertisement_local_names(advertisement)
    address_match = bool(target_address and address == target_address)
    target_names = {name for name in (target_name, target_short_name) if name}
    name_match = any(name in names for name in target_names)
    service_match = _advertisement_has_service_uuid(advertisement, SERVICE_UUID)
    if not address_match and not name_match and not service_match:
        return False

    if now - last_gatt_self_adv_seen_write < 15:
        return True

    try:
        payload = {
            'timestamp': now,
            'pid': os.getpid(),
            'address': address,
            'target_address': target_address,
            'name': names[0] if names else '',
            'target_name': target_name,
            'target_short_name': target_short_name,
            'address_match': address_match,
            'name_match': name_match,
            'service_uuid_match': service_match,
            'rssi': getattr(advertisement, 'rssi', None),
        }
        _atomic_write_text(
            GATT_SELF_ADV_SEEN_FILE,
            json.dumps(payload, separators=(',', ':')) + '\n',
        )
        last_gatt_self_adv_seen_write = now
    except Exception as e:
        print(f'Failed to persist GATT self-advertisement heartbeat: {e}', flush=True)
    return True


def _is_gatt_advertising(device):
    for attr in ('is_advertising', 'advertising'):
        value = getattr(device, attr, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                return False
        if value is not None:
            return bool(value)
    return False


def connected_self_adv_refresh_due(now=None):
    # Do not tear down/recreate an extended advertising set while a controller is
    # connected. Some adapters/Bumble versions can lose the advertising handle
    # during that sequence and then reject subsequent enable/disable commands with
    # UNKNOWN_ADVERTISING_IDENTIFIER_ERROR. The watchdog remains responsible for
    # bounded recovery if discoverability is truly wedged.
    return False


async def refresh_gatt_advertising_for_discoverability(
    device,
    advertising_data,
    scan_response_data,
    ble_name,
    *,
    reason,
):
    global connected_advertising_next_retry_at, connected_advertising_resume_succeeded

    now = time.time()
    if connected_advertising_next_retry_at > now:
        return False
    if len(active_gatt_connections) != 1:
        return False

    try:
        if _is_gatt_advertising(device):
            await device.stop_advertising()
            await asyncio.sleep(0.05)
        if len(active_gatt_connections) != 1:
            return False
        await device.start_advertising(
            advertising_data=advertising_data,
            scan_response_data=scan_response_data,
            auto_restart=False,
        )
        persist_gatt_advertising_ready(ble_name, device.public_address)
        connected_advertising_resume_succeeded = True
        print(f'GATT advertising refreshed: {reason}', flush=True)
        return True
    except Exception as e:
        connected_advertising_next_retry_at = (
            time.time() + GATT_ADVERTISING_RESUME_FAILURE_BACKOFF_SECONDS
        )
        print(
            'GATT advertising refresh failed; keeping existing connection: '
            f'{type(e).__name__}: {e}',
            flush=True,
        )
        return False


async def restart_gatt_advertising_after_disconnect(
    device,
    advertising_data,
    scan_response_data,
    ble_name,
    *,
    delay=0.25,
):
    """Restart normal advertising after the last connected controller leaves."""
    global connected_advertising_resume_succeeded

    if delay > 0:
        await asyncio.sleep(delay)

    if active_gatt_connections:
        return False

    connected_advertising_resume_succeeded = False

    if _is_gatt_advertising(device):
        print('GATT advertising already active after all clients disconnected', flush=True)
        return True

    try:
        await device.start_advertising(
            advertising_data=advertising_data,
            scan_response_data=scan_response_data,
            auto_restart=False,
        )
        persist_gatt_advertising_ready(ble_name, device.public_address)
        print('GATT advertising restarted after all clients disconnected', flush=True)
        return True
    except Exception as e:
        print(
            'GATT advertising restart after disconnect failed: '
            f'{type(e).__name__}: {e}',
            flush=True,
        )
        return False


async def keep_gatt_connected_advertising_on(
    device,
    advertising_data,
    scan_response_data,
    ble_name,
    *,
    delay=GATT_ADVERTISING_RESUME_DELAY_SECONDS,
    reason='controller connected',
):
    """Try once to keep advertising active while a controller is connected."""
    global connected_advertising_resume_succeeded, connected_advertising_next_retry_at

    if delay > 0:
        await asyncio.sleep(delay)

    if len(active_gatt_connections) != 1:
        return False

    now = time.time()
    if connected_advertising_next_retry_at > now:
        remaining = int(connected_advertising_next_retry_at - now)
        print(
            f'GATT advertising resume retry suppressed for {remaining}s after '
            'recent controller error',
            flush=True,
        )
        return False

    if _is_gatt_advertising(device):
        print(f'GATT advertising already active: {reason}', flush=True)
        connected_advertising_resume_succeeded = True
        return True

    try:
        await device.start_advertising(
            advertising_data=advertising_data,
            scan_response_data=scan_response_data,
            auto_restart=False,
        )
        persist_gatt_advertising_ready(ble_name, device.public_address)
        print(
            f'GATT advertising kept on: {reason}; additional controllers should '
            'be able to discover this trailer anytime',
            flush=True,
        )
        connected_advertising_resume_succeeded = True
        return True
    except Exception as e:
        connected_advertising_next_retry_at = (
            time.time() + GATT_ADVERTISING_RESUME_FAILURE_BACKOFF_SECONDS
        )
        print(
            'GATT advertising resume after connection failed; controller may not '
            f'support advertising while connected: {type(e).__name__}: {e}',
            flush=True,
        )
        return False


async def maintain_single_client_gatt_advertising(
    device,
    advertising_data,
    scan_response_data,
    ble_name,
    *,
    delay=GATT_ADVERTISING_RESUME_DELAY_SECONDS,
    reason='controller connected',
):
    """Maintain the invariant that one connected controller remains discoverable."""
    global connected_advertising_resume_succeeded

    if delay > 0:
        await asyncio.sleep(delay)

    while len(active_gatt_connections) == 1:
        if connected_self_adv_refresh_due():
            await refresh_gatt_advertising_for_discoverability(
                device,
                advertising_data,
                scan_response_data,
                ble_name,
                reason='single controller connected but self-scan cannot hear advert',
            )
            await asyncio.sleep(GATT_CONNECTED_ADVERTISING_VERIFY_INTERVAL_SECONDS)
            reason = 'single controller still connected'
            continue

        if _is_gatt_advertising(device):
            connected_advertising_resume_succeeded = True
            await asyncio.sleep(GATT_CONNECTED_ADVERTISING_VERIFY_INTERVAL_SECONDS)
            reason = 'single controller still connected'
            continue

        await keep_gatt_connected_advertising_on(
            device,
            advertising_data,
            scan_response_data,
            ble_name,
            delay=0,
            reason=reason,
        )
        if len(active_gatt_connections) != 1:
            break
        interval = (
            GATT_CONNECTED_ADVERTISING_VERIFY_INTERVAL_SECONDS
            if _is_gatt_advertising(device)
            else GATT_CONNECTED_ADVERTISING_RETRY_INTERVAL_SECONDS
        )
        await asyncio.sleep(interval)
        reason = 'single controller still connected'

    return False


async def resume_gatt_advertising_after_connection(
    device,
    advertising_data,
    scan_response_data,
    ble_name,
    *,
    delay=GATT_ADVERTISING_RESUME_DELAY_SECONDS,
):
    """Try to keep the GATT server discoverable after one client connects."""
    return await keep_gatt_connected_advertising_on(
        device,
        advertising_data,
        scan_response_data,
        ble_name,
        delay=delay,
        reason='controller connected',
    )


def schedule_single_client_gatt_advertising_maintenance(
    device,
    advertising_data,
    scan_response_data,
    ble_name,
    *,
    reason,
):
    global connected_advertising_maintainer_task

    if (
        connected_advertising_maintainer_task is not None
        and not connected_advertising_maintainer_task.done()
    ):
        return connected_advertising_maintainer_task

    connected_advertising_maintainer_task = asyncio.create_task(
        maintain_single_client_gatt_advertising(
            device,
            advertising_data,
            scan_response_data,
            ble_name,
            reason=reason,
        )
    )
    connected_advertising_maintainer_task.add_done_callback(_handle_background_task_done)
    return connected_advertising_maintainer_task


def reconcile_gatt_connection_bookkeeping(
    device,
    advertising_data,
    scan_response_data,
    ble_name,
    *,
    now=None,
    reason='bookkeeping_maintainer',
):
    """Prune missed disconnects and restore single-client discoverability."""
    before_count = len(active_gatt_connections)
    removed = prune_inactive_gatt_connections(now=now, reason=reason)
    if before_count > 1 and removed and len(active_gatt_connections) == 1:
        schedule_single_client_gatt_advertising_maintenance(
            device,
            advertising_data,
            scan_response_data,
            ble_name,
            reason='stale controller pruned; anchor remains',
        )
    return removed


async def maintain_gatt_connection_bookkeeping(
    device,
    advertising_data,
    scan_response_data,
    ble_name,
    *,
    interval=GATT_CONNECTION_BOOKKEEPING_INTERVAL_SECONDS,
):
    """Continuously recover from missed disconnect events while one anchor remains."""
    while True:
        reconcile_gatt_connection_bookkeeping(
            device,
            advertising_data,
            scan_response_data,
            ble_name,
        )
        await asyncio.sleep(interval)


def schedule_gatt_connection_bookkeeping_maintenance(
    device,
    advertising_data,
    scan_response_data,
    ble_name,
):
    global gatt_connection_bookkeeping_task

    if (
        gatt_connection_bookkeeping_task is not None
        and not gatt_connection_bookkeeping_task.done()
    ):
        return gatt_connection_bookkeeping_task

    gatt_connection_bookkeeping_task = asyncio.create_task(
        maintain_gatt_connection_bookkeeping(
            device,
            advertising_data,
            scan_response_data,
            ble_name,
        )
    )
    gatt_connection_bookkeeping_task.add_done_callback(_handle_background_task_done)
    return gatt_connection_bookkeeping_task


def install_gatt_advertising_resume_hook(
    device,
    advertising_data,
    scan_response_data,
    ble_name,
):
    if os.environ.get('ROTORSYNC_DISABLE_CONNECTED_ADVERTISING') == '1':
        print(
            'GATT advertising resume-on-connect hook disabled; '
            'controller is single-client stable mode',
            flush=True,
        )
        return False

    if not hasattr(device, 'on'):
        print('GATT advertising resume hook unavailable: Bumble device has no event API', flush=True)
        return False

    def on_disconnection(peer):
        mark_gatt_controller_changed()
        current_count = active_gatt_connection_counts.get(peer, 0)
        departed_metadata = {}
        if current_count > 1:
            active_gatt_connection_counts[peer] = current_count - 1
        else:
            active_gatt_connection_counts.pop(peer, None)
            active_gatt_connections.discard(peer)
            departed_metadata = gatt_client_metadata_by_connection.pop(peer, None) or {}

        print(
            f'GATT client disconnected from {peer}; '
            f'{len(active_gatt_connections)} controller(s) remain',
            flush=True,
        )
        persist_gatt_connection_state('disconnect')
        connection_registry.record_event(
            'disconnect', 'ble', peer=peer,
            role=departed_metadata.get('role'),
            name=departed_metadata.get('name'),
            user_id=departed_metadata.get('user_id'),
            device=departed_metadata.get('device'),
        )
        push_pilot_status_to_dashboard()
        if active_gatt_connections:
            print(
                'GATT advertising left untouched; anchor controller remains',
                flush=True,
            )
            if len(active_gatt_connections) == 1:
                schedule_single_client_gatt_advertising_maintenance(
                    device,
                    advertising_data,
                    scan_response_data,
                    ble_name,
                    reason='controller disconnected; anchor remains',
                )
        else:
            task = asyncio.create_task(
                restart_gatt_advertising_after_disconnect(
                    device,
                    advertising_data,
                    scan_response_data,
                    ble_name,
                )
            )
            task.add_done_callback(_handle_background_task_done)

    def on_connection(connection):
        peer = _connection_key(connection)
        now = time.time()
        prune_inactive_gatt_connections(now=now, reason='new_connection')
        mark_gatt_controller_changed(now)
        previous_count = active_gatt_connection_counts.get(peer, 0)
        active_gatt_connection_counts[peer] = 1
        active_gatt_connections.add(peer)
        metadata = gatt_client_metadata_by_connection.setdefault(
            peer,
            {
                'role': 'unknown',
                'connected_at': now,
                'last_seen': now,
            },
        )
        metadata['connected_at'] = now
        metadata['last_seen'] = now
        metadata['connection'] = connection
        print(
            f'GATT client connected from {peer}; trying to keep advertising for '
            'additional controllers',
            flush=True,
        )
        persist_gatt_connection_state('connect')
        connection_registry.record_event('connect', 'ble', peer=peer)
        hello_deadline_task = asyncio.create_task(
            disconnect_unknown_gatt_client_if_no_hello(connection, peer, now)
        )
        hello_deadline_task.add_done_callback(_handle_background_task_done)
        if hasattr(connection, 'on'):
            connection.on('disconnection', lambda *args, peer=peer: on_disconnection(peer))
        else:
            print(
                'GATT connection object has no event API; disconnect advertising '
                'restart will rely on watchdog',
                flush=True,
            )
        if previous_count > 0:
            print(
                f'GATT duplicate connection event from {peer}; '
                'advertising state left untouched',
                flush=True,
            )
            return
        if len(active_gatt_connections) > 1:
            global connected_advertising_maintainer_task
            if (
                connected_advertising_maintainer_task is not None
                and not connected_advertising_maintainer_task.done()
            ):
                connected_advertising_maintainer_task.cancel()
                connected_advertising_maintainer_task = None
            print(
                'GATT multipoint connected; advertising state left untouched',
                flush=True,
            )
            return
        schedule_single_client_gatt_advertising_maintenance(
            device,
            advertising_data,
            scan_response_data,
            ble_name,
            reason='controller connected',
        )

    device.on('connection', on_connection)
    schedule_gatt_connection_bookkeeping_maintenance(
        device,
        advertising_data,
        scan_response_data,
        ble_name,
    )
    print('GATT advertising resume-on-connect hook installed', flush=True)
    return True


def _handle_background_task_done(task):
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        print(f'Background task error: {type(exc).__name__}: {exc}', flush=True)


def mark_gatt_client_seen(connection=None):
    """Record recent GATT client activity without touching the Bluetooth adapter."""
    global last_gatt_client_seen_write

    now = time.time()
    peer = _connection_key(connection) if connection is not None else None
    if peer:
        was_tracked = peer in active_gatt_connections
        if not was_tracked:
            active_gatt_connections.add(peer)
            active_gatt_connection_counts[peer] = max(
                1,
                active_gatt_connection_counts.get(peer, 0),
            )
            mark_gatt_controller_changed(now)
        metadata = gatt_client_metadata_by_connection.setdefault(
            peer,
            {
                'role': 'unknown',
                'connected_at': now,
                'last_seen': now,
            },
        )
        metadata['last_seen'] = now
        metadata['connection'] = connection
        if not was_tracked:
            print(
                f'Recovered active GATT controller from client activity: {peer}',
                flush=True,
            )
        prune_inactive_gatt_connections(now=now, reason='client_seen')

    if now - last_gatt_client_seen_write < 5:
        return

    try:
        _atomic_write_text(GATT_CLIENT_SEEN_FILE, f'{now:.3f}\n')
        last_gatt_client_seen_write = now
        if peer:
            persist_gatt_connection_state('client_seen')
    except Exception as e:
        print(f'Failed to persist GATT client heartbeat: {e}', flush=True)


async def monitor_gatt_adapter(expected_adapter, expected_device_path):
    """Exit so systemd can restart if the GATT adapter is re-enumerated."""
    while True:
        await asyncio.sleep(GATT_ADAPTER_CHECK_INTERVAL)
        current_adapter = find_adapter_by_device_path(expected_device_path)

        if not current_adapter:
            log_watchdog_event(
                f'GATT adapter path {expected_device_path} disappeared; exiting for restart'
            )
            os._exit(1)

        if current_adapter != expected_adapter:
            log_watchdog_event(
                f'GATT adapter moved from {expected_adapter} to {current_adapter}; exiting for restart'
            )
            os._exit(1)

        if not adapter_exists(expected_adapter):
            log_watchdog_event(
                f'GATT adapter {expected_adapter} no longer exists; exiting for restart'
            )
            os._exit(1)

def make_read_handler(data_key):
    def read_value(connection):
        mark_gatt_client_seen(connection)
        data = sensor_data[data_key].copy()
        data.pop('last_update', None)
        value = json.dumps(data)
        print(f'ReadValue {data_key}: {value}', flush=True)
        return bytes(value, 'utf-8')
    return read_value

def make_history_read_handler():
    def read_value(connection):
        mark_gatt_client_seen(connection)
        value = dashboard_status['history']
        print(f'ReadValue history: {value[:50]}...', flush=True)
        return bytes(value, 'utf-8')
    return read_value

def make_dashboard_read_handler(field):
    def read_value(connection):
        mark_gatt_client_seen(connection)
        value = str(dashboard_status[field])
        print(f'ReadValue {field}: {value}', flush=True)
        return bytes(value, 'utf-8')
    return read_value


def make_state_read_handler():
    def read_value(connection):
        mark_gatt_client_seen(connection)
        value = dashboard_status.get('state_json', '{}')
        print(f'ReadValue state: {value[:120]}', flush=True)
        return bytes(value, 'utf-8')
    return read_value


def make_live_telemetry_read_handler():
    def read_value(connection):
        global last_live_telemetry_client_read_at
        mark_gatt_client_seen(connection)
        last_live_telemetry_client_read_at = time.time()
        query_live_telemetry()
        value = dashboard_status.get('live_json', '{}')
        print(f'ReadValue live: {value[:80]}', flush=True)
        return bytes(value, 'utf-8')
    return read_value


def make_config_notify_read_handler():
    def read_value(connection):
        mark_gatt_client_seen(connection)
        value = _next_config_response_read_value(connection)
        print(f'ReadValue config_notify: {value[:120]}', flush=True)
        return bytes(value, 'utf-8')
    return read_value


def _connection_key(connection):
    for attr in ('peer_address', 'address', 'handle'):
        value = getattr(connection, attr, None)
        if value is not None:
            return str(value)
    return 'default'


def _next_control_command_seq():
    global control_command_seq
    control_command_seq += 1
    return control_command_seq


def _run_control_command(item):
    peer = item['peer']
    seq = item['seq']
    source = item['source']
    actions = item['actions']
    refresh = item.get('refresh', True)
    print(
        f'Control command #{seq} from {peer} via {source}: '
        f'{",".join(_redact_dashboard_command(action) for action in actions)}',
        flush=True,
    )
    for action in actions:
        send_dashboard_command(action)
    if refresh:
        query_dashboard_status()


async def control_command_worker():
    while True:
        item = await control_command_queue.get()
        try:
            _run_control_command(item)
        except Exception as e:
            print(
                f'Control command #{item.get("seq", "?")} error: '
                f'{type(e).__name__}: {e}',
                flush=True,
            )
        finally:
            control_command_queue.task_done()


def start_control_command_worker():
    global control_command_queue, control_command_worker_task
    if control_command_queue is None:
        control_command_queue = asyncio.Queue()
    if control_command_worker_task is None or control_command_worker_task.done():
        control_command_worker_task = asyncio.create_task(control_command_worker())
    return control_command_worker_task


def _enqueue_control_actions(connection, source, actions, *, refresh=True):
    mark_gatt_client_seen(connection)
    if isinstance(actions, str):
        actions = [actions]
    actions = [action for action in actions if action]
    if not actions:
        return

    item = {
        'seq': _next_control_command_seq(),
        'peer': _connection_key(connection),
        'source': source,
        'actions': actions,
        'refresh': refresh,
    }

    if control_command_queue is not None:
        control_command_queue.put_nowait(item)
        print(
            f'Queued control command #{item["seq"]} from {item["peer"]} '
            f'via {source}',
            flush=True,
        )
        return

    print(
        f'Control command queue unavailable; running #{item["seq"]} inline',
        flush=True,
    )
    _run_control_command(item)


def _maintenance_session_id(default='unknown'):
    return maintenance_active_session_id or default


def _notify_maintenance_stdout(payload=None):
    global maintenance_stdout_notify_task
    if not ble_device or not maintenance_stdout_char:
        return
    payload_text = payload if payload is not None else maintenance_last_stdout_payload
    maintenance_stdout_notify_queue.append(payload_text)
    overflow_count = len(maintenance_stdout_notify_queue) - MAINTENANCE_STDOUT_NOTIFY_QUEUE_LIMIT
    if overflow_count > 0:
        del maintenance_stdout_notify_queue[:overflow_count]

    async def _drain_notifications():
        global maintenance_stdout_notify_task
        try:
            while maintenance_stdout_notify_queue:
                queued_payload = maintenance_stdout_notify_queue.pop(0)
                try:
                    await ble_device.notify_subscribers(
                        maintenance_stdout_char,
                        queued_payload.encode('utf-8'),
                    )
                except Exception as e:
                    print(f'Maintenance stdout notify error: {e}', flush=True)
                if maintenance_stdout_notify_queue:
                    await asyncio.sleep(MAINTENANCE_STDOUT_NOTIFY_INTERVAL)
        finally:
            maintenance_stdout_notify_task = None

    try:
        if maintenance_stdout_notify_task is None or maintenance_stdout_notify_task.done():
            maintenance_stdout_notify_task = asyncio.get_running_loop().create_task(
                _drain_notifications()
            )
    except RuntimeError:
        pass


def _set_maintenance_stdout_obj(obj):
    global maintenance_stdout_seq, maintenance_last_stdout_payload
    maintenance_stdout_seq += 1
    payload = {
        'type': obj.get('type', 'output'),
        'seq': maintenance_stdout_seq,
        'session_id': obj.get('session_id') or _maintenance_session_id(),
    }
    for key in (
        'text',
        'data',
        'reason',
        'update_id',
        'sha256',
        'size',
        'status',
        'ack_type',
        'frame_type',
        'offset',
        'received',
        'expected_size',
        'commandId',
        'command_id',
    ):
        if key in obj and obj[key] is not None:
            payload[key] = obj[key]
    maintenance_last_stdout_payload = json.dumps(payload, separators=(',', ':'))
    print(f'Maintenance stdout: {maintenance_last_stdout_payload[:220]}', flush=True)
    _notify_maintenance_stdout(maintenance_last_stdout_payload)


def _emit_maintenance_text(text, *, event_type='output', session_id=None):
    text = str(text)
    if not text:
        return
    for start in range(0, len(text), MAINTENANCE_STDOUT_TEXT_CHARS):
        _set_maintenance_stdout_obj({
            'type': event_type,
            'session_id': session_id or _maintenance_session_id(),
            'text': text[start:start + MAINTENANCE_STDOUT_TEXT_CHARS],
        })


def make_maintenance_stdout_read_handler():
    def read_value(connection):
        print(f'ReadValue maintenance stdout: {maintenance_last_stdout_payload[:120]}', flush=True)
        return maintenance_last_stdout_payload.encode('utf-8')
    return read_value


def _cleanup_maintenance_chunks():
    now = time.time()
    stale_keys = [
        key for key, buffer in maintenance_chunks.items()
        if now - buffer.get('timestamp', 0) > maintenance_chunk_timeout
    ]
    for key in stale_keys:
        maintenance_chunks.pop(key, None)


def _decode_maintenance_write(connection, value, channel):
    """Decode iOS maintenance MCHUNK frames into the original payload bytes."""
    _cleanup_maintenance_chunks()
    try:
        data_str = value.decode('utf-8').strip()
    except UnicodeDecodeError:
        return value

    if not data_str.startswith('MCHUNK:'):
        return value

    parts = data_str.split(':', 3)
    if len(parts) != 4:
        print(f'Maintenance {channel}: invalid chunk header', flush=True)
        return None

    message_id, chunk_info, chunk_data = parts[1], parts[2], parts[3]
    try:
        chunk_num_str, total_chunks_str = chunk_info.split('/', 1)
        chunk_num = int(chunk_num_str)
        total_chunks = int(total_chunks_str)
    except Exception:
        print(f'Maintenance {channel}: invalid chunk count {chunk_info!r}', flush=True)
        return None

    if chunk_num < 1 or total_chunks < 1 or chunk_num > total_chunks:
        print(f'Maintenance {channel}: out-of-range chunk {chunk_info!r}', flush=True)
        return None

    key = f'{_connection_key(connection)}:{channel}:{message_id}'
    buffer = maintenance_chunks.get(key)
    if not buffer or buffer.get('total') != total_chunks:
        buffer = {'chunks': {}, 'total': total_chunks, 'timestamp': time.time()}
        maintenance_chunks[key] = buffer

    buffer['chunks'][chunk_num] = chunk_data
    buffer['timestamp'] = time.time()
    print(
        f'Maintenance {channel} chunk {chunk_num}/{total_chunks} '
        f'({len(chunk_data)} chars)',
        flush=True,
    )

    if len(buffer['chunks']) < total_chunks:
        return None

    try:
        encoded = ''.join(buffer['chunks'][i] for i in range(1, total_chunks + 1))
        decoded = base64.b64decode(encoded, validate=True)
    except Exception as e:
        print(f'Maintenance {channel}: chunk decode failed: {e}', flush=True)
        maintenance_chunks.pop(key, None)
        return None

    maintenance_chunks.pop(key, None)
    print(f'Maintenance {channel} complete: {len(decoded)} bytes', flush=True)
    return decoded


def _parse_maintenance_payload(payload_bytes):
    try:
        text = payload_bytes.decode('utf-8')
    except UnicodeDecodeError:
        return None, payload_bytes
    try:
        obj = json.loads(text)
    except Exception:
        return None, payload_bytes
    return obj if isinstance(obj, dict) else None, payload_bytes


def _provisioned_maintenance_secret_source():
    for env_name in ('BBB_MAINTENANCE_SECRET', 'MAINTENANCE_RELAY_SECRET'):
        value = os.environ.get(env_name, '').strip()
        if value:
            return f'env:{env_name}', value.encode('utf-8')

    for path in MAINTENANCE_SECRET_PATHS:
        try:
            with open(path, 'rb') as f:
                value = f.read().strip()
            if value:
                return f'file:{path}', value
        except OSError:
            continue

    return None, None


def _maintenance_secret_source():
    source, secret = _provisioned_maintenance_secret_source()
    if secret:
        return source, secret
    return 'development-default', MAINTENANCE_DEVELOPMENT_SECRET


def _maintenance_secret():
    return _maintenance_secret_source()[1]


def _log_maintenance_secret_status():
    source, _secret = _maintenance_secret_source()
    if source == 'development-default':
        print(
            'WARNING: maintenance relay secret missing; admin maintenance frames '
            'signed with the fleet secret will be rejected',
            flush=True,
        )
    else:
        print(f'Maintenance relay secret source: {source}', flush=True)


def ensure_cursor_control_setup():
    if os.geteuid() != 0:
        print('Cursor control setup skipped: rotorsync_bumble.py is not running as root', flush=True)
        return

    script = Path(CURSOR_CONTROL_SETUP_SCRIPT)
    if not script.exists():
        print(f'Cursor control setup skipped: {script} missing', flush=True)
        return

    try:
        result = subprocess.run(
            [str(script), '--restart-dashboard'],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as e:
        print(f'Cursor control setup failed: {e}', flush=True)
        return

    output = ((result.stdout or '') + (result.stderr or '')).strip()
    if result.returncode == 0:
        print(f'Cursor control setup ok: {output or "no output"}', flush=True)
    else:
        print(f'Cursor control setup failed ({result.returncode}): {output[:240]}', flush=True)


def _frame_maintenance_secret(frame):
    for key in MAINTENANCE_FRAME_SECRET_FIELDS:
        value = frame.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            secret = base64.b64decode(value.strip(), validate=True)
        except Exception as e:
            raise ValueError('invalid maintenance secret bootstrap') from e
        secret = secret.strip()
        if len(secret) < 32 or len(secret) > 4096:
            raise ValueError('invalid maintenance secret bootstrap length')
        return secret
    return None


def _install_maintenance_secret(secret):
    source, _existing = _provisioned_maintenance_secret_source()
    if source:
        return False

    path = Path(MAINTENANCE_USER_SECRET_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + '.tmp')
    with open(tmp_path, 'wb') as f:
        f.write(secret.strip() + b'\n')
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)
    print(f'Maintenance relay secret provisioned at {path}', flush=True)
    return True


def _canonical_maintenance_payload(frame):
    unsigned = {key: value for key, value in frame.items() if key != 'sig'}
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    ).encode('utf-8')


def _maintenance_frame_signature(frame):
    digest = hmac.new(
        _maintenance_secret(),
        _canonical_maintenance_payload(frame),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')


def _maintenance_frame_signature_with_secret(frame, secret):
    digest = hmac.new(
        secret,
        _canonical_maintenance_payload(frame),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')


def _verify_maintenance_frame(frame, now=None):
    if not isinstance(frame, dict):
        raise ValueError('maintenance frame must be a JSON object')

    signature = frame.get('sig')
    if not isinstance(signature, str) or not signature:
        raise ValueError('missing frame signature')

    source, secret = _maintenance_secret_source()
    expected = _maintenance_frame_signature_with_secret(frame, secret)
    if hmac.compare_digest(signature, expected):
        pass
    else:
        bootstrap_secret = None
        if source == 'development-default':
            bootstrap_secret = _frame_maintenance_secret(frame)
        if not bootstrap_secret:
            raise ValueError('invalid frame signature')
        expected = _maintenance_frame_signature_with_secret(frame, bootstrap_secret)
        if not hmac.compare_digest(signature, expected):
            raise ValueError('invalid frame signature')
        _install_maintenance_secret(bootstrap_secret)

    if not hmac.compare_digest(signature, expected):
        raise ValueError('invalid frame signature')

    expires_at = frame.get('expires_at')
    if expires_at is not None:
        try:
            expires_at_value = float(expires_at)
        except (TypeError, ValueError) as e:
            raise ValueError('invalid frame expiry') from e
        if (now if now is not None else time.time()) > expires_at_value:
            raise ValueError('expired maintenance frame')


async def _stop_maintenance_shell(reason='closed'):
    global maintenance_shell_process, maintenance_shell_reader_task, maintenance_active_session_id
    process = maintenance_shell_process
    task = maintenance_shell_reader_task
    current_task = asyncio.current_task()
    maintenance_shell_process = None
    maintenance_shell_reader_task = None

    if task and task is not current_task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    if process and process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    _set_maintenance_stdout_obj({
        'type': 'closed',
        'session_id': _maintenance_session_id(),
        'reason': reason,
    })
    maintenance_active_session_id = None


async def _read_maintenance_shell_stdout(process, session_id):
    try:
        while True:
            chunk = await process.stdout.read(256)
            if not chunk:
                break
            _emit_maintenance_text(
                chunk.decode('utf-8', errors='replace'),
                session_id=_maintenance_session_id(session_id),
            )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        _emit_maintenance_text(
            f'\n[maintenance stdout error: {e}]\n',
            session_id=_maintenance_session_id(session_id),
        )
    finally:
        if maintenance_shell_process is process:
            await _stop_maintenance_shell('shell exited')


async def _ensure_maintenance_shell(session_id=None):
    global maintenance_shell_process, maintenance_shell_reader_task, maintenance_active_session_id
    if maintenance_shell_process and maintenance_shell_process.returncode is None:
        if session_id:
            maintenance_active_session_id = session_id
        return maintenance_shell_process

    maintenance_active_session_id = session_id or maintenance_active_session_id or 'unknown'
    env = dict(os.environ)
    env.setdefault('TERM', 'dumb')
    process = await asyncio.create_subprocess_exec(
        '/bin/bash',
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=MAINTENANCE_REPO_DIR if os.path.isdir(MAINTENANCE_REPO_DIR) else os.getcwd(),
        env=env,
    )
    maintenance_shell_process = process
    maintenance_shell_reader_task = asyncio.create_task(
        _read_maintenance_shell_stdout(process, maintenance_active_session_id)
    )
    _set_maintenance_stdout_obj({
        'type': 'session_opened',
        'session_id': maintenance_active_session_id,
        'text': 'Maintenance shell ready\n',
    })
    return process


def _safe_update_id(update_id):
    update_id = str(update_id or '').strip()
    if not MAINTENANCE_UPDATE_ID_RE.match(update_id):
        raise ValueError('invalid update_id')
    return update_id


def _update_paths(update_id):
    safe_id = _safe_update_id(update_id)
    base = Path(MAINTENANCE_UPDATE_DIR) / safe_id
    return {
        'base': base,
        'tmp': base / 'artifact.bin.tmp',
        'artifact': base / 'artifact.bin',
        'meta': base / 'metadata.json',
    }


def _read_update_meta(update_id):
    paths = _update_paths(update_id)
    try:
        with open(paths['meta'], 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def _update_meta_time(meta):
    for key in ('applied_at', 'verified_at', 'updated_at', 'started_at'):
        try:
            value = float(meta.get(key, 0))
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return 0


def _newer_verified_bbb_master_update(update_id, meta):
    if not str(update_id).startswith('bbb-master-'):
        return None

    current_time = _update_meta_time(meta)
    update_root = Path(MAINTENANCE_UPDATE_DIR)
    try:
        candidates = list(update_root.iterdir())
    except FileNotFoundError:
        return None

    newest_id = None
    newest_time = current_time
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        candidate_id = candidate.name
        if candidate_id == update_id or not candidate_id.startswith('bbb-master-'):
            continue
        if not MAINTENANCE_UPDATE_ID_RE.match(candidate_id):
            continue
        candidate_meta = _read_update_meta(candidate_id)
        if not candidate_meta or candidate_meta.get('status') not in ('verified', 'applied'):
            continue
        if not _update_paths(candidate_id)['artifact'].exists():
            continue
        candidate_time = _update_meta_time(candidate_meta)
        if candidate_time > newest_time:
            newest_id = candidate_id
            newest_time = candidate_time

    return newest_id


def _write_update_meta(update_id, meta):
    paths = _update_paths(update_id)
    paths['base'].mkdir(parents=True, exist_ok=True)
    with open(paths['meta'], 'w', encoding='utf-8') as f:
        json.dump(meta, f, separators=(',', ':'), sort_keys=True)


def _emit_update_status(event_type, update_id, text, **extra):
    obj = {
        'type': event_type,
        'update_id': update_id,
        'text': text,
    }
    obj.update(extra)
    _set_maintenance_stdout_obj(obj)


def _emit_update_ack(frame, update_id, text, **extra):
    frame_type = str(frame.get('type') or frame.get('kind') or frame.get('op') or '').lower()
    obj = {
        'type': 'ack',
        'ack_type': frame_type,
        'frame_type': frame_type,
        'update_id': update_id,
        'text': text,
        'session_id': frame.get('session_id') or frame.get('sessionId') or _maintenance_session_id(),
        'commandId': frame.get('commandId'),
        'command_id': frame.get('command_id'),
    }
    obj.update(extra)
    _set_maintenance_stdout_obj(obj)


def _handle_update_begin(frame):
    update_id = _safe_update_id(frame.get('update_id'))
    expected_size = int(frame.get('size', -1))
    expected_sha = str(frame.get('sha256', '')).lower()
    if expected_size <= 0 or not re.match(r'^[a-f0-9]{64}$', expected_sha):
        raise ValueError('invalid update size or sha256')
    paths = _update_paths(update_id)
    paths['base'].mkdir(parents=True, exist_ok=True)
    with open(paths['tmp'], 'wb'):
        pass
    meta = {
        'update_id': update_id,
        'expected_size': expected_size,
        'expected_sha256': expected_sha,
        'received': 0,
        'status': 'receiving',
        'started_at': time.time(),
    }
    _write_update_meta(update_id, meta)
    maintenance_updates[update_id] = meta
    _emit_update_ack(
        frame,
        update_id,
        f'Update begin accepted for {update_id}\n',
        size=expected_size,
        sha256=expected_sha,
        status='receiving',
        received=0,
        expected_size=expected_size,
    )
    _emit_update_status('update_receiving', update_id, f'Receiving update {update_id}\n')


def _handle_update_chunk(frame):
    update_id = _safe_update_id(frame.get('update_id'))
    paths = _update_paths(update_id)
    meta = _read_update_meta(update_id)
    if not meta or meta.get('status') != 'receiving':
        raise ValueError('update is not receiving')
    offset = int(frame.get('offset', -1))
    try:
        chunk = base64.b64decode(str(frame.get('data_b64', '')), validate=True)
    except Exception as e:
        raise ValueError(f'invalid update chunk base64: {e}') from e
    current_size = paths['tmp'].stat().st_size if paths['tmp'].exists() else 0
    if offset != current_size:
        raise ValueError(f'chunk offset mismatch: got {offset}, expected {current_size}')
    expected_size = int(meta['expected_size'])
    if current_size + len(chunk) > expected_size:
        raise ValueError('update chunk exceeds expected size')
    with open(paths['tmp'], 'ab') as f:
        f.write(chunk)
    meta['received'] = current_size + len(chunk)
    meta['updated_at'] = time.time()
    _write_update_meta(update_id, meta)
    _emit_update_ack(
        frame,
        update_id,
        f'Update chunk accepted for {update_id} at {offset}\n',
        offset=offset,
        size=len(chunk),
        received=meta['received'],
        expected_size=expected_size,
        status='receiving',
    )
    if meta['received'] == expected_size:
        _emit_update_status(
            'update_received',
            update_id,
            f'Received {meta["received"]} bytes for {update_id}\n',
            size=meta['received'],
        )


def _handle_update_finalize(frame):
    update_id = _safe_update_id(frame.get('update_id'))
    paths = _update_paths(update_id)
    meta = _read_update_meta(update_id)
    if not meta:
        raise ValueError('unknown update')
    expected_size = int(meta['expected_size'])
    actual_size = paths['tmp'].stat().st_size if paths['tmp'].exists() else -1
    if actual_size != expected_size:
        raise ValueError(f'update size mismatch: got {actual_size}, expected {expected_size}')
    digest = hashlib.sha256()
    with open(paths['tmp'], 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    actual_sha = digest.hexdigest()
    if actual_sha != meta['expected_sha256']:
        raise ValueError('update sha256 mismatch')
    _validate_update_archive(paths['tmp'])
    os.replace(paths['tmp'], paths['artifact'])
    meta.update({
        'status': 'verified',
        'sha256': actual_sha,
        'size': actual_size,
        'verified_at': time.time(),
    })
    _write_update_meta(update_id, meta)
    _emit_update_ack(
        frame,
        update_id,
        f'Update finalize accepted for {update_id}\n',
        sha256=actual_sha,
        size=actual_size,
        received=actual_size,
        expected_size=expected_size,
        status='verified',
    )
    _emit_update_status(
        'update_verified',
        update_id,
        f'Verified update {update_id}: {actual_size} bytes\n',
        sha256=actual_sha,
        size=actual_size,
    )


def _handle_update_status(frame):
    update_id = _safe_update_id(frame.get('update_id'))
    meta = _read_update_meta(update_id)
    if not meta:
        _emit_update_status('update_status', update_id, f'No staged update {update_id}\n', status='missing')
        return
    _emit_update_status(
        'update_status',
        update_id,
        f'Update {update_id}: {meta.get("status", "unknown")}\n',
        status=meta.get('status'),
        size=meta.get('size') or meta.get('received'),
        sha256=meta.get('sha256') or meta.get('expected_sha256'),
    )


def _validate_tar_member(member):
    name = member.name
    if name.startswith('/') or '..' in Path(name).parts:
        raise ValueError(f'unsafe tar path: {name}')
    if member.islnk() or member.issym() or member.isdev():
        raise ValueError(f'unsafe tar member type: {name}')


def _tar_contains_bbb_snapshot(members):
    names = [Path(member.name) for member in members]
    root_names = {path.parts[0] for path in names if path.parts}
    candidate_roots = ['']
    if len(root_names) == 1:
        candidate_roots.append(next(iter(root_names)))

    for root in candidate_roots:
        prefix = f'{root}/' if root else ''
        has_dashboard = any(member.name == f'{prefix}dashboard.py' for member in members)
        has_bumble = any(member.name == f'{prefix}rotorsync_bumble.py' for member in members)
        has_src = any(
            member.name == f'{prefix}src' or member.name.startswith(f'{prefix}src/')
            for member in members
        )
        if has_dashboard and has_bumble and has_src:
            return True

    return False


def _validate_update_archive(artifact_path):
    if not tarfile.is_tarfile(artifact_path):
        raise ValueError('update artifact is not a tar archive')

    with tarfile.open(artifact_path) as archive:
        members = archive.getmembers()
        for member in members:
            _validate_tar_member(member)
        if not _tar_contains_bbb_snapshot(members):
            raise ValueError('update tar does not look like a BBB repo snapshot')


def _find_extracted_update_root(extract_dir):
    root = Path(extract_dir)
    if (root / 'dashboard.py').exists() and (root / 'rotorsync_bumble.py').exists():
        return root
    children = [child for child in root.iterdir() if child.is_dir()]
    if len(children) == 1:
        child = children[0]
        if (child / 'dashboard.py').exists() and (child / 'rotorsync_bumble.py').exists():
            return child
    raise ValueError('update tar does not look like a BBB repo snapshot')


def _copy_path(src, dst):
    src = Path(src)
    dst = Path(dst)
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    elif src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _path_owner(path):
    stat = Path(path).stat()
    return stat.st_uid, stat.st_gid


def _chown_path_recursive(path, uid, gid):
    path = Path(path)
    if not path.exists():
        return
    os.chown(path, uid, gid)
    if path.is_dir():
        for root, dirs, files in os.walk(path):
            for name in dirs + files:
                os.chown(Path(root) / name, uid, gid)


def _restore_repo_runtime_ownership(repo):
    try:
        uid, gid = _path_owner(repo)
    except OSError as e:
        print(f'Could not read maintenance repo owner: {e}', flush=True)
        return

    for name in MAINTENANCE_RUNTIME_PATHS:
        try:
            _chown_path_recursive(Path(repo) / name, uid, gid)
        except OSError as e:
            print(f'Could not restore ownership for {name}: {e}', flush=True)


def _backup_current_runtime(update_id):
    backup_dir = Path(MAINTENANCE_UPDATE_DIR) / update_id / 'backup'
    backup_dir.mkdir(parents=True, exist_ok=True)
    repo = Path(MAINTENANCE_REPO_DIR)
    for name in MAINTENANCE_RUNTIME_PATHS:
        src = repo / name
        if src.exists():
            _copy_path(src, backup_dir / name)
    return backup_dir


def _restore_runtime_backup(backup_dir):
    repo = Path(MAINTENANCE_REPO_DIR)
    backup_dir = Path(backup_dir)
    for name in MAINTENANCE_RUNTIME_PATHS:
        src = backup_dir / name
        dst = repo / name
        if src.exists():
            _copy_path(src, dst)
            continue
        if dst.is_dir():
            shutil.rmtree(dst)
        elif dst.exists():
            dst.unlink()


def _refresh_opt_runtime(repo_root):
    opt_root = Path('/opt')
    (opt_root / 'src').mkdir(parents=True, exist_ok=True)
    _copy_path(repo_root / 'rotorsync_bumble.py', opt_root / 'rotorsync_bumble.py')
    _copy_path(repo_root / 'rotorsync_watchdog.py', opt_root / 'rotorsync_watchdog.py')
    _copy_path(repo_root / 'src', opt_root / 'src')
    mopeka_src = repo_root / 'mopeka'
    mopeka_dst = opt_root / 'mopeka'
    if mopeka_src.exists():
        mopeka_dst.mkdir(parents=True, exist_ok=True)
        for file_path in mopeka_src.iterdir():
            if not file_path.is_file():
                continue
            if file_path.name == 'mopeka_config.json' and (mopeka_dst / file_path.name).exists():
                continue
            shutil.copy2(file_path, mopeka_dst / file_path.name)
    for relative in ('deploy/bbb-logrotate.conf', 'deploy/bbb-logrotate.service', 'deploy/bbb-logrotate.timer'):
        src = repo_root / relative
        if not src.exists():
            continue
        name = Path(relative).name
        dst = Path('/etc/logrotate.d/bbb') if name == 'bbb-logrotate.conf' else Path('/etc/systemd/system') / name
        _copy_path(src, dst)


def _apply_tar_update(update_id, artifact_path):
    extract_dir = Path(MAINTENANCE_TMP_DIR) / update_id
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(artifact_path) as archive:
        for member in archive.getmembers():
            _validate_tar_member(member)
        archive.extractall(extract_dir)

    update_root = _find_extracted_update_root(extract_dir)
    for required in ('dashboard.py', 'rotorsync_bumble.py', 'src'):
        if not (update_root / required).exists():
            raise ValueError(f'update is missing {required}')

    subprocess.run(
        ['python3', '-m', 'py_compile', str(update_root / 'dashboard.py'), str(update_root / 'rotorsync_bumble.py')],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    subprocess.run(
        ['python3', '-m', 'compileall', '-q', str(update_root / 'src')],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    repo = Path(MAINTENANCE_REPO_DIR)
    if not repo.exists():
        raise ValueError(f'{MAINTENANCE_REPO_DIR} does not exist')

    backup_dir = _backup_current_runtime(update_id)
    try:
        for name in MAINTENANCE_RUNTIME_PATHS:
            src = update_root / name
            if src.exists():
                _copy_path(src, repo / name)

        _restore_repo_runtime_ownership(repo)
        _refresh_opt_runtime(repo)
        subprocess.run(['systemctl', 'daemon-reload'], capture_output=True, text=True, timeout=10)
        subprocess.run(['systemctl', 'enable', '--now', 'bbb-logrotate.timer'], capture_output=True, text=True, timeout=10)
    except Exception as apply_error:
        try:
            _restore_runtime_backup(backup_dir)
            _restore_repo_runtime_ownership(repo)
        except Exception as rollback_error:
            raise RuntimeError(
                f'update apply failed and rollback failed: {apply_error}; rollback: {rollback_error}'
            ) from rollback_error
        raise RuntimeError(f'update apply failed; restored previous runtime: {apply_error}') from apply_error
    return update_root


def _schedule_service_restart():
    restart_cmd = (
        'sleep 1; '
        'systemctl restart iol_dashboard.service rotorsync_watchdog.service; '
        'systemctl restart rotorsync.service'
    )
    try:
        subprocess.run(
            [
                'systemd-run',
                '--unit=bbb-post-update-restart',
                '--description=Restart BBB services after maintenance update',
                '--on-active=1s',
                '/bin/bash',
                '-lc',
                restart_cmd,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as e:
        print(f'systemd-run restart scheduling failed; using fallback: {e}', flush=True)
        subprocess.Popen(['bash', '-lc', restart_cmd])


def _handle_update_apply(frame):
    update_id = _safe_update_id(frame.get('update_id'))
    paths = _update_paths(update_id)
    meta = _read_update_meta(update_id)
    if not meta or meta.get('status') != 'verified' or not paths['artifact'].exists():
        raise ValueError('update is not verified')
    newer_update_id = _newer_verified_bbb_master_update(update_id, meta)
    if newer_update_id:
        raise ValueError(f'stale update {update_id}; newer verified update {newer_update_id} exists')
    if not tarfile.is_tarfile(paths['artifact']):
        raise ValueError('verified artifact is not a tar archive')

    _emit_update_status('update_applying', update_id, f'Applying update {update_id}\n')
    try:
        _apply_tar_update(update_id, paths['artifact'])
    except Exception as e:
        meta['status'] = 'apply_failed'
        meta['apply_error'] = str(e)
        meta['apply_failed_at'] = time.time()
        _write_update_meta(update_id, meta)
        _emit_update_status(
            'update_apply_failed',
            update_id,
            f'Update apply failed; previous runtime restored: {e}\n',
            status='apply_failed',
            reason=str(e),
        )
        raise
    meta['status'] = 'applied'
    meta['applied_at'] = time.time()
    _write_update_meta(update_id, meta)
    _emit_update_status(
        'update_applied',
        update_id,
        'Update applied; restarting BBB services\n',
        status='restarting',
    )
    _schedule_service_restart()


async def _write_maintenance_stdin_bytes(data, session_id=None):
    process = await _ensure_maintenance_shell(session_id)
    if not process.stdin:
        raise RuntimeError('maintenance shell stdin unavailable')
    process.stdin.write(data)
    await process.stdin.drain()


async def _handle_maintenance_control_payload(payload_bytes):
    global maintenance_active_session_id
    frame, raw = _parse_maintenance_payload(payload_bytes)
    if not frame:
        print('Maintenance control error: unsigned control payload', flush=True)
        _set_maintenance_stdout_obj({
            'type': 'error',
            'session_id': _maintenance_session_id(),
            'text': 'unsigned maintenance control payload rejected\n',
            'reason': 'missing frame signature',
        })
        return

    frame_type = str(frame.get('type') or frame.get('kind') or frame.get('op') or '').lower()
    session_id = frame.get('session_id') or frame.get('sessionId') or maintenance_active_session_id

    try:
        _verify_maintenance_frame(frame)
        if session_id:
            maintenance_active_session_id = str(session_id)

        if frame_type == 'open':
            await _ensure_maintenance_shell(str(session_id) if session_id else None)
        elif frame_type in ('heartbeat', 'resize'):
            _set_maintenance_stdout_obj({
                'type': frame_type,
                'session_id': _maintenance_session_id(),
                'text': f'{frame_type} ack\n',
            })
        elif frame_type == 'close':
            await _stop_maintenance_shell('remote close requested')
        elif frame_type == 'stdin':
            await _write_maintenance_stdin_bytes(str(frame.get('data', '')).encode('utf-8'), str(session_id) if session_id else None)
        elif frame_type == 'update_begin':
            _handle_update_begin(frame)
        elif frame_type == 'update_chunk':
            _handle_update_chunk(frame)
        elif frame_type == 'update_finalize':
            _handle_update_finalize(frame)
        elif frame_type == 'update_status':
            _handle_update_status(frame)
        elif frame_type == 'update_apply':
            _handle_update_apply(frame)
        else:
            data = frame.get('data') or frame.get('command') or frame.get('cmd')
            if data:
                await _write_maintenance_stdin_bytes(str(data).encode('utf-8'), str(session_id) if session_id else None)
            else:
                _emit_maintenance_text(f'Unsupported maintenance frame type: {frame_type or "unknown"}\n')
    except Exception as e:
        print(f'Maintenance control error: {e}', flush=True)
        _set_maintenance_stdout_obj({
            'type': 'error',
            'session_id': _maintenance_session_id(),
            'text': f'{e}\n',
            'reason': str(e),
            'ack_type': frame_type or None,
            'frame_type': frame_type or None,
            'update_id': frame.get('update_id'),
            'offset': frame.get('offset'),
            'commandId': frame.get('commandId'),
            'command_id': frame.get('command_id'),
        })


async def _handle_maintenance_stdin_payload(payload_bytes):
    frame, raw = _parse_maintenance_payload(payload_bytes)
    if not frame:
        print('Maintenance stdin error: unsigned stdin payload', flush=True)
        _set_maintenance_stdout_obj({
            'type': 'error',
            'session_id': _maintenance_session_id(),
            'text': 'unsigned maintenance stdin payload rejected\n',
            'reason': 'missing frame signature',
        })
        return

    frame_type = str(frame.get('type') or frame.get('kind') or frame.get('op') or '').lower()
    session_id = frame.get('session_id') or frame.get('sessionId') or maintenance_active_session_id
    try:
        _verify_maintenance_frame(frame)
        if frame_type not in ('stdin', 'input'):
            raise ValueError(f'unsupported maintenance stdin frame type: {frame_type or "unknown"}')
        session_id = frame.get('session_id') or frame.get('sessionId') or maintenance_active_session_id
        data = frame.get('data')
        if data is None:
            data = frame.get('input') or frame.get('text') or ''
        await _write_maintenance_stdin_bytes(str(data).encode('utf-8'), str(session_id) if session_id else None)
    except Exception as e:
        print(f'Maintenance stdin error: {e}', flush=True)
        _set_maintenance_stdout_obj({
            'type': 'error',
            'session_id': _maintenance_session_id(),
            'text': f'{e}\n',
            'reason': str(e),
            'ack_type': frame_type or None,
            'frame_type': frame_type or None,
            'commandId': frame.get('commandId'),
            'command_id': frame.get('command_id'),
        })


def maintenance_control_write_handler(connection, value):
    payload = _decode_maintenance_write(connection, value, 'control')
    if payload is None:
        return
    try:
        asyncio.get_running_loop().create_task(_handle_maintenance_control_payload(payload))
    except RuntimeError:
        print('Maintenance control ignored: no event loop', flush=True)


def maintenance_stdin_write_handler(connection, value):
    payload = _decode_maintenance_write(connection, value, 'stdin')
    if payload is None:
        return
    try:
        asyncio.get_running_loop().create_task(_handle_maintenance_stdin_payload(payload))
    except RuntimeError:
        print('Maintenance stdin ignored: no event loop', flush=True)


def _notify_config_response(payload=None):
    if not ble_device or not config_notify_char:
        return
    payload_text = payload if payload is not None else config_response
    payload_chunks = _config_response_notify_frames(payload_text)

    async def _notify():
        for payload_chunk in payload_chunks:
            try:
                await ble_device.notify_subscribers(config_notify_char, payload_chunk.encode('utf-8'))
                if len(payload_chunks) > 1:
                    await asyncio.sleep(CONFIG_RESPONSE_CHUNK_NOTIFY_DELAY_SECONDS)
            except Exception as e:
                print(f'Config notify error: {e}', flush=True)
                break

    try:
        asyncio.get_running_loop().create_task(_notify())
    except RuntimeError:
        pass


def _config_response_notify_frames(payload_text):
    payload_text = payload_text or ''
    if len(payload_text.encode('utf-8')) <= CONFIG_RESPONSE_DIRECT_MAX_BYTES:
        return [payload_text]

    chunks = [
        payload_text[index:index + CONFIG_RESPONSE_CHUNK_SIZE]
        for index in range(0, len(payload_text), CONFIG_RESPONSE_CHUNK_SIZE)
    ]
    return [
        f'CHUNK:{index + 1}/{len(chunks)}:{chunk}'
        for index, chunk in enumerate(chunks)
    ]


def _config_response_read_value(payload_text):
    return _config_response_notify_frames(payload_text)[0]


def _next_config_response_read_value(connection):
    connection_key = _connection_key(connection)
    payload_text = config_response_by_connection.get(connection_key, config_response)
    frames = _config_response_notify_frames(payload_text)
    if len(frames) <= 1:
        config_response_read_index_by_connection[connection_key] = 0
        return frames[0]

    index = config_response_read_index_by_connection.get(connection_key, 0)
    frame = frames[min(index, len(frames) - 1)]
    config_response_read_index_by_connection[connection_key] = (index + 1) % len(frames)
    return frame


def _config_response_compression(cmd):
    compression = str(cmd.get('compression', '')).strip().lower()
    if compression in (CONFIG_RESPONSE_COMPRESSION, 'zlib+base64'):
        return CONFIG_RESPONSE_COMPRESSION
    return None


def _compressed_config_response_envelope(payload_text):
    payload_bytes = payload_text.encode('utf-8')
    compressor = zlib.compressobj(level=6, wbits=-zlib.MAX_WBITS)
    compressed = compressor.compress(payload_bytes) + compressor.flush()
    envelope = {
        'ok': True,
        'encoding': 'deflate+base64',
        'data': base64.b64encode(compressed).decode('ascii'),
        'raw_bytes': len(payload_bytes),
        'compressed_bytes': len(compressed),
    }
    try:
        payload_obj = json.loads(payload_text)
        for key in ('op', 'request_id', 'page', 'total_pages', 'total_items'):
            if key in payload_obj:
                envelope[key] = payload_obj[key]
    except Exception:
        pass
    return json.dumps(envelope, separators=(',', ':'))


def _set_config_response_text(payload_text, *, compression=None):
    global config_response
    response_text = (
        _compressed_config_response_envelope(payload_text)
        if compression == CONFIG_RESPONSE_COMPRESSION
        else payload_text
    )
    config_response = response_text
    _notify_config_response(response_text)


def _set_config_response_obj(obj, *, compression=None):
    payload_text = json.dumps(obj, separators=(',', ':'))
    _set_config_response_text(payload_text, compression=compression)


def _schedule_identity_restart(reason, delay=1.0):
    async def _restart():
        await asyncio.sleep(delay)
        log_watchdog_event(reason)
        os._exit(0)

    try:
        asyncio.get_running_loop().create_task(_restart())
    except RuntimeError:
        pass


def _set_paginated_config_response(items, *, request_id=None, op=None, page_size_bytes=450, max_pages=None, compression=None):
    global config_response_pages, config_response_pages_by_request
    pages = paginate_response(items, page_size_bytes=page_size_bytes)
    if max_pages is not None and max_pages > 0 and len(pages) > max_pages:
        limited_items = []
        for page_json in pages[:max_pages]:
            try:
                limited_items.extend(json.loads(page_json).get('items', []))
            except Exception:
                pass
        pages = paginate_response(limited_items, page_size_bytes=page_size_bytes)
    enriched_pages = []
    for page_json in pages:
        page_obj = json.loads(page_json)
        if request_id is not None:
            page_obj['request_id'] = request_id
        if op:
            page_obj['op'] = op
        enriched_pages.append(json.dumps(page_obj, separators=(',', ':')))
    config_response_pages = enriched_pages
    if request_id:
        config_response_pages_by_request[request_id] = list(enriched_pages)
        if len(config_response_pages_by_request) > 24:
            for stale_request_id in list(config_response_pages_by_request.keys())[:-24]:
                config_response_pages_by_request.pop(stale_request_id, None)
    if enriched_pages:
        _set_config_response_text(enriched_pages[0], compression=compression)
    else:
        _set_config_response_obj({
            'ok': True,
            'op': op,
            'request_id': request_id,
            'page': 1,
            'total_pages': 1,
            'total_items': 0,
            'items': [],
        }, compression=compression)

def pump_write_handler(connection, value):
    """Handle pump control writes. Write '1' or 'PS' to stop pump."""
    mark_gatt_client_seen(connection)
    cmd = value.decode('utf-8').strip().upper()
    print(f'Pump write from {_connection_key(connection)}: {cmd}', flush=True)
    if cmd in ['1', 'PS', 'STOP']:
        _enqueue_control_actions(connection, 'pump', 'PS')
    return


def command_write_handler(connection, value):
    """Handle compact JSON commands from the iOS app."""
    mark_gatt_client_seen(connection)
    try:
        payload = value.decode('utf-8').strip()
        print(f'Command write from {_connection_key(connection)}: {payload[:200]}', flush=True)
        cmd = json.loads(payload)
        if not isinstance(cmd, dict):
            raise ValueError('command payload must be a JSON object')
    except Exception as e:
        print(f'Command write parse error: {e}', flush=True)
        return

    command = str(cmd.get('cmd', '')).strip().lower()
    if not command:
        print('Command write ignored: missing cmd', flush=True)
        return

    if command in ('client_hello', 'hello'):
        _record_client_hello(connection, cmd)
        return

    if command in ('loc_update', 'loc'):
        _record_client_loc(connection, cmd)
        return

    if command == 'pump_stop':
        _enqueue_control_actions(connection, 'command:pump_stop', 'PS')
        return

    if command == 'confirm_fill':
        _enqueue_control_actions(connection, 'command:confirm_fill', 'TU')
        return

    if command in ('reset_flow', 'flow_reset'):
        _enqueue_control_actions(connection, 'command:reset_flow', 'RESET')
        return

    if command in ('ov', 'override_press', 'switch_ov'):
        _enqueue_control_actions(connection, 'command:ov', 'OV')
        return

    if command in ('update_box', 'run_update'):
        _enqueue_control_actions(connection, 'command:update_box', 'RUN_UPDATE', refresh=False)
        return

    if command in ('reboot_box', 'restart_box', 'reboot_system'):
        _enqueue_control_actions(connection, 'command:reboot_box', 'REBOOT', refresh=False)
        return

    if command in ('shutdown_box', 'poweroff_box', 'shutdown_system'):
        _enqueue_control_actions(connection, 'command:shutdown_box', 'SHUTDOWN', refresh=False)
        return

    if command in ('accept_pending_curve', 'apply_pending_curve'):
        _enqueue_control_actions(
            connection,
            'command:accept_pending_curve',
            'ACCEPT_PENDING_CURVE',
        )
        return

    if command in ('cursor_move', 'trackpad_move'):
        dx = _bounded_int(cmd.get('dx'), -250, 250)
        dy = _bounded_int(cmd.get('dy'), -250, 250)
        if dx or dy:
            _enqueue_cursor_command(connection, 'command:cursor_move', 'move', dx=dx, dy=dy)
        return

    if command in ('cursor_scroll', 'trackpad_scroll'):
        steps = _bounded_int(cmd.get('steps'), -8, 8)
        if steps:
            _enqueue_cursor_command(connection, 'command:cursor_scroll', 'scroll', steps=steps)
        return

    if command in ('cursor_click', 'trackpad_click'):
        button = _bounded_int(cmd.get('button'), 1, 3, default=1)
        _enqueue_cursor_command(connection, 'command:cursor_click', 'click', button=button)
        return

    if command in ('cursor_key', 'trackpad_key'):
        key = str(cmd.get('key', '')).strip().lower()
        if key in ('esc', 'escape', 'enter', 'return', 'alt_f4'):
            _enqueue_cursor_command(connection, 'command:cursor_key', 'key', key=key)
        else:
            print(f'Command write ignored: invalid cursor key {key!r}', flush=True)
        return

    if command == 'set_mode':
        mode = str(cmd.get('mode', '')).strip().lower()
        if mode == 'mix':
            _enqueue_control_actions(connection, 'command:set_mode', 'MIX')
        elif mode == 'fill':
            _enqueue_control_actions(connection, 'command:set_mode', 'FILL')
        else:
            print(f'Command write ignored: invalid mode {mode!r}', flush=True)
        return

    if command == 'adjust':
        delta = cmd.get('delta')
        allowed = {
            1: '+1',
            -1: '-1',
            10: '+10',
            -10: '-10',
        }
        try:
            delta = int(delta)
        except Exception:
            delta = None
        if delta in allowed:
            _enqueue_control_actions(connection, 'command:adjust', allowed[delta])
        else:
            print(f'Command write ignored: invalid delta {delta!r}', flush=True)
        return

    if command in ('set_target', 'set_requested_gallons', 'set_gallons'):
        gallons = _bounded_float(
            cmd.get('gallons', cmd.get('target', cmd.get('value'))),
            0.0,
            2140.0,
        )
        if gallons is None:
            print('Command write ignored: invalid requested gallons', flush=True)
            return
        _enqueue_control_actions(
            connection,
            'command:set_target',
            f'SET_REQUESTED_GALLONS:{gallons:.3f}',
        )
        return

    if command == 'set_override':
        enabled = cmd.get('enabled')
        if isinstance(enabled, bool):
            desired = enabled
        elif isinstance(enabled, int) and enabled in (0, 1):
            desired = bool(enabled)
        elif isinstance(enabled, str):
            normalized = enabled.strip().lower()
            if normalized in ('true', '1', 'yes', 'on'):
                desired = True
            elif normalized in ('false', '0', 'no', 'off'):
                desired = False
            else:
                print(f'Command write ignored: invalid override value {enabled!r}', flush=True)
                return
        else:
            print(f'Command write ignored: invalid override value {enabled!r}', flush=True)
            return
        _enqueue_control_actions(
            connection,
            'command:set_override',
            'OV:1' if desired else 'OV:0',
        )
        return

    if command == 'set_batchmix':
        data = cmd.get('data')
        if isinstance(data, dict):
            _send_validated_batchmix(json.dumps(data, separators=(",", ":")))
        else:
            print('Command write ignored: set_batchmix missing object data', flush=True)
        return

    print(f'Command write ignored: unsupported cmd {command!r}', flush=True)


def gallons_write_handler(connection, value):
    """Handle gallons adjustment. Write '+1', '-1', '+10', '-10'."""
    mark_gatt_client_seen(connection)
    cmd = value.decode('utf-8').strip()
    print(f'Gallons write from {_connection_key(connection)}: {cmd}', flush=True)

    if cmd in ['+1', '-1', '+10', '-10']:
        _enqueue_control_actions(connection, 'gallons', cmd)
    return

# Chunked data buffer for batch mix (handles large payloads)
batchmix_chunks = {}
batchmix_chunk_timeout = 30  # Seconds before incomplete chunks expire

def _send_validated_batchmix(compact_json):
    """Validate and forward a compact BatchMix JSON payload to the dashboard."""
    try:
        data = json.loads(compact_json)
    except json.JSONDecodeError as je:
        error_msg = f'Invalid JSON: {je}'
        print(f'BatchMix ERROR: {error_msg}', flush=True)
        send_dashboard_command(f'BATCHMIX_ERROR:{error_msg}')
        return False

    error_msg = batchmix_validation_error(data)
    if error_msg:
        print(f'BatchMix ERROR: {error_msg}', flush=True)
        send_dashboard_command(f'BATCHMIX_ERROR:{error_msg}')
        return False

    print(f'BatchMix validated: {len(data.get("products", []))} products', flush=True)
    send_dashboard_command(f'BATCHMIX:{compact_json}')
    return True

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

                    _send_validated_batchmix(compact_json)

                    # Clear buffer
                    batchmix_chunks = {}

        else:
            # Single write (no chunking) - data fits in one BLE write
            print(f'BatchMix single write: {len(data_str)} bytes', flush=True)
            # Strip newlines for socket transmission
            compact_json = data_str.replace('\n', '').replace('\r', '')

            _send_validated_batchmix(compact_json)

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


def _safe_calibration_profile_key(value):
    key = str(value or '').strip().lower().replace('_', '-')
    return ''.join(ch for ch in key if ch.isalnum() or ch == '-')


def _calibration_profile_key_for_box_tank(tank):
    cfg = load_config()
    mode = _normalize_box_mode(cfg.get('box_mode'))
    trailer = _get_assigned_trailer(cfg)
    tank = str(tank or '').strip().lower()
    tank = 'back' if tank.startswith('back') else 'front'

    if mode == 'fleet' and trailer not in (None, ''):
        return _safe_calibration_profile_key(f'trailer-{trailer}-{tank}')
    return f'customer-{tank}'


def _calibration_csv_path_for_cmd(cmd=None):
    cmd = cmd or {}
    profile = cmd.get('profile')
    if not profile and cmd.get('tank'):
        profile = _calibration_profile_key_for_box_tank(cmd.get('tank'))

    profile = _safe_calibration_profile_key(profile)
    if profile:
        os.makedirs(CALIBRATION_PROFILE_DIR, exist_ok=True)
        return os.path.join(CALIBRATION_PROFILE_DIR, f'{profile}.csv'), profile

    return CALIBRATION_CSV_PATH, ''


def load_calibration_csv(path=CALIBRATION_CSV_PATH):
    """Load calibration CSV. Standard header, no preamble."""
    points = []
    try:
        with open(path, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                points.append({
                    'tank_level_in': float(row['Tank Level (in)']),
                    'gallons': float(row['Gallons']),
                    'tank_size': float(row['Tank Size (gal)'])
                })
    except FileNotFoundError:
        print(f'Calibration CSV not found: {path}', flush=True)
    return points


def save_calibration_csv(points, path=CALIBRATION_CSV_PATH):
    """Write calibration points back to CSV, sorted descending by tank level."""
    points.sort(key=lambda p: p['tank_level_in'], reverse=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Tank Level (in)', 'Gallons', 'Tank Size (gal)'])
        for p in points:
            writer.writerow([p['tank_level_in'], p['gallons'], p['tank_size']])


def _calibration_file_mtimes():
    paths = [CALIBRATION_CSV_PATH]
    if os.path.isdir(CALIBRATION_PROFILE_DIR):
        for name in sorted(os.listdir(CALIBRATION_PROFILE_DIR)):
            if name.lower().endswith('.csv'):
                paths.append(os.path.join(CALIBRATION_PROFILE_DIR, name))

    mtimes = {}
    for path in paths:
        try:
            mtimes[path] = os.path.getmtime(path)
        except FileNotFoundError:
            mtimes[path] = None
    return mtimes


def reload_converter_if_calibration_changed(force=False):
    """Reload Mopeka conversion when dashboard-applied profiles change."""
    global calibration_mtime_snapshot, last_calibration_reload_check

    now = time.time()
    if not force and now - last_calibration_reload_check < 5.0:
        return

    last_calibration_reload_check = now
    current = _calibration_file_mtimes()
    if calibration_mtime_snapshot is None:
        calibration_mtime_snapshot = current
        return

    if current != calibration_mtime_snapshot:
        calibration_mtime_snapshot = current
        print('Calibration files changed; reloading Mopeka converter', flush=True)
        _reload_converter()


def load_config():
    """Load mopeka_config.json."""
    try:
        with open(MOPEKA_CONFIG_PATH, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            'box_mode': 'fleet',
            'assigned_trailer': None,
            'trailer': None,
        }


def save_config(cfg):
    """Write mopeka_config.json."""
    os.makedirs(os.path.dirname(MOPEKA_CONFIG_PATH), exist_ok=True)
    with open(MOPEKA_CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)


def _normalize_ble_mac(value):
    mac = str(value or '').strip().upper().replace('-', ':')
    parts = mac.split(':')
    if len(parts) != 6 or any(len(part) != 2 for part in parts):
        return None
    if any(any(ch not in '0123456789ABCDEF' for ch in part) for part in parts):
        return None
    return ':'.join(parts)


def _normalize_box_mode(value):
    mode = str(value or 'fleet').strip().lower()
    return 'customer' if mode == 'customer' else 'fleet'


def _get_assigned_trailer(cfg=None):
    cfg = cfg or load_config()
    return cfg.get('assigned_trailer', cfg.get('trailer'))


def _compute_bms_name(cfg=None):
    cfg = cfg or load_config()
    explicit_name = str(cfg.get('bms_name') or '').strip()
    if explicit_name:
        return explicit_name

    trailer = _get_assigned_trailer(cfg)
    if trailer not in (None, ''):
        return f'TR{trailer}-BMS'

    return BMS_NAME


def _box_mode_uses_trailer_list(cfg=None):
    cfg = cfg or load_config()
    return _normalize_box_mode(cfg.get('box_mode')) == 'fleet'


def _mopeka_enabled(cfg=None):
    cfg = cfg or load_config()
    return bool(cfg.get('mopeka_enabled', True))


def _unconfigured_ble_name():
    """BLE/mDNS name for a box with no trailer assigned. Includes the box serial
    (from the hostname) so several unconfigured boxes don't collide on one name
    over BLE or mDNS, e.g. host 'trailersync-sn009' -> 'TrailerSync-Uncfg-sn009'.
    Mirrors rotorlink.config.unconfigured_name() so BLE and WiFi share one identity.

    KEPT SHORT ('Uncfg', not 'Unconfigured'): the full name rides in the BLE
    scan-response, whose data is length-limited. The longer 'Unconfigured-<serial>'
    (~30 chars) overflows it on the fleet's Realtek adapters
    (HCI_LE_SET_EXTENDED_SCAN_RESPONSE_DATA -> INVALID_COMMAND_PARAMETERS), which
    aborts advertising and crash-loops the box. 'Uncfg-<serial>' (~23) fits."""
    host = socket.gethostname()
    serial = host
    for _prefix in ('trailersync-', 'rotorsync-'):
        if serial.lower().startswith(_prefix):
            serial = serial[len(_prefix):]
            break
    # Hard length clamp: 'TrailerSync-Uncfg-' is 18 chars and the full name rides
    # in the length-limited BLE scan-response — an unexpected hostname (e.g. one
    # not following the 'trailersync-<serial>' convention) must never overflow it
    # and crash-loop the box (HCI_LE_SET_EXTENDED_..._DATA INVALID_COMMAND_PARAMS).
    serial = serial.strip()[:11]
    return f'TrailerSync-Uncfg-{serial}' if serial else 'TrailerSync-Uncfg'


def _clamp_ble_name(name):
    """The full name rides in the BLE scan-response: COMPLETE_LOCAL_NAME inside a
    31-byte AD structure leaves 29 usable bytes. An over-long name (e.g. a long
    customer display_name) overflows it on the fleet's Realtek adapters
    (HCI_LE_SET_EXTENDED_SCAN_RESPONSE_DATA -> INVALID_COMMAND_PARAMETERS), which
    aborts advertising and crash-loops the box — the same failure the
    unconfigured-name path already guards against. Clamp on UTF-8 bytes so a
    multi-byte character is dropped whole, never split."""
    encoded = str(name or '').encode('utf-8')[:29]
    return encoded.decode('utf-8', 'ignore').strip()


def _compute_ble_name(cfg=None):
    cfg = cfg or load_config()
    mode = _normalize_box_mode(cfg.get('box_mode'))
    display_name = str(cfg.get('display_name') or '').strip()
    trailer = _get_assigned_trailer(cfg)

    if mode == 'customer':
        return _clamp_ble_name(display_name or DEFAULT_CUSTOMER_BLE_NAME)

    if trailer not in (None, ''):
        return _clamp_ble_name(f'TrailerSync-TR{trailer}')

    return _clamp_ble_name(display_name or _unconfigured_ble_name())


def _compute_short_ble_advertising_name(ble_name):
    """Return a compact identity that fits in legacy BLE advertising."""
    name = str(ble_name or '').strip()
    match = re.fullmatch(r'TrailerSync-(TR\d+)', name)
    if match:
        return match.group(1)
    # Unconfigured boxes: advertise the serial so the full name
    # ("TrailerSync-Uncfg-<serial>") still has a compact, unique form.
    match = re.fullmatch(r'TrailerSync-Uncfg-(.+)', name)
    if match:
        return match.group(1)
    return ''


def _current_box_config():
    cfg = load_config()
    mode = _normalize_box_mode(cfg.get('box_mode'))
    return {
        'box_mode': mode,
        'assigned_trailer': _get_assigned_trailer(cfg),
        'display_name': str(cfg.get('display_name') or '').strip(),
        'ble_name': _compute_ble_name(cfg),
        'mopeka_enabled': _mopeka_enabled(cfg),
        'trailer_list_enabled': mode == 'fleet',
    }


def enforce_bumble_only_stack():
    """Best-effort block of BlueZ so Bumble can keep exclusive HCI ownership."""
    commands = [
        ['systemctl', 'disable', '--now', 'bluetooth.service'],
        ['systemctl', 'mask', 'bluetooth.service'],
        ['systemctl', 'stop', 'bluetooth.service'],
    ]
    for cmd in commands:
        try:
            subprocess.run(cmd, capture_output=True)
        except Exception:
            pass


# =============================================================================
# Trailer Selection Logic
# =============================================================================

def clear_trailer_assignment():
    """Clear fleet trailer assignment without overwriting adapter settings."""
    global MOPEKA1_MAC_SUFFIX, MOPEKA2_MAC_SUFFIX, BMS_NAME
    cfg = load_config()
    cfg.update({
        'box_mode': _normalize_box_mode(cfg.get('box_mode')),
        'assigned_trailer': None,
        'trailer': None,
        'display_name': '',
        'front_id': '',
        'back_id': '',
    })
    save_config(cfg)
    MOPEKA1_MAC_SUFFIX = ''
    MOPEKA2_MAC_SUFFIX = ''
    BMS_NAME = _compute_bms_name(cfg)
    try:
        mopeka_reload()
    except Exception as e:
        print(f'mopeka_converter reload: {e}', flush=True)
    print('Cleared trailer assignment; box is unconfigured', flush=True)
    return _current_trailer_info()


def _is_clear_trailer_value(value):
    if value is None:
        return True
    text = str(value).strip().lower()
    return text in ('', '0', 'none', 'null', 'unconfigured', 'unassigned', 'clear')


def apply_trailer(trailer_num):
    """Look up trailer in sensor CSV, set MOPEKA1/2_MAC_SUFFIX globals,
    save config, reload mopeka_converter, return trailer info dict."""
    global MOPEKA1_MAC_SUFFIX, MOPEKA2_MAC_SUFFIX, BMS_NAME

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
    cfg = load_config()
    cfg.update({
        'box_mode': _normalize_box_mode(cfg.get('box_mode')),
        'assigned_trailer': trailer_num,
        'trailer': trailer_num,
        'front_id': front_id,
        'back_id': back_id,
        'display_name': f'TrailerSync-TR{trailer_num}',
    })
    save_config(cfg)
    BMS_NAME = _compute_bms_name(cfg)

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
    global MOPEKA1_MAC_SUFFIX, MOPEKA2_MAC_SUFFIX
    cfg = load_config()
    front_id = str(cfg.get('front_id') or '').strip().upper()
    back_id = str(cfg.get('back_id') or '').strip().upper()
    trailer_num = _get_assigned_trailer(cfg)

    # If no trailer is assigned, or the box is in customer mode, fall back to
    # the explicitly stored sensor IDs. This keeps manually configured boxes
    # working across restarts and prevents a blank trailer assignment from
    # discarding otherwise valid Mopeka sensor settings.
    if not _box_mode_uses_trailer_list(cfg) or trailer_num is None:
        restored = False

        if front_id and front_id != '---------------':
            MOPEKA1_MAC_SUFFIX = front_id
            restored = True

        if back_id and back_id != '---------------':
            MOPEKA2_MAC_SUFFIX = back_id
            restored = True

        if restored:
            print(
                f'Restored manual Mopeka IDs from config: '
                f'front={MOPEKA1_MAC_SUFFIX or "-"} back={MOPEKA2_MAC_SUFFIX or "-"}',
                flush=True,
            )
        elif not _box_mode_uses_trailer_list(cfg):
            print('Customer mode active; no manual Mopeka IDs configured', flush=True)
        else:
            print('No trailer assigned and no manual Mopeka IDs configured', flush=True)
        return

    if trailer_num is not None:
        result = apply_trailer(trailer_num)
        if result:
            print(f'Restored trailer {trailer_num} from config', flush=True)
        else:
            print(f'Failed to restore trailer {trailer_num}', flush=True)


def restore_bms_config():
    """Restore BMS config from mopeka_config.json."""
    global BMS_MAC, BMS_NAME
    cfg = load_config()
    saved_mac = _normalize_ble_mac(cfg.get('bms_mac'))
    if saved_mac:
        BMS_MAC = saved_mac
        print(f'Restored BMS MAC from config: {BMS_MAC}', flush=True)
    BMS_NAME = _compute_bms_name(cfg)
    print(f'Restored BMS name from config: {BMS_NAME}', flush=True)


def restore_adapter_config():
    """Restore per-box Bluetooth adapter MACs from mopeka_config.json."""
    global GATT_ADAPTER_MAC, SENSOR_ADAPTER_MAC
    cfg = load_config()

    saved_gatt = _normalize_ble_mac(cfg.get('gatt_adapter_mac'))
    if saved_gatt:
        GATT_ADAPTER_MAC = saved_gatt
        print(f'Restored GATT adapter MAC from config: {GATT_ADAPTER_MAC}', flush=True)

    saved_sensor = _normalize_ble_mac(cfg.get('sensor_adapter_mac'))
    if saved_sensor:
        SENSOR_ADAPTER_MAC = saved_sensor
        print(f'Restored sensor adapter MAC from config: {SENSOR_ADAPTER_MAC}', flush=True)


def persist_adapter_config():
    """Persist the active per-box Bluetooth adapter MACs to mopeka_config.json."""
    cfg = load_config()
    changed = False

    if GATT_ADAPTER_MAC and _normalize_ble_mac(cfg.get('gatt_adapter_mac')) != GATT_ADAPTER_MAC:
        cfg['gatt_adapter_mac'] = GATT_ADAPTER_MAC
        changed = True

    if SENSOR_ADAPTER_MAC and _normalize_ble_mac(cfg.get('sensor_adapter_mac')) != SENSOR_ADAPTER_MAC:
        cfg['sensor_adapter_mac'] = SENSOR_ADAPTER_MAC
        changed = True

    if changed:
        save_config(cfg)
        print(
            f'Persisted adapter config: gatt={GATT_ADAPTER_MAC} sensor={SENSOR_ADAPTER_MAC}',
            flush=True,
        )


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



def _wifi_code_from_response(resp):
    if not resp:
        return 'NO_RESPONSE'
    if resp.startswith('WIFI_OK:'):
        return 'OK'
    if resp.startswith('WIFI_ERR:'):
        try:
            payload = resp.split(':', 1)[1]
            data = json.loads(payload)
            return data.get('code', 'UNKNOWN')
        except Exception:
            return 'UNKNOWN'
    return 'BAD_RESPONSE'


def _wifi_set_from_ble(cmd):
    """Handle WIFI_SET operation from config command channel and forward to dashboard."""
    global config_response, config_response_pages
    request_id = cmd.get('request_id')

    ssid = str(cmd.get('ssid', '')).strip()
    password = str(cmd.get('password', ''))
    hidden = bool(cmd.get('hidden', False))

    if not ssid:
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'WIFI_SET', 'request_id': request_id, 'error': 'Missing ssid'})
        return

    if len(ssid) > 64:
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'WIFI_SET', 'request_id': request_id, 'error': 'SSID too long'})
        return

    if len(password) > 128:
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'WIFI_SET', 'request_id': request_id, 'error': 'Password too long'})
        return

    payload = {
        'ssid': ssid,
        'password': password,
        'hidden': hidden,
    }

    resp = send_dashboard_command(f"WIFI_SET:{json.dumps(payload, separators=(',', ':'))}")
    code = _wifi_code_from_response(resp)

    if resp and resp.startswith('WIFI_OK:'):
        try:
            data = json.loads(resp.split(':', 1)[1])
        except Exception:
            data = {'ssid': ssid}
        data['ok'] = True
        data['op'] = 'WIFI_SET'
        if request_id is not None:
            data['request_id'] = request_id
        _set_config_response_obj(data)
    else:
        _set_config_response_obj({'ok': False, 'op': 'WIFI_SET', 'request_id': request_id, 'error': code})

    config_response_pages = []


def _wifi_status_from_ble(cmd=None):
    """Query current WiFi status from dashboard."""
    global config_response, config_response_pages
    request_id = cmd.get('request_id') if isinstance(cmd, dict) else None

    resp = send_dashboard_command('WIFI_STATUS')
    if resp and resp.startswith('WIFI_STATUS:'):
        payload = resp.split(':', 1)[1]
        try:
            data = json.loads(payload)
        except Exception:
            data = {'ok': False, 'error': 'PARSE_ERROR'}
        data['op'] = 'WIFI_STATUS'
        if request_id is not None:
            data['request_id'] = request_id
        _set_config_response_obj(data)
    else:
        _set_config_response_obj({'ok': False, 'op': 'WIFI_STATUS', 'request_id': request_id, 'error': 'NO_RESPONSE'})
    config_response_pages = []


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
    request_id = cmd.get('request_id')
    print(f'Config command: {op}', flush=True)

    try:
        if op == 'WIFI_SET':
            _wifi_set_from_ble(cmd)
        elif op == 'WIFI_STATUS':
            _wifi_status_from_ble(cmd)
        elif op == 'GET_BOX_CONFIG':
            _cmd_get_box_config(request_id=request_id)
        elif op == 'SET_BOX_MODE':
            _cmd_set_box_mode(cmd, request_id=request_id)
        elif op == 'SET_DISPLAY_NAME':
            _cmd_set_display_name(cmd, request_id=request_id)
        elif op == 'SET_MOPEKA_ENABLED':
            _cmd_set_mopeka_enabled(cmd, request_id=request_id)
        elif op == 'GET_BMS':
            _cmd_get_bms(request_id=request_id)
        elif op == 'SET_BMS_MAC':
            _cmd_set_bms_mac(cmd, request_id=request_id)
        elif op == 'GET_TRAILER':
            _cmd_get_trailer(request_id=request_id)
        elif op == 'SELECT_TRAILER':
            _cmd_select_trailer(cmd, request_id=request_id)
        elif op == 'LIST_TRAILERS':
            _cmd_list_trailers(request_id=request_id)
        elif op == 'LIST_SENSORS':
            _cmd_list_sensors(cmd, request_id=request_id)
        elif op == 'ADD_SENSOR':
            _cmd_add_sensor(cmd, request_id=request_id)
        elif op == 'UPDATE_SENSOR':
            _cmd_update_sensor(cmd, request_id=request_id)
        elif op == 'DELETE_SENSOR':
            _cmd_delete_sensor(cmd, request_id=request_id)
        elif op == 'LIST_CALIBRATION':
            _cmd_list_calibration(cmd, request_id=request_id)
        elif op == 'ADD_CALIBRATION':
            _cmd_add_calibration(cmd, request_id=request_id)
        elif op == 'UPDATE_CALIBRATION':
            _cmd_update_calibration(cmd, request_id=request_id)
        elif op == 'DELETE_CALIBRATION':
            _cmd_delete_calibration(cmd, request_id=request_id)
        elif op == 'GET_MOPEKA_HISTORY':
            _cmd_get_mopeka_history(cmd, request_id=request_id)
        elif op == 'GET_FILL_HISTORY':
            _cmd_get_fill_history(cmd, request_id=request_id)
        elif op == 'GET_CONNECTIONS':
            _cmd_get_connections(request_id=request_id)
        elif op == 'GET_BOX_HEALTH':
            _cmd_get_box_health(request_id=request_id)
        elif op == 'GET_CONNECTION_LOG':
            _cmd_get_connection_log(cmd, request_id=request_id)
        elif op == 'PAGE':
            _cmd_page(cmd, request_id=request_id)
        else:
            _set_config_response_obj({'ok': False, 'error': f'Unknown op: {op}', 'request_id': request_id, 'op': op})
            config_response_pages = []
    except Exception as e:
        print(f'Config command error: {e}', flush=True)
        _set_config_response_obj({'ok': False, 'error': str(e), 'request_id': request_id, 'op': op})
        config_response_pages = []


def _current_trailer_info():
    cfg = load_config()
    mode = _normalize_box_mode(cfg.get('box_mode'))
    trailer_num = _get_assigned_trailer(cfg)
    if mode != 'fleet':
        return {'box_mode': mode, 'trailer': None, 'enabled': False}
    if trailer_num is None:
        return {'box_mode': mode, 'trailer': None, 'enabled': True}

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

    return {
        'box_mode': mode,
        'enabled': True,
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


def _cmd_get_trailer(*, request_id=None):
    global config_response_pages
    config_response_pages = []
    payload = {'ok': True, 'op': 'GET_TRAILER', 'current': _current_trailer_info()}
    if request_id is not None:
        payload['request_id'] = request_id
    _set_config_response_obj(payload)


def _cmd_get_box_config(*, request_id=None):
    global config_response_pages
    config_response_pages = []
    payload = {'ok': True, 'op': 'GET_BOX_CONFIG', 'box': _current_box_config()}
    if request_id is not None:
        payload['request_id'] = request_id
    _set_config_response_obj(payload)


def _cmd_set_box_mode(cmd, *, request_id=None):
    global config_response_pages
    mode = _normalize_box_mode(cmd.get('mode'))
    cfg = load_config()
    cfg['box_mode'] = mode
    if mode == 'customer':
        cfg['assigned_trailer'] = None
        cfg['trailer'] = None
        if not str(cfg.get('display_name') or '').strip():
            cfg['display_name'] = DEFAULT_CUSTOMER_BLE_NAME
    save_config(cfg)
    config_response_pages = []
    _set_config_response_obj({
        'ok': True,
        'op': 'SET_BOX_MODE',
        'request_id': request_id,
        'box': _current_box_config(),
    })
    _schedule_identity_restart(f'Box mode changed to {mode}; restarting to refresh BLE identity')


def _cmd_set_display_name(cmd, *, request_id=None):
    global config_response_pages
    display_name = str(cmd.get('display_name') or '').strip()
    config_response_pages = []
    if not display_name:
        _set_config_response_obj({
            'ok': False,
            'op': 'SET_DISPLAY_NAME',
            'request_id': request_id,
            'error': 'display_name required',
        })
        return

    cfg = load_config()
    cfg['display_name'] = display_name
    save_config(cfg)
    _set_config_response_obj({
        'ok': True,
        'op': 'SET_DISPLAY_NAME',
        'request_id': request_id,
        'box': _current_box_config(),
    })
    _schedule_identity_restart('Display name changed; restarting to refresh BLE identity')


def _cmd_set_mopeka_enabled(cmd, *, request_id=None):
    global config_response_pages
    cfg = load_config()
    cfg['mopeka_enabled'] = bool(cmd.get('enabled'))
    save_config(cfg)
    config_response_pages = []
    _set_config_response_obj({
        'ok': True,
        'op': 'SET_MOPEKA_ENABLED',
        'request_id': request_id,
        'box': _current_box_config(),
    })


def _cmd_get_bms(*, request_id=None):
    global config_response_pages
    config_response_pages = []
    payload = {
        'ok': True,
        'op': 'GET_BMS',
        'bms': {
            'mac': BMS_MAC,
            'name': _compute_bms_name(),
            'enabled': bool(BMS_ENABLED),
        },
    }
    if request_id is not None:
        payload['request_id'] = request_id
    _set_config_response_obj(payload)


def _cmd_set_bms_mac(cmd, *, request_id=None):
    global config_response_pages, BMS_MAC
    raw_mac = cmd.get('mac')
    mac = _normalize_ble_mac(raw_mac)
    config_response_pages = []

    if not mac:
        _set_config_response_obj({
            'ok': False,
            'op': 'SET_BMS_MAC',
            'request_id': request_id,
            'error': 'Invalid MAC address',
        })
        return

    BMS_MAC = mac
    cfg = load_config()
    cfg['bms_mac'] = mac
    save_config(cfg)
    _set_config_response_obj({
        'ok': True,
        'op': 'SET_BMS_MAC',
        'request_id': request_id,
        'bms': {
            'mac': BMS_MAC,
            'name': _compute_bms_name(),
            'enabled': bool(BMS_ENABLED),
        },
    })


def _cmd_select_trailer(cmd, *, request_id=None):
    global config_response_pages
    if not _box_mode_uses_trailer_list():
        config_response_pages = []
        _set_config_response_obj({
            'ok': False,
            'op': 'SELECT_TRAILER',
            'request_id': request_id,
            'error': 'Trailer selection disabled in customer mode',
        })
        return

    trailer_value = cmd.get('trailer')
    if _is_clear_trailer_value(trailer_value):
        result = clear_trailer_assignment()
        config_response_pages = []
        _set_config_response_obj({
            'ok': True,
            'op': 'SELECT_TRAILER',
            'request_id': request_id,
            'current': result,
            'box': _current_box_config(),
        })
        _schedule_identity_restart('Trailer cleared; restarting to refresh BLE identity')
        return

    try:
        trailer_num = int(trailer_value)
    except Exception:
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'SELECT_TRAILER', 'request_id': request_id, 'error': 'Invalid trailer'})
        return

    result = apply_trailer(trailer_num)
    config_response_pages = []
    if result is None:
        _set_config_response_obj({'ok': False, 'op': 'SELECT_TRAILER', 'request_id': request_id, 'error': f'Trailer {trailer_num} not found'})
        return

    _set_config_response_obj({'ok': True, 'op': 'SELECT_TRAILER', 'request_id': request_id, 'current': result})
    _schedule_identity_restart(f'Trailer changed to {trailer_num}; restarting to refresh BLE identity')


def _cmd_list_trailers(*, request_id=None):
    global config_response, config_response_pages
    if not _box_mode_uses_trailer_list():
        config_response_pages = []
        _set_config_response_obj({
            'ok': False,
            'op': 'LIST_TRAILERS',
            'request_id': request_id,
            'error': 'Trailer list disabled in customer mode',
        })
        return

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

    items = [
        {
            'trailer': 0,
            'man': 'Unconfigured',
            'label': 'Unconfigured',
            'display_name': DEFAULT_FLEET_BLE_NAME,
            'front': '',
            'back': '',
        }
    ] + sorted(trailers.values(), key=lambda x: x.get('trailer', 0))
    _set_paginated_config_response(items, request_id=request_id, op='LIST_TRAILERS')


def _cmd_list_sensors(cmd, *, request_id=None):
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

    _set_paginated_config_response(items, request_id=request_id, op='LIST_SENSORS')


def _cmd_add_sensor(cmd, *, request_id=None):
    global config_response, config_response_pages
    data = cmd.get('data')
    if not data:
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'ADD_SENSOR', 'request_id': request_id, 'error': 'Missing data field'})
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
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'ADD_SENSOR', 'request_id': request_id, 'error': 'Required: id, trailer, tank'})
        return

    sensors.append(new_sensor)
    tank_order = {'Front': 0, 'Back': 1}
    sensors.sort(key=lambda s: (int(s['Trailer']) if s.get('Trailer', '').isdigit() else 999,
                                tank_order.get(s.get('Tank', ''), 2)))
    save_sensor_csv(sensors)
    _reload_converter()

    config_response_pages = []
    _set_config_response_obj({'ok': True, 'op': 'ADD_SENSOR', 'request_id': request_id, 'id': new_sensor.get('Mopeka ID', '')})


def _cmd_update_sensor(cmd, *, request_id=None):
    global config_response, config_response_pages
    sensor_id = cmd.get('id')
    data = cmd.get('data')
    if not sensor_id or not data:
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'UPDATE_SENSOR', 'request_id': request_id, 'error': 'Required: id, data'})
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
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'UPDATE_SENSOR', 'request_id': request_id, 'error': f'Sensor {sensor_id} not found'})
        return

    save_sensor_csv(sensors)
    _reload_converter()

    config_response_pages = []
    _set_config_response_obj({'ok': True, 'op': 'UPDATE_SENSOR', 'request_id': request_id, 'id': sensor_id})


def _cmd_delete_sensor(cmd, *, request_id=None):
    global config_response, config_response_pages
    sensor_id = cmd.get('id')
    if not sensor_id:
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'DELETE_SENSOR', 'request_id': request_id, 'error': 'Required: id'})
        return

    sensors = load_sensor_csv()
    original_len = len(sensors)
    sensors = [s for s in sensors if s.get('Mopeka ID') != sensor_id]

    if len(sensors) == original_len:
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'DELETE_SENSOR', 'request_id': request_id, 'error': f'Sensor {sensor_id} not found'})
        return

    save_sensor_csv(sensors)
    _reload_converter()

    config_response_pages = []
    _set_config_response_obj({'ok': True, 'op': 'DELETE_SENSOR', 'request_id': request_id, 'id': sensor_id})


def _cmd_list_calibration(cmd=None, *, request_id=None):
    global config_response, config_response_pages
    path, profile = _calibration_csv_path_for_cmd(cmd or {})
    points = load_calibration_csv(path)

    items = []
    for i, p in enumerate(points):
        items.append({
            'index': i,
            'tank_level_in': p['tank_level_in'],
            'gallons': p['gallons'],
            'tank_size': p['tank_size']
        })

    _set_paginated_config_response(items, request_id=request_id, op='LIST_CALIBRATION')


def _cmd_add_calibration(cmd, *, request_id=None):
    global config_response, config_response_pages
    data = cmd.get('data')
    if not data:
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'ADD_CALIBRATION', 'request_id': request_id, 'error': 'Missing data field'})
        return

    if 'tank_level_in' not in data or 'gallons' not in data:
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'ADD_CALIBRATION', 'request_id': request_id, 'error': 'Required: tank_level_in, gallons'})
        return

    path, profile = _calibration_csv_path_for_cmd(cmd)
    points = load_calibration_csv(path)
    new_point = {
        'tank_level_in': float(data['tank_level_in']),
        'gallons': float(data['gallons']),
        'tank_size': float(data.get('tank_size', 1070.0))
    }
    points.append(new_point)
    save_calibration_csv(points, path)
    _reload_converter()

    config_response_pages = []
    _set_config_response_obj({
        'ok': True,
        'op': 'ADD_CALIBRATION',
        'request_id': request_id,
        'profile': profile or None,
    })


def _cmd_update_calibration(cmd, *, request_id=None):
    global config_response, config_response_pages
    index = cmd.get('index')
    data = cmd.get('data')
    if index is None or not data:
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'UPDATE_CALIBRATION', 'request_id': request_id, 'error': 'Required: index, data'})
        return

    path, profile = _calibration_csv_path_for_cmd(cmd)
    points = load_calibration_csv(path)
    if index < 0 or index >= len(points):
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'UPDATE_CALIBRATION', 'request_id': request_id, 'error': f'Index {index} out of range (0-{len(points)-1})'})
        return

    for key in ('tank_level_in', 'gallons', 'tank_size'):
        if key in data:
            points[index][key] = float(data[key])

    save_calibration_csv(points, path)
    _reload_converter()

    config_response_pages = []
    _set_config_response_obj({
        'ok': True,
        'op': 'UPDATE_CALIBRATION',
        'request_id': request_id,
        'index': index,
        'profile': profile or None,
    })


def _cmd_delete_calibration(cmd, *, request_id=None):
    global config_response, config_response_pages
    index = cmd.get('index')
    if index is None:
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'DELETE_CALIBRATION', 'request_id': request_id, 'error': 'Required: index'})
        return

    path, profile = _calibration_csv_path_for_cmd(cmd)
    points = load_calibration_csv(path)
    if index < 0 or index >= len(points):
        config_response_pages = []
        _set_config_response_obj({'ok': False, 'op': 'DELETE_CALIBRATION', 'request_id': request_id, 'error': f'Index {index} out of range (0-{len(points)-1})'})
        return

    points.pop(index)
    save_calibration_csv(points, path)
    _reload_converter()

    config_response_pages = []
    _set_config_response_obj({
        'ok': True,
        'op': 'DELETE_CALIBRATION',
        'request_id': request_id,
        'index': index,
        'profile': profile or None,
    })


def _cmd_page(cmd, *, request_id=None):
    global config_response
    page = cmd.get('page', 1)
    cursor_request_id = cmd.get('cursor_request_id')
    pages = config_response_pages_by_request.get(cursor_request_id) if cursor_request_id else config_response_pages

    if not pages:
        _set_config_response_obj({'ok': False, 'op': 'PAGE', 'request_id': request_id, 'error': 'No paginated data available'})
        return

    if page < 1 or page > len(pages):
        _set_config_response_obj({'ok': False, 'op': 'PAGE', 'request_id': request_id, 'error': f'Page {page} out of range (1-{len(pages)})'})
        return

    page_obj = json.loads(pages[page - 1])
    if cursor_request_id:
        page_obj['cursor_request_id'] = cursor_request_id
    if request_id is not None:
        page_obj['request_id'] = request_id
    _set_config_response_obj(page_obj, compression=_config_response_compression(cmd))


def process_config_command_for_connection(data_str, connection_key):
    """Run config command state against the caller's response/page buffers."""
    global config_response, config_response_pages

    previous_global_response = config_response
    previous_global_pages = config_response_pages
    config_response = config_response_by_connection.get(
        connection_key,
        previous_global_response,
    )
    config_response_pages = list(
        config_response_pages_by_connection.get(connection_key, [])
    )

    try:
        process_config_command(data_str)
        config_response_by_connection[connection_key] = config_response
        config_response_pages_by_connection[connection_key] = list(config_response_pages)
        config_response_read_index_by_connection[connection_key] = 0
        previous_global_response = config_response
        previous_global_pages = list(config_response_pages)
    finally:
        config_response = previous_global_response
        config_response_pages = previous_global_pages


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
    info = _current_trailer_info()
    value = json.dumps(info, separators=(',', ':'))
    print(f'ReadValue trailer: {value}', flush=True)
    return value.encode('utf-8')


def trailer_write_handler(connection, value):
    """Write trailer number to select and configure sensors for that trailer."""
    if not _box_mode_uses_trailer_list():
        print('Trailer write rejected: customer mode', flush=True)
        return

    trailer_str = value.decode('utf-8').strip()
    print(f'Trailer write: {trailer_str}', flush=True)
    if _is_clear_trailer_value(trailer_str):
        clear_trailer_assignment()
        _schedule_identity_restart('Trailer write cleared assignment; restarting to refresh BLE identity')
        return

    try:
        trailer_num = int(trailer_str)
    except ValueError:
        print(f'Invalid trailer number: {trailer_str}', flush=True)
        return

    result = apply_trailer(trailer_num)
    if result is None:
        print(f'Trailer {trailer_num} not found in sensor CSV', flush=True)
        return

    _schedule_identity_restart(f'Trailer write changed to {trailer_num}; restarting to refresh BLE identity')
    return


def config_cmd_write_handler(connection, value):
    """Handle config command writes. Supports chunked writes via CHUNK:X/Y:data pattern."""
    global config_cmd_chunks

    mark_gatt_client_seen(connection)
    try:
        data_str = value.decode('utf-8').strip()
        connection_key = _connection_key(connection)

        if data_str.startswith('CHUNK:'):
            parts = data_str.split(':', 2)
            if len(parts) >= 3:
                chunk_info = parts[1]
                chunk_data = parts[2]
                chunk_num, total_chunks = map(int, chunk_info.split('/'))

                print(f'ConfigCmd chunk {chunk_num}/{total_chunks} ({len(chunk_data)} bytes)', flush=True)

                buffer = config_cmd_chunks.get(connection_key)
                if not buffer or buffer.get('total') != total_chunks:
                    buffer = {
                        'chunks': {},
                        'total': total_chunks,
                        'timestamp': time.time()
                    }
                    config_cmd_chunks[connection_key] = buffer

                buffer['chunks'][chunk_num] = chunk_data
                buffer['timestamp'] = time.time()

                if len(buffer['chunks']) == total_chunks:
                    assembled = ''
                    for i in range(1, total_chunks + 1):
                        if i in buffer['chunks']:
                            assembled += buffer['chunks'][i]
                        else:
                            print(f'ConfigCmd: missing chunk {i}!', flush=True)
                            return
                    config_cmd_chunks.pop(connection_key, None)
                    process_config_command_for_connection(assembled, connection_key)
        else:
            process_config_command_for_connection(data_str, connection_key)

    except Exception as e:
        print(f'ConfigCmd error: {e}', flush=True)
    return


def config_data_read_handler(connection):
    """Read the response from the last config command."""
    mark_gatt_client_seen(connection)
    value = config_response_by_connection.get(_connection_key(connection), config_response)
    print(f'ReadValue config_data: {value[:80]}...', flush=True)
    return value.encode('utf-8')


def decode_mopeka(data):
    temp_raw = data[2] & 0x7F
    tank_raw = data[3] | ((data[4] & 0x3F) << 8)
    quality = (data[4] >> 6) & 0x03
    # Use the air coefficients from the reference parser so empty spray tanks
    # decode to the physical tank height instead of propane-liquid depth.
    level_mm = tank_raw * (0.153096 + 0.000327 * temp_raw - 0.000000294 * temp_raw * temp_raw)
    return {'level_mm': round(level_mm, 1), 'quality': quality}


def _float_or_empty(value, digits=3):
    try:
        return f'{float(value):.{digits}f}'
    except (TypeError, ValueError):
        return ''


def _int_or_empty(value):
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return ''


def _mopeka_history_snapshot(current_time, m1, m2):
    return {
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_time)),
        'front_gal': m1.get('gallons'),
        'back_gal': m2.get('gallons'),
        'front_mm': m1.get('level_mm'),
        'back_mm': m2.get('level_mm'),
        'front_in': m1.get('level_in'),
        'back_in': m2.get('level_in'),
        'front_quality': m1.get('quality'),
        'back_quality': m2.get('quality'),
    }


def _mopeka_history_change_reason(snapshot, previous_snapshot):
    if not previous_snapshot:
        return 'start'
    for key in ('front_gal', 'back_gal'):
        try:
            if abs(float(snapshot[key]) - float(previous_snapshot[key])) >= MOPEKA_HISTORY_CHANGE_THRESHOLD_GAL:
                return 'change'
        except (KeyError, TypeError, ValueError):
            continue
    return ''


def _append_mopeka_history_row(snapshot, reason):
    fieldnames = [
        'timestamp',
        'reason',
        'front_gal',
        'back_gal',
        'front_mm',
        'back_mm',
        'front_in',
        'back_in',
        'front_quality',
        'back_quality',
    ]
    row = {
        'timestamp': snapshot['timestamp'],
        'reason': reason,
        'front_gal': _float_or_empty(snapshot.get('front_gal'), 3),
        'back_gal': _float_or_empty(snapshot.get('back_gal'), 3),
        'front_mm': _float_or_empty(snapshot.get('front_mm'), 1),
        'back_mm': _float_or_empty(snapshot.get('back_mm'), 1),
        'front_in': _float_or_empty(snapshot.get('front_in'), 2),
        'back_in': _float_or_empty(snapshot.get('back_in'), 2),
        'front_quality': _int_or_empty(snapshot.get('front_quality')),
        'back_quality': _int_or_empty(snapshot.get('back_quality')),
    }
    needs_header = not os.path.exists(MOPEKA_HISTORY_LOG_PATH) or os.path.getsize(MOPEKA_HISTORY_LOG_PATH) == 0
    with open(MOPEKA_HISTORY_LOG_PATH, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if needs_header:
            writer.writeheader()
        writer.writerow(row)


def maybe_log_mopeka_history(current_time, m1, m2):
    global last_mopeka_history_log_at, last_mopeka_history_snapshot

    snapshot = _mopeka_history_snapshot(current_time, m1, m2)
    reason = _mopeka_history_change_reason(snapshot, last_mopeka_history_snapshot)
    if not reason and current_time - last_mopeka_history_log_at >= MOPEKA_HISTORY_BASELINE_INTERVAL:
        reason = 'periodic'
    if not reason:
        return False

    try:
        _append_mopeka_history_row(snapshot, reason)
        last_mopeka_history_log_at = current_time
        last_mopeka_history_snapshot = snapshot
        return True
    except Exception as e:
        print(f'Mopeka history log error: {e}', flush=True)
        return False


def _history_float(value):
    try:
        if value in (None, ''):
            return None
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _history_int(value):
    try:
        if value in (None, ''):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _history_timestamp_epoch(value):
    try:
        return int(time.mktime(time.strptime(value, '%Y-%m-%d %H:%M:%S')))
    except (TypeError, ValueError):
        return None


def _clamped_history_window(cmd, *, default_hours=12.0):
    now = time.time()
    min_since = now - HISTORY_RETENTION_SECONDS
    try:
        until = float(cmd.get('until', now))
    except (TypeError, ValueError):
        until = now
    until = max(min(until, now + 60.0), min_since)

    if 'since' in cmd:
        try:
            since = float(cmd.get('since'))
        except (TypeError, ValueError):
            since = until - (default_hours * 3600.0)
    else:
        try:
            hours = float(cmd.get('hours', default_hours))
        except (TypeError, ValueError):
            hours = default_hours
        hours = max(1.0, min(float(HISTORY_RETENTION_SECONDS) / 3600.0, hours))
        since = until - (hours * 3600.0)

    since = max(min(since, until), min_since)
    return since, until


def _history_named_field(parts, name):
    prefix = f'{name}:'
    for part in parts:
        text = part.strip()
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return ''


def _parse_float_token(value):
    try:
        cleaned = (
            str(value)
            .replace('gal', '')
            .replace('GPM', '')
            .replace('F', '')
            .replace('s', '')
            .strip()
        )
        if not cleaned:
            return None
        return round(float(cleaned), 3)
    except (TypeError, ValueError):
        return None


def _fill_history_item_from_line(line):
    # One shared parser with the WiFi server (src/fill_history.py) — the two
    # copies drifted once and loads fetched over WiFi lost pilot attribution.
    return _shared_fill_item_from_line(line)


def _prune_fill_history_file(now):
    if not os.path.exists(FILL_HISTORY_LOG_PATH):
        return

    try:
        if os.path.getsize(FILL_HISTORY_LOG_PATH) <= HISTORY_MAX_FILE_BYTES:
            return

        cutoff = now - HISTORY_RETENTION_SECONDS
        with open(FILL_HISTORY_LOG_PATH) as f:
            lines = f.readlines()
        kept = []
        for line in lines:
            item = _fill_history_item_from_line(line)
            if item and item['t'] >= cutoff:
                kept.append(line)
        if not kept and lines:
            kept = lines[-2000:]
        with open(FILL_HISTORY_LOG_PATH, 'w') as f:
            f.writelines(kept)
    except Exception as e:
        print(f'Fill history prune error: {e}', flush=True)


def _prune_mopeka_history_file(now):
    if not os.path.exists(MOPEKA_HISTORY_LOG_PATH):
        return

    try:
        if os.path.getsize(MOPEKA_HISTORY_LOG_PATH) <= HISTORY_MAX_FILE_BYTES:
            return

        cutoff = now - HISTORY_RETENTION_SECONDS
        with open(MOPEKA_HISTORY_LOG_PATH, newline='') as f:
            rows = list(csv.DictReader(f))
            fieldnames = f.fieldnames or []
        kept = []
        for row in rows:
            timestamp = _history_timestamp_epoch(row.get('timestamp'))
            if timestamp is not None and timestamp >= cutoff:
                kept.append(row)
        if not kept and rows:
            kept = rows[-2000:]
        with open(MOPEKA_HISTORY_LOG_PATH, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(kept)
    except Exception as e:
        print(f'Mopeka history prune error: {e}', flush=True)


def maybe_prune_history_files():
    global last_history_prune_at
    now = time.time()
    if now - last_history_prune_at < HISTORY_PRUNE_INTERVAL:
        return
    last_history_prune_at = now
    _prune_fill_history_file(now)
    _prune_mopeka_history_file(now)


def _history_newest_first_requested(cmd):
    value = cmd.get('newest_first')
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ('1', 'true', 'yes', 'y', 'desc', 'newest', 'newest_first'):
        return True
    order = str(cmd.get('order') or '').strip().lower()
    return order in ('desc', 'newest', 'newest_first')


def _mopeka_history_paths():
    path = Path(MOPEKA_HISTORY_LOG_PATH)
    paths = []
    if path.exists():
        paths.append(path)

    for rotated_path in sorted(
        path.parent.glob(f'{path.name}.*'),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    ):
        if rotated_path == path:
            continue
        if rotated_path.name.endswith('.zst') or rotated_path.suffix in ('', '.1') or rotated_path.name.endswith('.csv.1'):
            paths.append(rotated_path)

    return paths[:120]


def _read_mopeka_history_rows(path):
    try:
        if path.name.endswith('.zst'):
            result = subprocess.run(
                ['zstdcat', str(path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or '').strip()
                print(f'Mopeka history read skipped {path}: {detail}', flush=True)
                return []
            return list(csv.DictReader(io.StringIO(result.stdout)))

        with open(path, newline='') as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f'Mopeka history read skipped {path}: {e}', flush=True)
        return []


def _load_mopeka_history_items(cmd):
    since, until = _clamped_history_window(cmd)
    items = []
    seen = set()
    for path in _mopeka_history_paths():
        for row in _read_mopeka_history_rows(path):
            timestamp = _history_timestamp_epoch(row.get('timestamp'))
            if timestamp is None or timestamp < since or timestamp > until:
                continue
            item = {
                't': timestamp,
                'r': row.get('reason') or '',
                'fg': _history_float(row.get('front_gal')),
                'bg': _history_float(row.get('back_gal')),
                'fmm': _history_float(row.get('front_mm')),
                'bmm': _history_float(row.get('back_mm')),
                'fin': _history_float(row.get('front_in')),
                'bin': _history_float(row.get('back_in')),
                'fq': _history_int(row.get('front_quality')),
                'bq': _history_int(row.get('back_quality')),
            }
            key = (
                item['t'],
                item['fg'],
                item['bg'],
                item['fmm'],
                item['bmm'],
            )
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    return sorted(items, key=lambda item: item['t'], reverse=_history_newest_first_requested(cmd))


def _cmd_get_mopeka_history(cmd, *, request_id=None):
    maybe_prune_history_files()
    items = _load_mopeka_history_items(cmd)
    _set_paginated_config_response(
        items,
        request_id=request_id,
        op='GET_MOPEKA_HISTORY',
        page_size_bytes=8192,
        compression=_config_response_compression(cmd),
    )


def _load_fill_history_items(cmd):
    if not os.path.exists(FILL_HISTORY_LOG_PATH):
        return []

    since, until = _clamped_history_window(cmd)
    items = []
    with open(FILL_HISTORY_LOG_PATH) as f:
        for line in f:
            item = _fill_history_item_from_line(line)
            if not item:
                continue
            if item['t'] < since or item['t'] > until:
                continue
            items.append(item)
    return sorted(items, key=lambda item: item['t'])


def _cmd_get_fill_history(cmd, *, request_id=None):
    maybe_prune_history_files()
    items = _load_fill_history_items(cmd)
    _set_paginated_config_response(
        items,
        request_id=request_id,
        op='GET_FILL_HISTORY',
        page_size_bytes=450,
    )


def _cmd_get_connections(*, request_id=None):
    global config_response_pages
    config_response_pages = []
    payload = {
        'ok': True,
        'op': 'GET_CONNECTIONS',
        'connections': connection_registry.read_connections(),
    }
    if request_id is not None:
        payload['request_id'] = request_id
    _set_config_response_obj(payload)


def _cmd_get_box_health(*, request_id=None):
    global config_response_pages
    config_response_pages = []
    payload = {
        'ok': True,
        'op': 'GET_BOX_HEALTH',
        'health': connection_registry.box_health(),
    }
    if request_id is not None:
        payload['request_id'] = request_id
    _set_config_response_obj(payload)


def _cmd_get_connection_log(cmd, *, request_id=None):
    try:
        since = float(cmd.get('since', 0))
    except (TypeError, ValueError):
        since = 0.0
    try:
        limit = max(1, min(int(cmd.get('limit', 500)), 500))
    except (TypeError, ValueError):
        limit = 500
    items = connection_registry.read_log_since(since, limit=limit)
    _set_paginated_config_response(
        items,
        request_id=request_id,
        op='GET_CONNECTION_LOG',
        page_size_bytes=450,
    )


async def scan_mopeka(sensor_device, current_time):
    global last_gatt_self_adv_debug_log_at

    mopeka_found = False
    active_self_scan = _should_use_active_self_adv_scan(current_time)
    debug_ad_count = 0
    debug_ad_samples = []
    debug_match_count = 0
    debug_match_samples = []

    def on_advertisement(advertisement):
        nonlocal mopeka_found, debug_ad_count, debug_ad_samples
        nonlocal debug_match_count, debug_match_samples

        try:
            debug_ad_count += 1
            summary = _gatt_self_advertisement_debug_summary(advertisement)
            if len(debug_ad_samples) < 10:
                debug_ad_samples.append(summary)
            if _gatt_self_advertisement_matches(summary):
                debug_match_count += 1
                if len(debug_match_samples) < 5:
                    debug_match_samples.append(summary)
            maybe_mark_gatt_self_advertisement_seen(advertisement, current_time)
            addr = str(advertisement.address).upper()
            manufacturer_data = advertisement.data.get_all(
                AdvertisingData.MANUFACTURER_SPECIFIC_DATA
            )

            for company_id, data in manufacturer_data:
                if company_id != 89:
                    continue

                decoded = decode_mopeka(data)
                decoded['last_update'] = current_time

                if MOPEKA1_MAC_SUFFIX and MOPEKA1_MAC_SUFFIX in addr:
                    conversion = mm_to_gallons(decoded["level_mm"], MOPEKA1_MAC_SUFFIX)
                    decoded.update(conversion)
                    sensor_data['mopeka1'] = decoded
                    print(f'Mopeka1: {decoded}', flush=True)
                    mopeka_found = True
                elif MOPEKA2_MAC_SUFFIX and MOPEKA2_MAC_SUFFIX in addr:
                    conversion = mm_to_gallons(decoded["level_mm"], MOPEKA2_MAC_SUFFIX)
                    decoded.update(conversion)
                    sensor_data['mopeka2'] = decoded
                    print(f'Mopeka2: {decoded}', flush=True)
                    mopeka_found = True
        except Exception as e:
            print(f'Mopeka advertisement parse error: {e}', flush=True)

    sensor_device.on('advertisement', on_advertisement)
    try:
        await asyncio.wait_for(
            sensor_device.start_scanning(active=active_self_scan),
            timeout=MOPEKA_SCAN_OPERATION_TIMEOUT,
        )
        await asyncio.sleep(SCAN_TIMEOUT)
        try:
            await asyncio.wait_for(sensor_device.stop_scanning(), timeout=5)
        except Exception as e:
            print(f'Mopeka scan stop warning: {type(e).__name__}: {e!r}', flush=True)
    finally:
        sensor_device.remove_listener('advertisement', on_advertisement)

    should_log_self_scan = (
        active_self_scan
        or debug_match_count > 0
        or current_time - last_gatt_self_adv_debug_log_at >= GATT_SELF_ADV_DEBUG_LOG_SECONDS
    )
    if should_log_self_scan:
        target = {
            'address': gatt_self_advertisement_target.get('address') or '',
            'name': gatt_self_advertisement_target.get('name') or '',
        }
        print(
            'GATT self-scan: '
            f'active={str(active_self_scan).lower()} '
            f'target={json.dumps(target, separators=(",", ":"))} '
            f'saw={debug_ad_count} '
            f'matches={debug_match_count} '
            f'match_samples={json.dumps(debug_match_samples, separators=(",", ":"))} '
            f'samples={json.dumps(debug_ad_samples, separators=(",", ":"))}',
            flush=True,
        )
        last_gatt_self_adv_debug_log_at = current_time

    return mopeka_found


def _advertising_data_name(ad_data):
    data_type = getattr(AdvertisingData, 'Type', AdvertisingData)
    for attr in ('COMPLETE_LOCAL_NAME', 'SHORTENED_LOCAL_NAME'):
        key = getattr(data_type, attr, None)
        if key is None:
            key = getattr(AdvertisingData, attr, None)
        if key is None:
            continue
        try:
            value = ad_data.get(key)
        except Exception:
            value = None
        if value:
            return value
    return None


async def find_sensor_peer_by_name(sensor_device, name, timeout):
    """Resolve a sensor name without Bumble's duplicate-advertisement race."""
    loop = asyncio.get_running_loop()
    peer_address = loop.create_future()

    def on_advertisement(advertisement):
        if peer_address.done():
            return
        if _advertising_data_name(advertisement.data) == name:
            peer_address.set_result(advertisement.address)

    was_scanning = getattr(sensor_device, 'scanning', False)
    sensor_device.on('advertisement', on_advertisement)
    try:
        if not was_scanning:
            await sensor_device.start_scanning(filter_duplicates=True)
        return await asyncio.wait_for(peer_address, timeout=timeout)
    finally:
        try:
            sensor_device.remove_listener('advertisement', on_advertisement)
        except Exception:
            pass
        if not was_scanning:
            try:
                await sensor_device.stop_scanning()
            except Exception as e:
                print(
                    f'Sensor name scan stop warning: {type(e).__name__}: {e!r}',
                    flush=True,
                )


async def read_bms(sensor_device, current_time):
    response_buffer = bytearray()
    notification_received = asyncio.Event()
    connection = None
    peer = None
    notify_char = None
    write_char = None
    subscriber = None

    try:
        connect_errors = []
        for target in (BMS_MAC, BMS_NAME):
            try:
                connect_target = target
                if target == BMS_NAME:
                    connect_target = await find_sensor_peer_by_name(
                        sensor_device,
                        target,
                        BMS_TIMEOUT,
                    )
                connection = await sensor_device.connect(
                    connect_target,
                    own_address_type=hci.OwnAddressType.RANDOM,
                    timeout=BMS_TIMEOUT,
                )
                print(f'BMS connected via {target}', flush=True)
                break
            except Exception as e:
                connect_errors.append(f'{target}: {type(e).__name__} {e!r}')

        if connection is None:
            raise TimeoutError('; '.join(connect_errors))

        peer = Peer(connection)
        await peer.discover_services()
        notify_chars = await peer.discover_characteristics(uuids=[BMS_NOTIFY_UUID])
        write_chars = await peer.discover_characteristics(uuids=[BMS_WRITE_UUID])

        if not notify_chars or not write_chars:
            raise RuntimeError('BMS characteristics not found')

        notify_char = notify_chars[0]
        write_char = write_chars[0]

        def on_bms_notification(value):
            response_buffer.extend(value)
            notification_received.set()

        subscriber = on_bms_notification
        await peer.subscribe(notify_char, subscriber)
        # Match the older working flow: wake/prime with cell-info, then request hw-info.
        await peer.write_value(write_char, jbd_cmd(0x04), with_response=False)
        await asyncio.sleep(1.0)
        notification_received.clear()
        response_buffer.clear()
        await peer.write_value(write_char, jbd_cmd(0x03), with_response=False)

        hwinfo_frame = None
        deadline = time.time() + 5.0
        while time.time() < deadline:
            await asyncio.wait_for(notification_received.wait(), timeout=max(0.1, deadline - time.time()))
            notification_received.clear()
            hwinfo_frame = _extract_jbd_frame(response_buffer, expected_function=0x03)
            if hwinfo_frame is not None:
                break

        if hwinfo_frame is None:
            raise RuntimeError('Timed out waiting for complete BMS hardware-info frame')

        payload_len = hwinfo_frame[3]
        d = hwinfo_frame[4:4 + payload_len]
        if len(d) < 23:
            raise RuntimeError(f'BMS hardware-info payload too short ({len(d)} bytes)')

        sensor_data['bms'] = {
            'voltage': round(int.from_bytes(d[0:2], 'big') * 0.01, 2),
            'soc': d[19],
            'last_update': current_time
        }
        print(f'BMS: {sensor_data["bms"]}', flush=True)
        return True
    finally:
        if peer and notify_char and subscriber:
            try:
                await peer.unsubscribe(notify_char, subscriber)
            except Exception:
                pass
        if connection:
            try:
                await connection.disconnect()
            except Exception:
                pass

def jbd_cmd(func):
    frame = [0xDD, 0xA5, func, 0x00]
    crc = sum([-b for b in frame[2:4]]) & 0xFFFF
    return bytes(frame + [crc >> 8, crc & 0xFF, 0x77])

async def poll_dashboard_status(device, state_char):
    """Periodically poll dashboard state and notify subscribers on change."""
    poll_count = 0
    initial_state_json = dashboard_status.get('state_json', '{}')
    last_notified_state_compare_json = _state_notify_compare_json(
        initial_state_json,
        suppress_live_fields=False,
    )
    while True:
        try:
            updated = query_dashboard_status()
            current_state_json = dashboard_status.get('state_json', '{}')
            current_state = dashboard_status.get('state') or {}
            suppress_live_fields = _state_notify_should_suppress_live_fields(
                len(active_gatt_connections),
                current_state,
            )
            current_state_compare_json = _state_notify_compare_json(
                current_state_json,
                suppress_live_fields=suppress_live_fields,
            )
            if updated and current_state_compare_json != last_notified_state_compare_json:
                await device.notify_subscribers(
                    state_char,
                    bytes(current_state_json, 'utf-8'),
                )
                last_notified_state_compare_json = current_state_compare_json
            # Poll history less often than live state; it only changes after fills.
            poll_count += 1
            if poll_count >= STATUS_HISTORY_POLL_CYCLES:
                query_fill_history()
                poll_count = 0
        except Exception as e:
            print(f'Status poll error: {e}', flush=True)
        await asyncio.sleep(STATUS_POLL_INTERVAL)


def _live_telemetry_notify_due(
    controller_count,
    now,
    last_notify_at,
    flow_active=False,
    pilot_priority_active=False,
):
    if flow_active:
        if controller_count > 1 and pilot_priority_active:
            return now - last_notify_at >= LIVE_TELEMETRY_PILOT_PRIORITY_NOTIFY_INTERVAL
        return True
    if controller_count <= 1:
        return True
    return now - last_notify_at >= LIVE_TELEMETRY_MULTIPOINT_NOTIFY_INTERVAL


async def poll_live_telemetry(device, live_char):
    """Poll only actual gallons and flow fast, then notify subscribers on change."""
    last_notified_live_json = dashboard_status.get('live_json', '{}')
    last_live_notify_at = 0.0
    while True:
        try:
            now = time.time()
            cached_state = dashboard_status.get('state') or {}
            recent_client_read = (
                now - last_live_telemetry_client_read_at
                <= LIVE_TELEMETRY_FAST_READ_WINDOW
            )
            should_poll = (
                active_gatt_connections
                and (recent_client_read or _state_live_telemetry_active(cached_state))
            )
            if should_poll:
                updated = query_live_telemetry()
                current_live_json = dashboard_status.get('live_json', '{}')
                current_state = dashboard_status.get('state') or {}
                flow_active = _state_live_telemetry_active(current_state)
                notify_due = _live_telemetry_notify_due(
                    len(active_gatt_connections),
                    now,
                    last_live_notify_at,
                    flow_active=flow_active,
                    pilot_priority_active=_pilot_priority_active(current_state),
                )
                if updated and current_live_json != last_notified_live_json and notify_due:
                    await device.notify_subscribers(
                        live_char,
                        bytes(current_live_json, 'utf-8'),
                    )
                    last_notified_live_json = current_live_json
                    last_live_notify_at = now
        except Exception as e:
            print(f'Live telemetry poll error: {e}', flush=True)
        await asyncio.sleep(LIVE_TELEMETRY_POLL_INTERVAL)

async def read_sensors(sensor_device, sensor_adapter):
    global sensor_data, sensor_loop_heartbeat

    cfg = load_config()
    mopeka_enabled = _mopeka_enabled(cfg)

    print(f"Sensor reader started on {sensor_adapter}", flush=True)
    if BMS_ENABLED:
        print(f"Scan interval: {SCAN_INTERVAL}s, BMS every {BMS_READ_INTERVAL} cycles", flush=True)
    else:
        print(f"Scan interval: {SCAN_INTERVAL}s, BMS polling disabled", flush=True)
    print(f"Mopeka polling {'enabled' if mopeka_enabled else 'disabled'}", flush=True)
    print(f"Auto-recovery after {MAX_CONSECUTIVE_FAILURES} failures", flush=True)

    cycle_count = 0
    mopeka_failures = 0
    bms_failures = 0
    last_adapter_reset = 0
    mopeka_disabled_announced = False

    while True:
        sensor_loop_heartbeat = time.time()
        cycle_count += 1
        current_time = time.time()
        reload_converter_if_calibration_changed()

        if mopeka_enabled and mopeka_failures >= MAX_CONSECUTIVE_FAILURES:
            if current_time - last_adapter_reset > ADAPTER_RESET_COOLDOWN:
                print(
                    'Mopeka remains offline after repeated scans; keeping GATT bridge up',
                    flush=True,
                )
                send_dashboard_command('MOPEKA_OFFLINE')
                mopeka_failures = 0
                last_adapter_reset = current_time

        if bms_failures >= MAX_CONSECUTIVE_FAILURES:
            if current_time - last_adapter_reset > ADAPTER_RESET_COOLDOWN:
                print(
                    'BMS remains offline after repeated reads; keeping GATT bridge up',
                    flush=True,
                )
                bms_failures = 0
                last_adapter_reset = current_time

        mopeka_defer_reason = gatt_sensor_defer_reason('mopeka', now=current_time)
        if mopeka_enabled and mopeka_defer_reason:
            maybe_log_sensor_defer('Mopeka', mopeka_defer_reason, now=current_time)
        elif mopeka_enabled:
            try:
                mopeka_found = await asyncio.wait_for(
                    scan_mopeka(sensor_device, current_time),
                    timeout=MOPEKA_SCAN_OPERATION_TIMEOUT + 2,
                )

                if mopeka_found:
                    mopeka_failures = 0
                    m1 = sensor_data["mopeka1"]
                    m2 = sensor_data["mopeka2"]
                    m1_gal = m1.get("gallons", 0)
                    m2_gal = m2.get("gallons", 0)
                    m1_q = m1.get("quality", 0)
                    m2_q = m2.get("quality", 0)
                    m1_mm = m1.get("level_mm", 0)
                    m2_mm = m2.get("level_mm", 0)
                    m1_in = m1.get("level_in", 0)
                    m2_in = m2.get("level_in", 0)
                    maybe_log_mopeka_history(current_time, m1, m2)
                    send_dashboard_command(f"MOPEKA:{m1_gal:.0f}|{m2_gal:.0f}|{m1_q}|{m2_q}")
                    send_dashboard_command(f"MOPEKA_RAW:{m1_mm:.1f}|{m2_mm:.1f}|{m1_in:.2f}|{m2_in:.2f}")
                else:
                    mopeka_failures += 1
                    if mopeka_failures > 1:
                        print(f'Mopeka not found ({mopeka_failures}/{MAX_CONSECUTIVE_FAILURES})', flush=True)
                        send_dashboard_command('MOPEKA_OFFLINE')

            except Exception as e:
                mopeka_failures += 1
                print(
                    f'Mopeka error ({mopeka_failures}/{MAX_CONSECUTIVE_FAILURES}) '
                    f'{type(e).__name__}: {e!r}',
                    flush=True,
                )
        else:
            mopeka_failures = 0
            if not mopeka_disabled_announced:
                send_dashboard_command('MOPEKA_DISABLED')
                mopeka_disabled_announced = True

        sensor_loop_heartbeat = time.time()
        if BMS_ENABLED and (cycle_count == 1 or cycle_count % BMS_READ_INTERVAL == 0):
            bms_defer_reason = gatt_sensor_defer_reason('bms', now=time.time())
            if bms_defer_reason:
                maybe_log_sensor_defer('BMS', bms_defer_reason, now=time.time())
            else:
                try:
                    if await asyncio.wait_for(
                        read_bms(sensor_device, current_time),
                        timeout=BMS_READ_OPERATION_TIMEOUT,
                    ):
                        bms_failures = 0
                        bms = sensor_data["bms"]
                        send_dashboard_command(f"BMS:{bms.get('soc', 0)}|{bms.get('voltage', 0):.2f}")
                except Exception as e:
                    bms_failures += 1
                    print(
                        f'BMS error ({bms_failures}/{MAX_CONSECUTIVE_FAILURES}) '
                        f'{type(e).__name__}: {e!r}',
                        flush=True,
                    )

        await asyncio.sleep(SCAN_INTERVAL)


def _handle_sensor_task_done(task):
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        log_watchdog_event('Sensor task was cancelled; exiting for restart')
        os._exit(1)
    except Exception as e:
        log_watchdog_event(f'Failed to inspect sensor task completion: {type(e).__name__}: {e}')
        os._exit(1)

    if exc is not None:
        log_watchdog_event(f'Sensor task crashed: {type(exc).__name__}: {exc}')
        os._exit(1)

    log_watchdog_event('Sensor task exited unexpectedly; exiting for restart')
    os._exit(1)


async def monitor_sensor_health(sensor_task):
    global sensor_loop_heartbeat

    while True:
        await asyncio.sleep(10)

        if sensor_task.done():
            _handle_sensor_task_done(sensor_task)
            return

        if sensor_loop_heartbeat == 0:
            continue

        stale_for = time.time() - sensor_loop_heartbeat
        if stale_for > SENSOR_LOOP_HEARTBEAT_TIMEOUT:
            log_watchdog_event(
                f'Sensor loop heartbeat stale for {stale_for:.0f}s; exiting for restart'
            )
            os._exit(1)


async def open_hci_socket_transport_with_retry(
    adapter_index,
    adapter_name,
    *,
    attempts=HCI_SOCKET_OPEN_RETRY_ATTEMPTS,
    delay=HCI_SOCKET_OPEN_RETRY_DELAY_SECONDS,
    timeout=None,
):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            prepare_adapter_for_hci_user_channel(adapter_name)
            if timeout is None:
                return await open_hci_socket_transport(adapter_index)
            return await asyncio.wait_for(
                open_hci_socket_transport(adapter_index),
                timeout=timeout,
            )
        except Exception as e:
            last_error = e
            if attempt >= attempts:
                break
            print(
                f'WARNING: {adapter_name} HCI socket open failed '
                f'({attempt}/{attempts}): {e!r}; retrying in {delay:.1f}s',
                flush=True,
            )
            await asyncio.sleep(delay)

    raise last_error or RuntimeError(f'{adapter_name} HCI socket open failed')


async def main():
    global ble_device, config_notify_char, maintenance_stdout_char
    print('Starting Rotorsync GATT server (Bumble)...', flush=True)
    _log_maintenance_secret_status()
    ensure_cursor_control_setup()
    # Initialize Mopeka gallon converter
    mopeka_init()
    reload_converter_if_calibration_changed(force=True)

    # Restore adapter config from last session
    restore_adapter_config()

    # Restore BMS config from last session
    restore_bms_config()

    # Restore trailer config from last session
    restore_trailer_config()

    # Find adapters by known USB chip role first, then saved MAC fallback.
    gatt_adapter, sensor_adapter = select_runtime_adapters()

    if not gatt_adapter:
        print(f'ERROR: GATT adapter {GATT_ADAPTER_MAC} not found!', flush=True)
        return
    if not sensor_adapter:
        print(f'WARNING: Sensor adapter {SENSOR_ADAPTER_MAC} not found - sensors disabled', flush=True)

    gatt_adapter_index = int(gatt_adapter.replace('hci', ''))
    sensor_adapter_index = int(sensor_adapter.replace('hci', '')) if sensor_adapter else None
    persist_adapter_config()
    gatt_device_path = get_adapter_device_path(gatt_adapter)
    persist_gatt_device_path(gatt_device_path)
    print(f'GATT adapter: {gatt_adapter} ({GATT_ADAPTER_MAC})', flush=True)
    if sensor_adapter:
        print(f'Sensor adapter: {sensor_adapter} ({SENSOR_ADAPTER_MAC})', flush=True)

    enforce_bumble_only_stack()
    await asyncio.sleep(0.5)

    sensor_device = None
    if sensor_adapter and sensor_adapter_index is not None:
        print(f'Opening HCI socket for {sensor_adapter}...', flush=True)
        try:
            sensor_transport = await open_hci_socket_transport_with_retry(
                sensor_adapter_index,
                sensor_adapter,
                attempts=5,
                delay=HCI_SOCKET_OPEN_RETRY_DELAY_SECONDS,
                timeout=SENSOR_ADAPTER_OPEN_TIMEOUT,
            )
            sensor_host = Host(sensor_transport.source, sensor_transport.sink)
            sensor_device = Device(name='TrailerSync-Sensors', host=sensor_host)
            await sensor_device.power_on()
        except Exception as e:
            print(
                f'WARNING: Sensor adapter {sensor_adapter} open failed; sensors disabled: {e!r}',
                flush=True,
            )
            sensor_adapter = None

    if sensor_adapter:
        sensor_task = asyncio.create_task(read_sensors(sensor_device, sensor_adapter))
        sensor_task.add_done_callback(_handle_sensor_task_done)
        asyncio.create_task(monitor_sensor_health(sensor_task))
    gatt_monitor_task = asyncio.create_task(
        monitor_gatt_adapter(gatt_adapter, gatt_device_path)
    )

    print(f'Opening HCI socket for {gatt_adapter}...', flush=True)
    hci_transport = await open_hci_socket_transport_with_retry(
        gatt_adapter_index,
        gatt_adapter,
    )

    host = Host(hci_transport.source, hci_transport.sink)
    ble_name = _compute_ble_name()
    device = Device(name=ble_name, host=host)
    ble_device = device

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
    state_char = Characteristic(
        STATE_CHAR_UUID,
        Characteristic.Properties.READ | Characteristic.Properties.NOTIFY,
        Characteristic.READABLE,
        CharacteristicValue(read=make_state_read_handler()),
    )
    live_telemetry_char = Characteristic(
        LIVE_TELEMETRY_CHAR_UUID,
        Characteristic.Properties.READ | Characteristic.Properties.NOTIFY,
        Characteristic.READABLE,
        CharacteristicValue(read=make_live_telemetry_read_handler()),
    )
    command_char = Characteristic(
        COMMAND_CHAR_UUID,
        Characteristic.Properties.WRITE | Characteristic.Properties.WRITE_WITHOUT_RESPONSE,
        Characteristic.WRITEABLE,
        CharacteristicValue(write=command_write_handler),
    )

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
    config_notify_char = Characteristic(
        CONFIG_NOTIFY_CHAR_UUID,
        Characteristic.Properties.READ | Characteristic.Properties.NOTIFY,
        Characteristic.READABLE,
        CharacteristicValue(read=make_config_notify_read_handler()),
    )
    maintenance_control_char = Characteristic(
        MAINTENANCE_CONTROL_CHAR_UUID,
        Characteristic.Properties.WRITE | Characteristic.Properties.WRITE_WITHOUT_RESPONSE,
        Characteristic.WRITEABLE,
        CharacteristicValue(write=maintenance_control_write_handler),
    )
    maintenance_stdin_char = Characteristic(
        MAINTENANCE_STDIN_CHAR_UUID,
        Characteristic.Properties.WRITE | Characteristic.Properties.WRITE_WITHOUT_RESPONSE,
        Characteristic.WRITEABLE,
        CharacteristicValue(write=maintenance_stdin_write_handler),
    )
    maintenance_stdout_char = Characteristic(
        MAINTENANCE_STDOUT_CHAR_UUID,
        Characteristic.Properties.READ | Characteristic.Properties.NOTIFY,
        Characteristic.READABLE,
        CharacteristicValue(read=make_maintenance_stdout_read_handler()),
    )

    service = Service(
        SERVICE_UUID,
        [
            bms_char,
            mopeka1_char,
            mopeka2_char,
            pump_char,
            gallons_char,
            requested_char,
            actual_char,
            state_char,
            live_telemetry_char,
            command_char,
            history_char,
            batchmix_char,
            trailer_char,
            config_cmd_char,
            config_data_char,
            config_notify_char,
            maintenance_control_char,
            maintenance_stdin_char,
            maintenance_stdout_char,
        ],
    )
    device.add_service(service)
    if await wait_for_dashboard_ready():
        print('Dashboard socket is ready', flush=True)
    else:
        print(
            f'Dashboard socket not ready after {STARTUP_DASHBOARD_WAIT_SECONDS}s; '
            'continuing and relying on retry loop',
            flush=True,
        )
    start_control_command_worker()
    status_task = asyncio.create_task(poll_dashboard_status(device, state_char))
    live_telemetry_notify_task = asyncio.create_task(
        poll_live_telemetry(device, live_telemetry_char)
    )

    await device.power_on()
    print(f'Device address: {device.public_address}', flush=True)
    print(f'BLE name: {ble_name}', flush=True)

    short_ble_name = _compute_short_ble_advertising_name(ble_name)
    advertising_fields = [
        (AdvertisingData.INCOMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS, bytes(SERVICE_UUID)),
    ]
    if short_ble_name:
        advertising_fields.append(
            (AdvertisingData.SHORTENED_LOCAL_NAME, short_ble_name.encode('utf-8'))
        )
        print(f'BLE short advertising name: {short_ble_name}', flush=True)
    adv_data = AdvertisingData(advertising_fields)
    scan_response = AdvertisingData([
        (AdvertisingData.COMPLETE_LOCAL_NAME, ble_name.encode('utf-8')),
    ])
    advertising_payload = bytes(adv_data)
    scan_response_payload = bytes(scan_response)
    install_gatt_advertising_resume_hook(
        device,
        advertising_payload,
        scan_response_payload,
        ble_name,
    )

    await device.start_advertising(
        advertising_data=advertising_payload,
        scan_response_data=scan_response_payload,
        auto_restart=False,
    )
    persist_gatt_advertising_ready(ble_name, device.public_address)
    persist_gatt_connection_state('advertising_ready')
    print('=== Rotorsync GATT Server Running ===', flush=True)
    print('Characteristics:', flush=True)
    print('  def1: BMS (read)        - {"voltage": x, "soc": y}', flush=True)
    print('  def2: Mopeka1 (read)    - {"level_mm": x, "quality": y, "gallons": z}', flush=True)
    print('  def3: Mopeka2 (read)    - {"level_mm": x, "quality": y, "gallons": z}', flush=True)
    print('  def4: Pump (write)      - "PS" to stop pump', flush=True)
    print('  def5: Gallons (write)   - "+1", "-1", "+10", "-10"', flush=True)
    print('  def6: Requested (read)  - requested gallons', flush=True)
    print('  def7: Actual (read)     - actual gallons', flush=True)
    print('  defd: State (r/n)       - live dashboard JSON snapshot', flush=True)
    print('  df03: Live (r/n)        - actual gallons + flow only', flush=True)
    print('  defe: Command (write)   - JSON command channel for iPad app', flush=True)
    print('  def8: History (read)    - last 5 fills', flush=True)
    print('  def9: BatchMix (write)  - JSON batch mix data from iPad', flush=True)
    print('  defa: Trailer (r/w)     - fleet mode trailer selection/current trailer', flush=True)
    print('  defb: ConfigCmd (write) - JSON commands for sensor/calibration CRUD', flush=True)
    print('  defc: ConfigData (read) - response from last config command', flush=True)
    print('  deff: ConfigNotify(r/n) - last config response with request_id echo', flush=True)
    print('  df00: MaintCtl (write) - admin maintenance control/update frames', flush=True)
    print('  df01: MaintIn (write)  - admin maintenance shell stdin', flush=True)
    print('  df02: MaintOut (r/n)   - admin maintenance shell stdout/status', flush=True)

    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
