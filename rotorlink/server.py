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
import time
from typing import Dict, Optional, Set

import websockets

from . import command_translator, config, protocol, state_encoder
from .config_handler import ConfigHandler, _current_trailer_info
from .dashboard_client import DashboardClient
from .maintenance_handler import MaintenanceHandler, log_maintenance_secret_status
from src import connection_registry

logger = logging.getLogger("rotorlink.server")

# How many state-poll cycles between history polls (history only changes on a
# fill, so we poll it far less often), mirroring the BLE server.
HISTORY_POLL_CYCLES = 20

ARBITRATION = os.environ.get("ROTORLINK_ARBITRATION", "0") in ("1", "true", "yes")

# Cap on a single client's send inside a broadcast. A half-open peer (walked out
# of range, suspended app) stops ACKing; once its pipe fills, ws.send() awaits
# drain indefinitely — and the ping keepalive can't reap it, because the ping
# frame queues behind the same jammed pipe — freezing state updates for every
# healthy client. On timeout we abort that client's transport instead.
# Clamp to a sane floor: an env typo of 0 (or negative) would make wait_for
# time out immediately and abort every client on every broadcast.
BROADCAST_SEND_TIMEOUT = float(os.environ.get("ROTORLINK_BROADCAST_SEND_TIMEOUT", "5"))
if BROADCAST_SEND_TIMEOUT <= 0:
    logger.warning(
        "ROTORLINK_BROADCAST_SEND_TIMEOUT=%s is not a positive number; using 5s",
        os.environ.get("ROTORLINK_BROADCAST_SEND_TIMEOUT"),
    )
    BROADCAST_SEND_TIMEOUT = 5.0

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


def _sanitize_pilot_name(name) -> str:
    """Mirror rotorsync_bumble._sanitize_pilot_name: the name rides in a
    line-based dashboard command, so it must stay single-line and '|'-free."""
    return str(name or "").replace("\n", " ").replace("\r", " ").replace("|", "/").strip()[:80]


class ClientState:
    """Per-connection bookkeeping."""

    def __init__(self, websocket) -> None:
        self.ws = websocket
        # None = role not yet declared (eligible for single-client auto-control);
        # an explicit "viewer" is never auto-promoted to controller.
        self.role: Optional[str] = None
        self.user: Optional[str] = None
        self.device: Optional[dict] = None
        self.user_id: Optional[str] = None
        self.hello_received = False
        # When the latest client_hello arrived — the most recent pilot hello wins
        # pilot attribution, mirroring the BLE server's last_seen semantics.
        self.hello_at: float = 0.0
        self.connected_at: float = time.time()
        # wifi_lan | wifi_ap, classified from the peer IP at accept time.
        self.transport: str = connection_registry.classify_wifi_peer(self.peer_ip)
        # Last location update from this client: {lat, lon, acc, ts} or None.
        self.loc: Optional[dict] = None
        # One remote-maintenance PTY shell per connection, lazily created on the
        # first maintenance frame; torn down on disconnect. None until then.
        self.maintenance: Optional[MaintenanceHandler] = None

    @property
    def peer(self) -> str:
        try:
            return "%s:%s" % self.ws.remote_address[:2]
        except Exception:
            return "?"

    @property
    def peer_ip(self) -> str:
        try:
            return str(self.ws.remote_address[0])
        except Exception:
            return ""


