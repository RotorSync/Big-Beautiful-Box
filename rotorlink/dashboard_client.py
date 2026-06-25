"""
Dashboard client — talks to the dashboard's line-based command socket on
127.0.0.1:9999, exactly the way `rotorsync_bumble.py` does: open a short-lived
TCP connection, send one `<command>\n`, read one response line, close.

Because each call is its own connection that the dashboard closes immediately
(see src/socket_handler.py), many clients (BLE server + RotorLink + others) can
use the socket concurrently without any change to the dashboard.

The socket calls are blocking; we run them in a thread executor so the asyncio
event loop is never stalled.
"""

import asyncio
import json
import logging
import socket
from typing import Optional

from . import config

logger = logging.getLogger("rotorlink.dashboard")


def _redact(cmd: str) -> str:
    """Hide secrets (WiFi password) from logs, mirroring the BLE server."""
    try:
        if cmd.startswith("WIFI_SET:"):
            data = json.loads(cmd.split(":", 1)[1])
            if isinstance(data, dict) and "password" in data:
                data["password"] = "***"
            return "WIFI_SET:" + json.dumps(data, separators=(",", ":"))
    except Exception:
        pass
    return cmd


def _send_blocking(cmd: str) -> Optional[str]:
    """Synchronous connect/send/recv/close against the dashboard socket.

    Reads until the dashboard closes the connection (it always does after one
    response — see src/socket_handler.py) so a STATE_JSON payload larger than a
    single recv buffer is never truncated into invalid JSON.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(config.DASHBOARD_TIMEOUT)
            s.connect((config.DASHBOARD_HOST, config.DASHBOARD_PORT))
            s.send(f"{cmd}\n".encode())
            chunks = []
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            response = b"".join(chunks).decode().strip()
            logger.debug("dashboard %s -> %s", _redact(cmd), response)
            return response
    except Exception as e:
        logger.warning("dashboard command failed (%s): %s", _redact(cmd), e)
        return None


class DashboardClient:
    """Async facade over the dashboard's :9999 command socket."""

    def __init__(self) -> None:
        # None = unknown; True/False once we learn whether the dashboard speaks
        # STATE_JSON, so we never pay the legacy STATUS round-trip on a modern
        # box (and don't keep probing STATE_JSON on a legacy one).
        self._state_json_supported: Optional[bool] = None

    async def send_command(self, cmd: str) -> Optional[str]:
        """Send a raw command line, return the raw response line (or None)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _send_blocking, cmd)

    async def query_state(self) -> Optional[dict]:
        """
        Ask the dashboard for its current state snapshot.

        Primary path is `STATE_JSON` -> `STATE_JSON:{...}`. Falls back to the
        legacy `STATUS` -> `REQ:..|ACT:..|MODE:..` line if the box predates
        STATE_JSON, so RotorLink works against older dashboards too.
        """
        if self._state_json_supported is not False:
            response = await self.send_command("STATE_JSON")
            if response and response.startswith("STATE_JSON:"):
                try:
                    state = json.loads(response.split(":", 1)[1])
                    self._state_json_supported = True
                    return state
                except Exception as e:
                    logger.warning("STATE_JSON parse error: %s", e)
                    return None  # don't mask a parse error with stale STATUS data
            # A non-None reply that isn't STATE_JSON means this box is legacy.
            if response is not None and self._state_json_supported is None:
                self._state_json_supported = False

        response = await self.send_command("STATUS")
        if response and response.startswith("REQ:"):
            state: dict = {}
            for part in response.split("|"):
                if part.startswith("REQ:"):
                    state["requested_gal"] = _safe_float(part[4:])
                elif part.startswith("ACT:"):
                    state["actual_gal"] = _safe_float(part[4:])
                elif part.startswith("MODE:"):
                    state["mode"] = part[5:]
            return state or None
        return None

    async def is_ready(self) -> bool:
        """True once the dashboard answers a state query."""
        return await self.query_state() is not None


def _safe_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
