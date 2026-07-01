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
# Link-quality gate for joining a known station network (nmcli SIGNAL, 0-100).
# Only LEAVE the AP to join a known network when its signal clears STA_JOIN; once
# on STA, STAY until it falls below the lower STA_DROP (or disappears). The gap
# between the two is hysteresis — it stops the box flapping AP<->STA at the edge
# of hangar-WiFi range. Set STA_JOIN very high (e.g. 101) to effectively pin AP.
STA_JOIN_SIGNAL = int(os.environ.get("ROTORLINK_STA_JOIN_SIGNAL", "55"))
STA_DROP_SIGNAL = int(os.environ.get("ROTORLINK_STA_DROP_SIGNAL", "40"))
# A mode switch must be WANTED for this many consecutive evals before we act, so a
# single transient nmcli read (mid-transition state, a one-off stale scan) can't
# flap a working link — e.g. tear a healthy hangar STA down to an AP on one blip.
SWITCH_DEBOUNCE = max(1, int(os.environ.get("ROTORLINK_SWITCH_DEBOUNCE", "2")))


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
        self._pending = None     # the mode we're accumulating debounce for
        self._pending_count = 0  # consecutive evals that wanted self._pending

    # --- state queries ----------------------------------------------------
    def _saved_sta_conns(self) -> dict:
        """Map {ssid: connection-name} for saved NM wifi connections that aren't
        our AP — the 'known networks' we prefer to join as a station (e.g. the
        hangar 'Headings'). We keep the connection NAME because that is what
        `nmcli con up` needs (it can differ from the SSID)."""
        rc, out = _run(["nmcli", "-t", "-f", "NAME,TYPE", "con", "show"])
        names = [
            line.split(":", 1)[0]
            for line in out.splitlines()
            if line.endswith(":802-11-wireless") and not line.startswith(AP_CON_NAME + ":")
        ]
        conns = {}
        for name in names:
            rc, ssid = _run(["nmcli", "-g", "802-11-wireless.ssid", "con", "show", name])
            # Unescape the same way _best_known does, so an SSID with special chars
            # compares equal on both sides of the `ssid in conns` match.
            ssid = ssid.replace("\\:", ":").replace("\\\\", "\\").strip()
            if ssid:
                conns[ssid] = name
        return conns

    def _best_known(self) -> tuple:
        """(signal, ssid, connection-name) of the strongest currently-visible
        SAVED network, or (-1, None, None) if none is visible. Gates joining a
        station network on link quality (so the box won't leave its AP for a
        marginal hangar signal) AND names the exact connection to bring up."""
        conns = self._saved_sta_conns()
        if not conns:
            return (-1, None, None)
        rc, out = _run(["nmcli", "-t", "-f", "SSID,SIGNAL", "device", "wifi", "list", "--rescan", "no"])
        best_sig, best_ssid = -1, None
        for line in out.splitlines():
            if ":" not in line:
                continue
            ssid, sig = line.rsplit(":", 1)          # SIGNAL is the numeric last field
            ssid = ssid.replace("\\:", ":").replace("\\\\", "\\").strip()
            sig = sig.strip()
            if ssid in conns and sig.isdigit() and int(sig) > best_sig:
                best_sig, best_ssid = int(sig), ssid
        return (best_sig, best_ssid, conns.get(best_ssid))

    def _client_count(self) -> int:
        """Connected RotorLink clients (established TCP on the WS port). Pi-level
        check — no coupling to the server module."""
        rc, out = _run(["ss", "-tn", "state", "established"])
        return sum(1 for line in out.splitlines() if (":%d " % WS_PORT) in line or (":%d\t" % WS_PORT) in line)

    def _current_mode(self) -> str:
        """Classify wlan0: "ap" | "sta" | "unknown". Parse the fields properly —
        the old `"connected" in out.lower()` test also matched "disconnected", so a
        down interface read as "sta"."""
        rc, out = _run(["nmcli", "-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION", "device", "show", AP_IFACE])
        state_code, conn = "", ""
        for line in out.splitlines():
            if line.startswith("GENERAL.STATE:"):
                rest = line.split(":", 1)[1].strip()      # e.g. "100 (connected)"
                state_code = rest.split()[0] if rest else ""
            elif line.startswith("GENERAL.CONNECTION:"):
                conn = line.split(":", 1)[1].strip()       # active connection, or "" / "--"
        if conn == AP_CON_NAME:
            return "ap"
        # NM state 100 == fully connected; anything else (disconnected/unavailable/
        # connecting/deactivating) is not a settled STA link.
        if state_code == "100" and conn and conn != "--":
            return "sta"
        return "unknown"

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
    def _activate(self, target: str, sta_conn: str = None) -> None:
        if not AP_ENABLED:
            logger.info("[dry-run] would switch -> %s (ROTORLINK_AP_ENABLED off)", target)
            return
        if target == "ap":
            if not self.ensure_ap_profile():
                logger.error("cannot switch -> ap: AP profile unavailable (check PSK); will retry")
                return
            # Single radio (wlan0): the chip can't run AP + STA except on ONE
            # shared channel, so a lingering STA association makes `con up` of the
            # AP silently fail. Free the interface FIRST, then raise the AP.
            _run(["nmcli", "device", "disconnect", AP_IFACE])
            rc, out = _run(["nmcli", "con", "up", AP_CON_NAME])
            if rc != 0:
                logger.error("AP bring-up failed rc=%s: %s — will retry next loop",
                             rc, out.strip()[:200])
                return  # leave self._mode unchanged so the next eval retries
        else:
            # Return to a known station network. Take the AP down, then bring up
            # the CHOSEN connection BY NAME on wlan0. `nmcli device connect wlan0`
            # is unreliable coming out of AP mode (it re-activates the AP profile
            # instead of joining the STA network), so name the connection.
            _run(["nmcli", "con", "down", AP_CON_NAME])
            if not sta_conn:
                logger.error("cannot switch -> sta: no known connection resolved; will retry")
                return  # leave self._mode unchanged so the next eval retries
            rc, out = _run(["nmcli", "con", "up", sta_conn, "ifname", AP_IFACE])
            if rc != 0:
                logger.error("STA bring-up (%s) failed rc=%s: %s — will retry next loop",
                             sta_conn, rc, out.strip()[:200])
                return  # leave self._mode unchanged so the next eval retries
        self._mode = target
        logger.info("switched -> %s%s", target, (" (%s)" % sta_conn) if target == "sta" else "")

    # --- main loop --------------------------------------------------------
    async def run(self) -> None:
        logger.info(
            "network manager: AP_ENABLED=%s iface=%s ap_ssid=%s sta_join>=%s sta_drop<%s "
            "(default=AP; join known WiFi only above the signal gate, idle-gated)",
            AP_ENABLED, AP_IFACE, AP_SSID, STA_JOIN_SIGNAL, STA_DROP_SIGNAL,
        )
        self.ensure_ap_profile()  # safe: autoconnect off
        loop = asyncio.get_running_loop()
        while True:
            try:
                # Run the blocking nmcli/ss probes off the event loop.
                current = await loop.run_in_executor(None, self._current_mode)
                best_sig, best_ssid, best_conn = await loop.run_in_executor(None, self._best_known)
                clients = await loop.run_in_executor(None, self._client_count)

                # STA-preferred, but signal-gated with hysteresis: leave the AP to
                # join a known network only when its signal clears STA_JOIN; once on
                # STA, stay until it drops below STA_DROP (or vanishes -> best=-1).
                threshold = STA_DROP_SIGNAL if current == "sta" else STA_JOIN_SIGNAL
                desired = "sta" if best_sig >= threshold else "ap"

                # a down/disconnected wlan0 reports current=="unknown"; we converge
                # from "unknown" toward desired — the "WiFi dropped, so host the AP"
                # case the old `current != "unknown"` guard wrongly refused. But we
                # DEBOUNCE: require the same switch to be wanted for SWITCH_DEBOUNCE
                # consecutive evals, so a single transient read can't flap a link.
                if desired != current:
                    self._pending_count = self._pending_count + 1 if self._pending == desired else 1
                    self._pending = desired
                    if clients > 0:
                        logger.info("want %s (best known %s signal=%s) but %d client(s) connected — deferring (idle-gate)",
                                    desired, best_ssid, best_sig, clients)
                    elif self._pending_count >= SWITCH_DEBOUNCE:
                        # pass the resolved STA connection name so the sta switch
                        # brings up the right network by name (see _activate).
                        await loop.run_in_executor(None, self._activate, desired, best_conn)
                        self._pending, self._pending_count = None, 0
                    else:
                        logger.info("want %s (current=%s best=%s(%s)) — debouncing %d/%d",
                                    desired, current, best_sig, best_ssid, self._pending_count, SWITCH_DEBOUNCE)
                else:
                    self._pending, self._pending_count = None, 0
                    logger.debug("mode ok: current=%s desired=%s best=%s(%s) clients=%d",
                                 current, desired, best_sig, best_ssid, clients)
            except Exception as e:
                logger.warning("network manager loop error: %s", e)
            await asyncio.sleep(EVAL_INTERVAL)
