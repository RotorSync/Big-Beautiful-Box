"""
RotorLink WebSocket server.

Responsibilities:
  * accept many clients; greet each with `hello` (descriptor + manifest);
  * forward `command` frames to the dashboard's :9999 socket and reply;
  * poll the dashboard and broadcast `state` (and `history`) on change.

Control arbitration ("many viewers, one controller") is implemented but OFF by
default (ROTORLINK_ARBITRATION=1 to enable) so P1 is a clean transport; the
iOS multi-client work in P2 turns it on. Read-only commands are always allowed;
an emergency STOP is always allowed from any client even under arbitration.
"""

import asyncio
import json
import logging
import os
from typing import Dict, Optional, Set

import websockets

from . import command_translator, config, protocol, state_encoder
from .dashboard_client import DashboardClient

logger = logging.getLogger("rotorlink.server")

# How many state-poll cycles between history polls (history only changes on a
# fill, so we poll it far less often), mirroring the BLE server.
HISTORY_POLL_CYCLES = 20

ARBITRATION = os.environ.get("ROTORLINK_ARBITRATION", "0") in ("1", "true", "yes")

# Commands that act on equipment (gated by arbitration) vs. side-effect-free.
_CONTROL_CAP = next(
    c for c in config.capability_manifest() if c["id"] == "trailer.fill.control"
)
READ_COMMANDS: Set[str] = set(_CONTROL_CAP.get("read_commands", []))
# Dashboard verbs always allowed even under arbitration: STOP (raw) and PS (the
# pump-stop line the app's {"cmd":"pump_stop"} translates to).
EMERGENCY_COMMANDS: Set[str] = {"STOP", "PS"}


def _command_verb(command: str) -> str:
    """`SET_REQUESTED_GALLONS:12.5` -> `SET_REQUESTED_GALLONS`."""
    return command.split(":", 1)[0].strip().upper()


class ClientState:
    """Per-connection bookkeeping."""

    def __init__(self, websocket) -> None:
        self.ws = websocket
        # None = role not yet declared (eligible for single-client auto-control);
        # an explicit "viewer" is never auto-promoted to controller.
        self.role: Optional[str] = None
        self.user: Optional[str] = None
        self.device: Optional[dict] = None
        self.hello_received = False

    @property
    def peer(self) -> str:
        try:
            return "%s:%s" % self.ws.remote_address[:2]
        except Exception:
            return "?"


