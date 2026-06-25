"""
RotorLink wire protocol — a thin typed JSON envelope over WebSocket text frames.

Every message is a JSON object with a `type` string. The forward-compatibility
contract (so a newer Pi never breaks an older app, and vice versa):
  * extra/unknown fields are ignored;
  * unknown `type`s are ignored (logged, connection kept);
  * new fields are added, never renamed/retyped in place.

Message types
-------------
Pi  -> app:  hello            device descriptor + capability manifest (on connect)
             state            {state: {...}}  live dashboard snapshot, on change
             command_result   {id, ok, response}  reply to a command
             history          {history: "..."}  last-fills blob, on change
             bms              {bms: {...}}  battery snapshot, on change
             mopeka           {index, mopeka: {...}}  per-tank level, on change
             trailer_config   {trailer: {...}}  current trailer config, on connect/change
             config_response  {op, request_id, response: {...}}  reply to a config_command
             maintenance_output  {frame: {...}}  remote-maintenance shell output
                                 (PTY bytes: frame.enc="pty", frame.text=base64)
             error            {message}
app -> Pi :  client_hello     {role, user, device}  who is connecting
             command          {id?, command, args?}  a dashboard command line
             config_command   {op, request_id, ...}  a config-system command (whole-JSON reply)
             maintenance_control {frame: {...}}  signed control frame (open/close/
                                 resize/heartbeat/stdin), verbatim from the admin server
             maintenance_input   {frame: {...}}  signed stdin/resize frame (keystrokes)
             ping             ->  pong

Remote maintenance (WiFi PTY relay)
-----------------------------------
The admin server HMAC-SHA256-signs control frames and the iPad relays the SAME
signed bytes here unchanged (we verify, never re-sign). `maintenance_control`
and `maintenance_input` carry that signed frame under `frame`. Outbound
`maintenance_output` carries a maintenance-frame dict under `frame`; PTY output
sets `frame.enc = "pty"` with base64(raw PTY bytes) in `frame.text`, streamed
full-rate. The BLE leg (rotorsync_bumble.py) is the untouched fallback.
"""

import json
import logging
from typing import Any, Optional

from . import PROTOCOL_VERSION, config

logger = logging.getLogger("rotorlink.protocol")


def build_hello() -> dict:
    """The first frame the Pi sends to a freshly connected client."""
    return {
        "type": "hello",
        "proto": PROTOCOL_VERSION,
        "device": config.device_descriptor(),
        "capabilities": config.capability_manifest(),
    }


def build_state(state: dict) -> dict:
    return {"type": "state", "state": state}


def build_history(history: str) -> dict:
    return {"type": "history", "history": history}


def build_bms(bms: dict) -> dict:
    return {"type": "bms", "bms": bms}


def build_mopeka(index: int, mopeka: dict) -> dict:
    return {"type": "mopeka", "index": index, "mopeka": mopeka}


def build_trailer_config(trailer: dict) -> dict:
    """The TRAILER characteristic payload (bumble: _current_trailer_info),
    emitted on connect and on change. Feeds the app's parseTrailerConfig."""
    return {"type": "trailer_config", "trailer": trailer}


def build_config_response(op, request_id, response: dict) -> dict:
    """Reply to an inbound `config_command`. The WHOLE response JSON rides in
    `response`; `op`/`request_id` are surfaced at the envelope level too so the
    app can correlate even before decoding the body."""
    return {
        "type": "config_response",
        "op": op,
        "request_id": request_id,
        "response": response,
    }


def build_command_result(cmd_id: Optional[str], ok: bool, response: Any) -> dict:
    return {"type": "command_result", "id": cmd_id, "ok": ok, "response": response}


def build_maintenance_output(frame: dict) -> dict:
    """Wrap a remote-maintenance output frame for the app.

    `frame` is the maintenance-frame dict (type/seq/session_id/text/...). For
    PTY data it carries enc="pty" with base64(raw PTY bytes) in `text`; for
    status events (session_opened/closed/error/heartbeat) `enc` is absent and
    `text` is plain. Full-rate; the app feeds `frame` to its existing MQTT
    publish path unchanged."""
    return {"type": "maintenance_output", "frame": frame}


def build_error(message: str) -> dict:
    return {"type": "error", "message": message}


def encode(message: dict) -> str:
    return json.dumps(message, separators=(",", ":"))


def decode(raw: str) -> Optional[dict]:
    """
    Parse one inbound frame. Returns None (and logs) on anything malformed —
    one bad message must never crash the server or drop the connection.
    """
    try:
        message = json.loads(raw)
        if isinstance(message, dict) and isinstance(message.get("type"), str):
            return message
        logger.warning("ignoring frame without a string `type`: %.120s", raw)
    except Exception as e:
        logger.warning("ignoring undecodable frame: %s", e)
    return None
