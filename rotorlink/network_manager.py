"""
RotorLink network manager — the field/hangar AP↔STA state machine.

Topology (locked):
  * Default = the Pi hosts its OWN WiFi access point (NetworkManager AP +
    `ipv4.method shared` so it hands out DHCP/NAT). This is field mode: the iPad
    joins the Pi's AP directly, no router needed.
  * When a KNOWN network (e.g. the hangar "Headings") is in range AND the Pi is
    idle, drop the AP and join as a station (STA) instead — so the iPad reaches
    the Pi over the LAN and the Pi gets internet/NTP. mDNS advertises either way.
  * Switch gating (critical): only flip modes when NO RotorLink client is
    connected and no operation is active — never drop a connected iPad mid-op.

SAFETY: switching modes on the single wlan0 interface is disruptive (activating
the AP drops any STA connection, and vice versa). So this manager is DISABLED by
default (ROTORLINK_AP_ENABLED unset/0) — it only *logs* the decisions it would
make (dry-run). Set ROTORLINK_AP_ENABLED=1 to actually switch, which should only
be done on a box where dropping the current WiFi is acceptable.
"""

import asyncio
import logging
import os
import socket
import subprocess

logger = logging.getLogger("rotorlink.network")

# --- config (env-overridable; kept out of config.py to avoid file contention) -
AP_ENABLED = os.environ.get("ROTORLINK_AP_ENABLED", "0") in ("1", "true", "yes")
AP_IFACE = os.environ.get("ROTORLINK_AP_IFACE", "wlan0")
AP_CON_NAME = os.environ.get("ROTORLINK_AP_CON", "rotorlink-ap")


# The file bumble persists with the live BLE advertised name (e.g. TrailerSync-TR7).
BLE_NAME_FILE = os.environ.get(
    "ROTORLINK_BLE_NAME_FILE", "/home/pi/rotorsync_gatt_advertising_ready.json"
)


def _ble_advertised_name() -> str:
    """The trailer's BLE advertised name (e.g. 'TrailerSync-TR7'), REUSED as the
    AP SSID so the iPad keeps ONE device identity for BLE + WiFi (its existing
    device list). Read from the file bumble persists; fall back to the config
    display_name, then the hostname."""
    import json
    try:
        with open(BLE_NAME_FILE) as f:
            name = str(json.load(f).get("name") or "").strip()
        if name:
            return name
    except Exception:
        pass
    try:
        from . import config
        with open(config.MOPEKA_CONFIG_PATH) as f:
            dn = str(json.load(f).get("display_name") or "").strip()
        if dn:
            return dn
    except Exception:
        pass
    return socket.gethostname()


# AP SSID == the trailer's BLE name (one identity for BLE + WiFi); SSIDs cap at 32.
AP_SSID = (os.environ.get("ROTORLINK_AP_SSID") or _ble_advertised_name())[:32]
AP_BAND = os.environ.get("ROTORLINK_AP_BAND", "bg")  # 2.4GHz for range
# WPA2 PSK: env, or a file the deploy drops in. Must be 8..63 chars to be used.
AP_PSK = os.environ.get("ROTORLINK_AP_PSK", "")
AP_PSK_FILE = os.environ.get("ROTORLINK_AP_PSK_FILE", "/etc/rotorlink/ap.psk")
# How often to re-evaluate desired mode.
EVAL_INTERVAL = float(os.environ.get("ROTORLINK_AP_EVAL_INTERVAL", "20"))
# The RotorLink WS port — used to count connected clients (idle gate) without
# coupling to the server module.
WS_PORT = int(os.environ.get("ROTORLINK_WS_PORT", "8765"))