class RotorLinkServer:
    def __init__(self) -> None:
        self.dashboard = DashboardClient()
        self.config_handler = ConfigHandler(self.dashboard)
        self.clients: Dict[object, ClientState] = {}
        self._controller: Optional[ClientState] = None
        self._last_state: Optional[dict] = None
        self._last_state_json: Optional[str] = None
        self._last_history: Optional[str] = None
        self._last_bms: Optional[dict] = None
        self._last_mopeka: Dict[int, Optional[dict]] = {1: None, 2: None}
        # Last broadcast trailer-config JSON, so we only re-emit `trailer_config`
        # when the trailer assignment / sensor offsets actually change.
        self._last_trailer_config_json: Optional[str] = None
        # Last pilot name pushed to the dashboard (None = none pushed / cleared),
        # so WIFI_PILOT_* lines are only sent on change, like the BLE server.
        self._last_pushed_pilot: Optional[str] = None

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
        # Surface where the maintenance relay secret is coming from (or warn if
        # only the dev default is available) at startup, like bumble does.
        log_maintenance_secret_status()
        broadcaster = asyncio.create_task(self._state_loop())
        broadcaster.add_done_callback(self._on_broadcaster_done)
        try:
            # ping keepalive so a hard client drop surfaces as ConnectionClosed
            # (triggering cleanup) instead of lingering as a ghost client.
            server = await websockets.serve(
                self._handle,
                config.WS_HOST,
                config.WS_PORT,
                ping_interval=20,
                ping_timeout=20,
            )
            try:
                await asyncio.Future()  # run forever
            finally:
                # Shut down FAST. The default teardown waits for every client to
                # complete the WebSocket close handshake, but after a network
                # switch (the AP<->STA flip) the client (iPad) is gone and that
                # wait blocks for tens of seconds — leaving systemd stuck in
                # "deactivating". Abort the TCP connections immediately so
                # wait_closed() returns at once; each connection's handler still
                # runs its finally on the forced close (maintenance shells are
                # torn down there). Bound the wait anyway as a backstop.
                server.close()
                for ws in list(self.clients):
                    try:
                        ws.transport.abort()
                    except Exception:
                        pass
                try:
                    await asyncio.wait_for(server.wait_closed(), timeout=2)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
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
        connection_registry.record_event("connect", state.transport, peer=state.peer)
        self._write_wifi_snapshot()
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
            # Current trailer config (read from the same Pi files bumble uses) so
            # a fresh client's config UI is populated immediately, not blank
            # until the next change.
            trailer = self._read_trailer_config()
            if trailer is not None:
                await self._send(state, protocol.build_trailer_config(trailer))
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
            # Terminate this connection's maintenance shell (if any) so a dropped
            # WiFi link never strands a live root shell on the Pi.
            if state.maintenance is not None:
                try:
                    await state.maintenance.shutdown()
                except Exception as e:  # noqa: BLE001
                    logger.warning("maintenance teardown error for %s: %s", state.peer, e)
                state.maintenance = None
            logger.info(
                "client disconnected: %s (%d total)", state.peer, len(self.clients)
            )
            connection_registry.record_event(
                "disconnect", state.transport, peer=state.peer, role=state.role,
                name=state.user, user_id=state.user_id, device=state.device,
            )
            self._write_wifi_snapshot()
            try:
                await self._push_pilot_status()
            except Exception as e:  # noqa: BLE001
                logger.warning("pilot status push failed on disconnect: %s", e)

    # --- connection registry -------------------------------------------------

    def _write_wifi_snapshot(self) -> None:
        clients = []
        for s in self.clients.values():
            entry = {
                "transport": s.transport,
                "peer": s.peer,
                "role": s.role,
                "name": s.user,
                "user_id": s.user_id,
                "device": s.device if isinstance(s.device, str) else None,
                "connected_at": round(s.connected_at, 3),
                "hello_at": round(s.hello_at, 3) if s.hello_at else None,
            }
            clients.append({k: v for k, v in entry.items() if v is not None})
        connection_registry.write_snapshot("wifi", clients)

    # --- location ------------------------------------------------------------

    @staticmethod
    def _parse_loc(value) -> Optional[dict]:
        """Accepts a nested hello `loc` dict or a flat loc_update message —
        anything carrying numeric `lat`/`lon` (and optional `acc`)."""
        if not isinstance(value, dict):
            return None
        try:
            lat = float(value.get("lat"))
            lon = float(value.get("lon"))
        except (TypeError, ValueError):
            return None
        loc = {"lat": round(lat, 6), "lon": round(lon, 6), "ts": round(time.time(), 3)}
        try:
            if value.get("acc") is not None:
                loc["acc"] = round(float(value["acc"]), 1)
        except (TypeError, ValueError):
            pass
        return loc

    async def _apply_loc(self, state: ClientState, value) -> None:
        loc = self._parse_loc(value)
        if loc is None:
            return
        state.loc = loc
        if str(state.role or "").strip().lower() == "pilot":
            parts = f"{loc['lat']},{loc['lon']}"
            if "acc" in loc:
                parts += f",{loc['acc']}"
            await self.dashboard.send_command(f"WIFI_PILOT_LOC:{parts}")

    # --- pilot attribution ---------------------------------------------------

    def _current_pilot_name(self) -> Optional[str]:
        """Name of the connected WiFi client whose role is 'pilot' (most recent
        hello wins), mirroring rotorsync_bumble._current_pilot_name."""
        best_name = None
        best_at = -1.0
        for state in self.clients.values():
            if str(state.role or "").strip().lower() != "pilot":
                continue
            name = _sanitize_pilot_name(state.user)
            if name and state.hello_at >= best_at:
                best_at = state.hello_at
                best_name = name
        return best_name

    async def _push_pilot_status(self) -> None:
        """Tell the dashboard which WiFi pilot is connected, on change only.

        Uses WIFI_PILOT_CONNECTED/WIFI_PILOT_DISCONNECTED (not the BLE server's
        PILOT_* verbs) so the dashboard can track the two transports separately —
        a WiFi drop must never clear a pilot who is still connected over BLE.
        Dashboards that predate the verbs just ignore them."""
        name = self._current_pilot_name()
        if name == self._last_pushed_pilot:
            return
        previous = self._last_pushed_pilot
        self._last_pushed_pilot = name
        if name:
            await self.dashboard.send_command(f"WIFI_PILOT_CONNECTED:{name}")
        elif previous:
            await self.dashboard.send_command(f"WIFI_PILOT_DISCONNECTED:{previous}")

    async def _on_message(self, state: ClientState, raw: str) -> None:
        message = protocol.decode(raw)
        if message is None:
            return
        mtype = message["type"]

        if mtype == "client_hello":
            state.hello_received = True
            state.role = str(message.get("role", "viewer"))  # explicit from now on
            state.user = message.get("user") or message.get("name")
            state.user_id = message.get("user_id")
            state.device = message.get("device")
            state.hello_at = time.time()
            if ARBITRATION and state.role == "controller" and self._controller is None:
                self._controller = state
            logger.info(
                "client_hello from %s role=%s user=%s", state.peer, state.role, state.user
            )
            connection_registry.record_event(
                "hello", state.transport, peer=state.peer, role=state.role,
                name=state.user, user_id=state.user_id, device=state.device,
            )
            self._write_wifi_snapshot()
            await self._apply_loc(state, message.get("loc"))
            await self._push_pilot_status()
        elif mtype == "command":
            await self._handle_command(state, message)
        elif mtype == "config_command":
            await self._handle_config_command(state, message)
        elif mtype == "maintenance_control":
            await self._handle_maintenance(state, message, kind="control")
        elif mtype == "maintenance_input":
            await self._handle_maintenance(state, message, kind="input")
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
                state.user_id = message.get("user_id") or state.user_id
                state.device = message.get("device") or state.device
                state.hello_at = time.time()
                logger.info(
                    "client_hello (cmd) from %s role=%s user=%s", state.peer, state.role, state.user
                )
                connection_registry.record_event(
                    "hello", state.transport, peer=state.peer, role=state.role,
                    name=state.user, user_id=state.user_id, device=state.device,
                )
                self._write_wifi_snapshot()
                await self._apply_loc(state, message.get("loc") or message)
                await self._send(state, protocol.build_command_result(cmd_id, True, "hello"))
                await self._push_pilot_status()
                return
            # Lightweight location update — {"cmd":"loc_update","lat":..,"lon":..,
            # "acc":..}. Stored per client; a pilot's location is forwarded to
            # the dashboard so it can be stamped onto loads (see WIFI_PILOT_LOC).
            if str(message.get("cmd", "")).strip().lower() in ("loc_update", "loc"):
                await self._apply_loc(state, message)
                await self._send(state, protocol.build_command_result(cmd_id, True, "loc"))
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

    async def _handle_config_command(self, state: ClientState, message: dict) -> None:
        """Dispatch an inbound config-system command and reply with the WHOLE
        response JSON (no chunking, no compression). The app builds a ConfigCommand
        and the fields ride at the top level of this message; we forward them to
        the config handler verbatim, then echo op + request_id back so the app can
        correlate the reply with its pending request."""
        op = message.get("op", "")
        request_id = message.get("request_id")
        # Pass the message straight through (minus the envelope `type`). Unknown
        # fields are harmless; the handler keys off the ones it knows.
        cmd = {k: v for k, v in message.items() if k != "type"}
        response = await self.config_handler.handle(cmd)
        await self._send(state, protocol.build_config_response(op, request_id, response))
        # A write op may have changed the trailer assignment / offsets — re-emit
        # trailer_config to every client if so (cheap; only broadcasts on change).
        await self._broadcast_trailer_config()

    async def _handle_maintenance(self, state: ClientState, message: dict, *, kind: str) -> None:
        """Relay a signed remote-maintenance frame to this connection's PTY shell.

        The admin server signs control/stdin/resize frames and the iPad forwards
        the SAME bytes verbatim under `frame` (we verify, never re-sign). Output
        (PTY bytes full-rate, status events) is pushed back as `maintenance_output`
        on THIS connection only — the relay is point-to-point per session."""
        # The signed frame rides under `frame`; tolerate a flat frame too
        # (everything except the envelope `type`) for forward-compat.
        frame = message.get("frame")
        if not isinstance(frame, dict):
            frame = {k: v for k, v in message.items() if k != "type"}

        if state.maintenance is None:
            async def _emit(out_frame: dict) -> None:
                await self._send(state, protocol.build_maintenance_output(out_frame))

            state.maintenance = MaintenanceHandler(_emit, asyncio.get_running_loop())

        if kind == "control":
            await state.maintenance.handle_control(frame)
        else:
            await state.maintenance.handle_input(frame)

    def _read_trailer_config(self) -> Optional[dict]:
        """Current trailer config (bumble: _current_trailer_info) read from the
        shared Pi files. Returns None only if reading raises."""
        try:
            return _current_trailer_info()
        except Exception as e:
            logger.warning("trailer config read failed: %s", e)
            return None

    async def _broadcast_trailer_config(self) -> None:
        """Broadcast trailer_config when the current trailer config changes."""
        trailer = self._read_trailer_config()
        if trailer is None:
            return
        trailer_json = json.dumps(trailer, sort_keys=True, separators=(",", ":"))
        if trailer_json != self._last_trailer_config_json:
            self._last_trailer_config_json = trailer_json
            await self._broadcast(protocol.build_trailer_config(trailer))

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
                    # Trailer config rarely changes (only on a SELECT_TRAILER /
                    # config-file edit, by us or bumble) — poll it on the same
                    # slow cadence as history and broadcast only on change.
                    await self._broadcast_trailer_config()
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
            *(self._send_bounded(c, payload) for c in list(self.clients.values())),
            return_exceptions=True,
        )

    async def _send_bounded(self, state: ClientState, payload: str) -> None:
        """A broadcast send, isolated: one stalled/half-open client must never
        block the gather (and with it every healthy client's updates). On
        timeout, abort the offender's transport — that surfaces as a closed
        connection in its handler, which runs the normal cleanup path."""
        try:
            await asyncio.wait_for(state.ws.send(payload), BROADCAST_SEND_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(
                "broadcast send to %s stalled >%.0fs; aborting that connection",
                state.peer, BROADCAST_SEND_TIMEOUT,
            )
            try:
                state.ws.transport.abort()
            except Exception:
                pass
        except Exception:
            # Drop happens in the connection's own finally; ignore here.
            pass

    # --- send helpers ------------------------------------------------------
    async def _send(self, state: ClientState, message: dict) -> None:
        await self._send_raw(state, protocol.encode(message))

    async def _send_raw(self, state: ClientState, payload: str) -> None:
        try:
            await state.ws.send(payload)
        except Exception:
            # Drop happens in the connection's own finally; ignore here.
            pass
