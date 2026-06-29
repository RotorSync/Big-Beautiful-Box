"""
RotorLink configuration — device descriptor, capability manifest, and the
network/dashboard settings. Everything is overridable via environment variables
so the same module works on any trailer Pi without code edits.
"""

import os
import socket

from . import PROTOCOL_VERSION, __version__


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


# --- WebSocket server -------------------------------------------------------
WS_HOST = _env("ROTORLINK_WS_HOST", "0.0.0.0")
WS_PORT = _env_int("ROTORLINK_WS_PORT", 8765)

# --- Dashboard command socket (the existing :9999 line protocol) ------------
DASHBOARD_HOST = _env("ROTORLINK_DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = _env_int("ROTORLINK_DASHBOARD_PORT", 9999)
DASHBOARD_TIMEOUT = _env_float("ROTORLINK_DASHBOARD_TIMEOUT", 2.0)

# How often we poll the dashboard for state and broadcast changes to clients.
STATE_POLL_INTERVAL = _env_float("ROTORLINK_STATE_POLL_INTERVAL", 0.5)

# --- mDNS / discovery -------------------------------------------------------
MDNS_SERVICE_TYPE = "_rotorlink._tcp"
MDNS_ENABLED = _env("ROTORLINK_MDNS", "1") not in ("0", "false", "no")

# --- Config-system data files ----------------------------------------------
# These point at the SAME files the BLE server (rotorsync_bumble.py) reads and
# writes, so RotorLink and bumble stay consistent. The running bumble lives at
# /opt/rotorsync_bumble.py, so its SCRIPT_DIR is /opt and its mopeka data dir is
# /opt/mopeka; the history logs live under /home/pi. All overridable via env so
# the same module works on a box laid out differently.
MOPEKA_DIR = _env("ROTORLINK_MOPEKA_DIR", "/opt/mopeka")
SENSOR_CSV_PATH = _env(
    "ROTORLINK_SENSOR_CSV", os.path.join(MOPEKA_DIR, "mopeka-sensor-details.csv")
)
CALIBRATION_CSV_PATH = _env(
    "ROTORLINK_CALIBRATION_CSV",
    os.path.join(MOPEKA_DIR, "calibration-points-1070gal-tank.csv"),
)
CALIBRATION_PROFILE_DIR = _env(
    "ROTORLINK_CALIBRATION_DIR", os.path.join(MOPEKA_DIR, "calibrations")
)
MOPEKA_CONFIG_PATH = _env(
    "ROTORLINK_MOPEKA_CONFIG", os.path.join(MOPEKA_DIR, "mopeka_config.json")
)
MOPEKA_HISTORY_LOG_PATH = _env(
    "ROTORLINK_MOPEKA_HISTORY", "/home/pi/mopeka_history.csv"
)
FILL_HISTORY_LOG_PATH = _env("ROTORLINK_FILL_HISTORY", "/home/pi/fill_history.log")

# Same retention/window bounds bumble enforces on history queries.
HISTORY_RETENTION_SECONDS = _env_int(
    "ROTORLINK_HISTORY_RETENTION_SECONDS", 366 * 24 * 3600
)

# The trailer's assigned BLE name (e.g. "TrailerSync-TR7") — bumble persists it
# here when it advertises. We reuse it as the WiFi/mDNS name so BLE + WiFi share
# ONE device identity (same source the AP SSID uses in network_manager.py).
BLE_NAME_FILE = _env(
    "ROTORLINK_BLE_NAME_FILE", "/home/pi/rotorsync_gatt_advertising_ready.json"
)


def unconfigured_name() -> str:
    """WiFi/mDNS name for a box with no trailer assigned: clearly 'unconfigured'
    but still unique per box (so several unassigned boxes on one network don't
    collide on the mDNS instance name). Uses the short serial from the hostname,
    e.g. host 'trailersync-sn007' -> 'TrailerSync-Unconfigured-sn007'."""
    host = socket.gethostname()
    serial = host
    if serial.lower().startswith("trailersync-"):
        serial = serial[len("trailersync-"):]
    serial = serial.strip()
    return "TrailerSync-Unconfigured-%s" % serial if serial else "TrailerSync-Unconfigured"


def trailer_name() -> str:
    """The assigned trailer name (e.g. 'TrailerSync-TR7') for WiFi/mDNS, matching
    the BLE advertised name. Read from the file bumble persists; fall back to the
    mopeka display_name, then an explicit 'TrailerSync-Unconfigured-<serial>'
    marker (so an unassigned box reads as unconfigured rather than a bare serial,
    while staying unique)."""
    import json
    try:
        with open(BLE_NAME_FILE) as f:
            name = str(json.load(f).get("name") or "").strip()
        if name:
            return name
    except Exception:
        pass
    try:
        with open(MOPEKA_CONFIG_PATH) as f:
            dn = str(json.load(f).get("display_name") or "").strip()
        if dn:
            return dn
    except Exception:
        pass
    return unconfigured_name()


def device_descriptor() -> dict:
    """
    Identity this Pi advertises in the `hello` message and mDNS TXT record.

    `app`/`name`/`serial`/`sw`/`proto`/`hw` — the app keys off `app` to pick the
    right UI and off `sw`/capability versions to gate features. `serial` falls
    back to the hostname so every box is identifiable even before one is set.
    """
    hostname = socket.gethostname()
    return {
        "app": _env("ROTORLINK_APP", "trailersync"),
        # WiFi/mDNS name == the assigned trailer (BLE name), not the box serial,
        # so BLE + WiFi share one identity. serial stays the hostname (the unique
        # box id + the resolvable .local host).
        "name": os.environ.get("ROTORLINK_NAME") or trailer_name(),
        "serial": _env("ROTORLINK_SERIAL", hostname),
        "sw": __version__,
        "proto": PROTOCOL_VERSION,
        "hw": _env("ROTORLINK_HW", "pi"),
    }


def capability_manifest() -> list:
    """
    What this device can do, by stable capability id + version. The app renders
    a capability's UI only if it is advertised, and gates behaviour by `v`.

    Additive-only within a version: add fields/caps freely, never rename/retype
    in place (a rename silently decodes to nil on the app). Bump `v` to break.
    """
    return [
        {
            # Live fill state pushed to every client on change (broadcast).
            "id": "trailer.fill.state",
            "v": 1,
            "push": True,
        },
        {
            # Commands the app may send (forwarded verbatim to the dashboard).
            # `read` commands are side-effect-free; `control` commands act on
            # equipment and are subject to control arbitration (one controller).
            "id": "trailer.fill.control",
            "v": 1,
            "push": False,
            "read_commands": ["STATE_JSON", "STATUS", "HISTORY"],
            "control_commands": [
                "SET_REQUESTED_GALLONS",
                "BATCHMIX",
                "START",
                "STOP",
                "RESET",
                "MODE",
            ],
        },
    ]