def _run(args, timeout=15):
    """Run a command, return (rc, stdout). Never raises."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 1, str(e)


def _ap_psk() -> str:
    if AP_PSK:
        return AP_PSK
    try:
        with open(AP_PSK_FILE) as f:
            return f.read().strip()
    except Exception:
        return ""


class NetworkManager:
    def __init__(self) -> None:
        self._mode = "unknown"   # "ap" | "sta" | "unknown"

    # --- state queries ----------------------------------------------------
    def _saved_sta_ssids(self) -> set:
        """SSIDs of saved NM wifi connections that aren't our AP — the 'known
        networks' we prefer to join as a station (e.g. the hangar 'Headings')."""
        rc, out = _run(["nmcli", "-t", "-f", "NAME,TYPE", "con", "show"])
        names = [
            line.split(":", 1)[0]
            for line in out.splitlines()
            if line.endswith(":802-11-wireless") and not line.startswith(AP_CON_NAME + ":")
        ]
        ssids = set()
        for name in names:
            rc, ssid = _run(["nmcli", "-g", "802-11-wireless.ssid", "con", "show", name])
            ssid = ssid.strip()
            if ssid:
                ssids.add(ssid)
        return ssids

    def _visible_ssids(self) -> set:
        rc, out = _run(["nmcli", "-t", "-f", "SSID", "device", "wifi", "list", "--rescan", "no"])
        return {s.strip() for s in out.splitlines() if s.strip()}

    def _known_network_available(self) -> bool:
        return bool(self._saved_sta_ssids() & self._visible_ssids())

    def _client_count(self) -> int:
        """Connected RotorLink clients (established TCP on the WS port). Pi-level
        check — no coupling to the server module."""
        rc, out = _run(["ss", "-tn", "state", "established"])
        return sum(1 for line in out.splitlines() if (":%d " % WS_PORT) in line or (":%d\t" % WS_PORT) in line)

    def _current_mode(self) -> str:
        rc, out = _run(["nmcli", "-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION", "device", "show", AP_IFACE])
        return "ap" if AP_CON_NAME in out else ("sta" if "connected" in out.lower() else "unknown")

    # --- AP profile -------------------------------------------------------
    def ensure_ap_profile(self) -> bool:
        """Create the AP NetworkManager connection if absent (autoconnect off, so
        creating it changes nothing live). Returns True if present/created."""
        rc, out = _run(["nmcli", "-t", "-f", "NAME", "con", "show"])
        if any(line == AP_CON_NAME for line in out.splitlines()):
            return True
        psk = _ap_psk()
        if not (8 <= len(psk) <= 63):
            logger.warning("AP profile not created: WPA2 PSK missing/invalid "
                           "(set ROTORLINK_AP_PSK or %s, 8..63 chars)", AP_PSK_FILE)
            return False
        rc, out = _run([
            "nmcli", "con", "add", "type", "wifi", "ifname", AP_IFACE,
            "con-name", AP_CON_NAME, "autoconnect", "no", "ssid", AP_SSID,
            "802-11-wireless.mode", "ap", "802-11-wireless.band", AP_BAND,
            "ipv4.method", "shared",
            "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk,
        ])
        if rc == 0:
            logger.info("created AP profile '%s' ssid=%s (autoconnect off)", AP_CON_NAME, AP_SSID)
            return True
        logger.error("failed to create AP profile: %s", out.strip()[:200])
        return False

    # --- switching --------------------------------------------------------
    def _activate(self, target: str) -> None:
        if not AP_ENABLED:
            logger.info("[dry-run] would switch -> %s (ROTORLINK_AP_ENABLED off)", target)
            return
        if target == "ap":
            self.ensure_ap_profile()
            _run(["nmcli", "con", "up", AP_CON_NAME])
        else:
            # Let NM auto-pick the best known STA connection.
            _run(["nmcli", "con", "down", AP_CON_NAME])
            _run(["nmcli", "device", "connect", AP_IFACE])
        self._mode = target
        logger.info("switched -> %s", target)

    # --- main loop --------------------------------------------------------
    async def run(self) -> None:
        logger.info(
            "network manager: AP_ENABLED=%s iface=%s ap_ssid=%s (default=AP, "
            "STA when a known network is in range + idle)",
            AP_ENABLED, AP_IFACE, AP_SSID,
        )
        self.ensure_ap_profile()  # safe: autoconnect off
        loop = asyncio.get_running_loop()
        while True:
            try:
                # Run the blocking nmcli/ss probes off the event loop.
                desired = "sta" if await loop.run_in_executor(None, self._known_network_available) else "ap"
                current = await loop.run_in_executor(None, self._current_mode)
                clients = await loop.run_in_executor(None, self._client_count)

                if desired != current and current != "unknown":
                    if clients > 0:
                        logger.info("want %s but %d client(s) connected — deferring (idle-gate)", desired, clients)
                    else:
                        await loop.run_in_executor(None, self._activate, desired)
                else:
                    logger.debug("mode ok: current=%s desired=%s clients=%d", current, desired, clients)
            except Exception as e:
                logger.warning("network manager loop error: %s", e)
            await asyncio.sleep(EVAL_INTERVAL)
