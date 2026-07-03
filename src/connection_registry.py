"""Connection registry shared by the BLE server (rotorsync_bumble) and the
WiFi server (rotorlink).

Each server reports its clients' connect/hello/disconnect events here; the
registry appends them to one JSONL log and keeps a per-server live-snapshot
file, so "who is connected right now" and "who connected when, over which
transport" can be served over either link (GET_CONNECTIONS /
GET_CONNECTION_LOG) and relayed to the backend by the app.

Transports: ble | wifi_lan | wifi_ap. AP clients are identified by peer IP in
the NetworkManager shared subnet (10.42.0.0/16) the Pi's AP hands out.

Two processes append concurrently: every event is a single O_APPEND write of
one JSON line, which Linux keeps atomic at these sizes.
"""
import json
import os
import subprocess
import time
import uuid

LOG_PATH = '/home/pi/rotorsync_connection_log.jsonl'
SNAPSHOT_PATHS = {
    'ble': '/home/pi/rotorsync_connections_ble.json',
    'wifi': '/home/pi/rotorsync_connections_wifi.json',
}
LOG_MAX_BYTES = 512 * 1024
LOG_KEEP_LINES = 2000
VERSION_PATH = '/home/pi/Big-Beautiful-Box/VERSION'
ADVERTISING_READY_PATH = '/home/pi/rotorsync_gatt_advertising_ready.json'
HEALTH_SERVICES = ('rotorsync.service', 'rotorlink.service', 'iol_dashboard.service')


def classify_wifi_peer(ip):
    """wifi_ap when the peer got its address from the Pi's own AP subnet."""
    return 'wifi_ap' if str(ip or '').startswith('10.42.') else 'wifi_lan'


def _clean(value, limit=120):
    if value is None:
        return None
    text = str(value).replace('\n', ' ').replace('\r', ' ').strip()
    return text[:limit] or None


def record_event(event, transport, *, peer=None, role=None, name=None,
                 user_id=None, device=None, log_path=LOG_PATH):
    """Append one connection event; returns the event dict (or None on error)."""
    entry = {
        'id': uuid.uuid4().hex,
        'ts': round(time.time(), 3),
        'event': str(event),
        'transport': str(transport),
        'peer': _clean(peer, 64),
        'role': _clean(role, 40),
        'name': _clean(name, 80),
        'user_id': _clean(user_id, 64),
        'device': _clean(device, 80),
    }
    try:
        line = json.dumps({k: v for k, v in entry.items() if v is not None},
                          separators=(',', ':')) + '\n'
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode('utf-8'))
        finally:
            os.close(fd)
        _maybe_prune(log_path)
        return entry
    except Exception:
        return None


def _maybe_prune(log_path):
    try:
        if os.path.getsize(log_path) <= LOG_MAX_BYTES:
            return
        with open(log_path) as f:
            lines = f.readlines()
        kept = lines[-LOG_KEEP_LINES:]
        tmp = log_path + '.tmp'
        with open(tmp, 'w') as f:
            f.writelines(kept)
        os.replace(tmp, log_path)
    except Exception:
        pass


def write_snapshot(server, clients, snapshot_path=None):
    """Persist a server's live client list ('ble' or 'wifi').

    clients: list of dicts with transport/peer/role/name/user_id/device/
    connected_at/hello_at (missing keys fine)."""
    path = snapshot_path or SNAPSHOT_PATHS.get(server)
    if not path:
        return
    try:
        payload = {'server': server, 'updated': round(time.time(), 3),
                   'clients': clients}
        tmp = path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(payload, f, separators=(',', ':'))
        os.replace(tmp, path)
    except Exception:
        pass


def read_connections(snapshot_paths=None, stale_after=180.0):
    """Merged live view across both servers. A snapshot older than
    stale_after seconds is reported with stale=True (its server may be down —
    its clients are likely gone)."""
    now = time.time()
    merged = []
    for server, path in (snapshot_paths or SNAPSHOT_PATHS).items():
        try:
            with open(path) as f:
                snap = json.load(f)
        except Exception:
            continue
        stale = (now - float(snap.get('updated') or 0)) > stale_after
        for client in snap.get('clients') or []:
            item = dict(client)
            item['server'] = server
            if stale:
                item['stale'] = True
            merged.append(item)
    return merged


def read_log_since(since_ts, limit=500, log_path=LOG_PATH):
    """Events with ts > since_ts, oldest first, capped at limit."""
    items = []
    try:
        with open(log_path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if float(entry.get('ts') or 0) > since_ts:
                    items.append(entry)
    except FileNotFoundError:
        return []
    except Exception:
        return []
    items.sort(key=lambda e: e.get('ts') or 0)
    return items[:limit]


def _service_state(unit):
    try:
        out = subprocess.run(
            ['systemctl', 'is-active', unit],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


def box_health():
    """Compact health blob: version, uptime, service states, BLE advertising
    freshness, disk. Cheap enough to serve on demand."""
    health = {'ts': round(time.time(), 3)}
    try:
        with open(VERSION_PATH) as f:
            health['version'] = f.read().strip()[:20]
    except Exception:
        health['version'] = None
    try:
        with open('/proc/uptime') as f:
            health['uptime_s'] = int(float(f.read().split()[0]))
    except Exception:
        pass
    health['services'] = {u.replace('.service', ''): _service_state(u)
                          for u in HEALTH_SERVICES}
    try:
        with open(ADVERTISING_READY_PATH) as f:
            adv = json.load(f)
        health['ble_name'] = adv.get('name')
        health['ble_advertising_age_s'] = int(time.time() - float(adv.get('timestamp') or 0))
    except Exception:
        health['ble_name'] = None
    try:
        stat = os.statvfs('/')
        health['disk_free_mb'] = int(stat.f_bavail * stat.f_frsize / (1024 * 1024))
    except Exception:
        pass
    return health
