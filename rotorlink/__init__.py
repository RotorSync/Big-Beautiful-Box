"""
RotorLink — local WiFi link between the RotorSync iPad app and this Pi.

A thin, standalone asyncio + websockets service that bridges the existing
dashboard command/state protocol (the line-based socket on 127.0.0.1:9999, the
same one the BLE server `rotorsync_bumble.py` already uses) onto a WebSocket so
the iPad can talk to the trailer over WiFi as well as Bluetooth.

It deliberately touches neither the dashboard nor the BLE server: it is just
another short-lived client of the dashboard's :9999 socket, run as its own
systemd service. See ROTORLINK_PLAN.md for the full design.
"""

__version__ = "0.1.0"
PROTOCOL_VERSION = 1