class RotorLinkServer:
    def __init__(self) -> None:
        self.dashboard = DashboardClient()
        self.clients: Dict[object, ClientState] = {}
        self._controller: Optional[ClientState] = None
        self._last_state: Optional[dict] = None
        self._last_state_json: Optional[str] = None
        self._last_history: Optional[str] = None
        self._last_bms: Optional[dict] = None
        self._last_mopeka: Dict[int, Optional[dict]] = {1: None, 2: None}

    # --- lifecycle ---------------------------------------------------------
    async def run(self) -> None:
        logger.info(
            "RotorLink %s starting: ws://%s:%s -> dashboard %s:%s",
            config.device_descriptor()["sw"],
            config.WS_HOST,
            config.WS_PORT,
            config.DASHBOARD_HOST,
            config.DASHBOARD_PORT,
        )
        broadcaster = asyncio.create_task(self._state_loop())
        broadcaster.add_done_callback(self._on_broadcaster_done)
        try:
            # ping keepalive so a hard client drop surfaces as ConnectionClosed
            # (triggering cleanup) instead of lingering as a ghost client.
            async with websockets.serve(
                self._handle,
                config.WS_HOST,
                config.WS_PORT,
                ping_interval=20,
                ping_timeout=20,
            ):
                await asyncio.Future()  # run forever
        finally:
            broadcaster.cancel()

    def _on_broadcaster_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("state broadcaster died unexpectedly: %r", exc)

    # --- per-connection ----------------------------------------------------
    async def _handle(self, websocket, path=None) -> None:
        # `path` is passed by websockets <11 and omitted by >=11 — accept both.
        state = ClientState(websocket)
        self.clients[websocket] = state
        logger.info("client connected: %s (%d total)", state.peer, len(self.clients))
        try:
            await self._send(state, protocol.build_hello())
            # Send the current snapshot immediately so a new client isn't blank
            # until the next change.
            if self._last_state is not None:
                await self._send(state, protocol.build_state(self._last_state))
            if self._last_bms is not None:
                await self._send(state, protocol.build_bms(self._last_bms))
            for index in (1, 2):
                if self._last_mopeka[index] is not None:
                    await self._send(state, protocol.build_mopeka(index, self._last_mopeka[index]))
            async for raw in websocket:
                await self._on_message(state, raw)
        except websockets.ConnectionClosed:
            pass
        except Exception as e:
            logger.warning("client %s error: %s", state.peer, e)
        finally:
            self.clients.pop(websocket, None)
            if self._controller is state:
                self._controller = None
            logger.info(
                "client disconnected: %s (%d total)", state.peer, len(self.clients)
            )

    async def _on_message(self, state: ClientState, raw: str) -> None:
        message = protocol.decode(raw)
        if message is None:
            return
        mtype = message["type"]

        if mtype == "client_hello":
            state.hello_received = True
            state.role = str(message.get("role", "viewer"))  # explicit from now on
            state.user = message.get("user")
            state.device = message.get("device")
            if ARBITRATION and state.role == "controller" and self._controller is None:
                self._controller = state
            logger.info(
                "client_hello from %s role=%s user=%s", state.peer, state.role, state.user
            )
        elif mtype == "command":
            await self._handle_command(state, message)
        elif mtype == "ping":
            await self._send(state, {"type": "pong"})
        else:
            # Forward-compat: ignore unknown types, keep the connection.
            logger.debug("ignoring unknown message type %r from %s", mtype, state.peer)

    async def _handle_command(self, state: ClientState, message: dict) -> None:
        cmd_id = message.get("id")

        # Preferred: an app `{"cmd": ...}` dict — the SAME vocabulary the app
        # sends over BLE — translated to a dashboard line. Fallback: a raw
        # dashboard line in `command` (read commands like STATE_JSON, or debug).
        if message.get("cmd"):
            # The app reuses its BLE {"cmd":"client_hello",...} over WiFi (rich
            # identity: role/name/user_id). Record it as a client_hello rather
            # than trying to translate it to a dashboard line.
            if str(message.get("cmd", "")).strip().lower() in ("client_hello", "hello"):
                state.role = str(message.get("role", state.role or "viewer"))
                state.user = message.get("name") or message.get("user") or state.user
                state.device = message.get("device") or state.device
                logger.info(
                    "client_hello (cmd) from %s role=%s user=%s", state.peer, state.role, state.user
                )
                await self._send(state, protocol.build_command_result(cmd_id, True, "hello"))
                return
            command = command_translator.translate(message)
            if command is None:
                await self._send(
                    state,
                    protocol.build_command_result(cmd_id, False, "unknown or invalid cmd"),
                )
                logger.info("ignored unknown cmd %r from %s", message.get("cmd"), state.peer)
                return
        else:
            command = message.get("command")
            if not isinstance(command, str) or not command:
                await self._send(
                    state,
                    protocol.build_command_result(cmd_id, False, "missing command"),
                )
                return

        verb = _command_verb(command)
        if not self._authorize(state, verb):
            await self._send(
                state,
                protocol.build_command_result(cmd_id, False, "not the controller"),
            )
            logger.info("denied control command %s from %s (not controller)", verb, state.peer)
            return

        response = await self.dashboard.send_command(command)
        ok = response is not None
        await self._send(state, protocol.build_command_result(cmd_id, ok, response))

    def _authorize(self, state: ClientState, verb: str) -> bool:
        """Read commands and emergencies are always allowed; control commands
        require being the controller when arbitration is on."""
        if not ARBITRATION:
            return True
        if verb in READ_COMMANDS or verb in EMERGENCY_COMMANDS:
            return True
        # First commander takes control — but never silently promote a client
        # that explicitly declared itself a viewer.
        if self._controller is None and state.role != "viewer":
            self._controller = state
        return self._controller is state

    # --- state broadcasting ------------------------------------------------
    async def _state_loop(self) -> None:
        cycles = 0
        while True:
            try:
                state = await self.dashboard.query_state()
                if state is not None:
                    state_json = json.dumps(state, sort_keys=True, separators=(",", ":"))
                    if state_json != self._last_state_json:
                        self._last_state_json = state_json
                        # Emit the compact BLE-format payload so the app decodes
                        # WiFi state with its existing RaspberryPiLiveState model.
                        compact = state_encoder.encode_ble_state(state, len(self.clients))
                        self._last_state = compact
                        await self._broadcast(protocol.build_state(compact))
                        # Battery + tank sensors ride the same dashboard snapshot;
                        # broadcast each in its BLE-characteristic shape on change.
                        await self._broadcast_sensors(state)

                cycles += 1
                if cycles >= HISTORY_POLL_CYCLES:
                    cycles = 0
                    response = await self.dashboard.send_command("HISTORY")
                    if response and response.startswith("HIST:"):
                        history = response[5:]
                        if history != self._last_history:
                            self._last_history = history
                            await self._broadcast(protocol.build_history(history))
            except Exception as e:
                logger.warning("state loop error: %s", e)
            await asyncio.sleep(config.STATE_POLL_INTERVAL)

    async def _broadcast_sensors(self, state: dict) -> None:
        """Broadcast BMS + per-tank Mopeka payloads (BLE-characteristic shape)
        when they change, derived from the same dashboard snapshot as state."""
        bms = state_encoder.encode_bms(state)
        if bms != self._last_bms:
            self._last_bms = bms
            if bms is not None:
                await self._broadcast(protocol.build_bms(bms))
        for index in (1, 2):
            mopeka = state_encoder.encode_mopeka(state, index)
            if mopeka != self._last_mopeka[index]:
                self._last_mopeka[index] = mopeka
                if mopeka is not None:
                    await self._broadcast(protocol.build_mopeka(index, mopeka))

    async def _broadcast(self, message: dict) -> None:
        if not self.clients:
            return
        payload = protocol.encode(message)
        await asyncio.gather(
            *(self._send_raw(c, payload) for c in list(self.clients.values())),
            return_exceptions=True,
        )

    # --- send helpers ------------------------------------------------------
    async def _send(self, state: ClientState, message: dict) -> None:
        await self._send_raw(state, protocol.encode(message))

    async def _send_raw(self, state: ClientState, payload: str) -> None:
        try:
            await state.ws.send(payload)
        except Exception:
            # Drop happens in the connection's own finally; ignore here.
            pass
