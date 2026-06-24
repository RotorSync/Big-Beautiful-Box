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
        "name": _env("ROTORLINK_NAME", hostname),
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
