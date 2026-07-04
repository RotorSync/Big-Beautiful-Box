"""
RotorLink remote-maintenance shell over WiFi.

This is the WiFi counterpart to the BLE maintenance relay in
`rotorsync_bumble.py`. The remote-maintenance model is unchanged: the RotorSync
admin server HMAC-SHA256-signs control frames and publishes them via MQTT, the
iPad `MaintenanceBridgeService` forwards the signed bytes verbatim, and this
module VERIFIES the signature and runs the shell. The only differences from the
BLE leg are the transport (WebSocket instead of GATT/MCHUNK) and the shell
itself:

  * the shell runs on a **PTY** (`pty.openpty()` + `bash -l`) so full-screen
    programs, line editing, colour and `\r` cursor moves work;
  * PTY output is streamed **FULL-RATE** (no 96-char / 80ms MCHUNK throttling).

The signature/secret scheme is replicated *byte-for-byte* from bumble so a frame
the admin server signs for the BLE Pi verifies identically here:

  secret precedence (first hit wins):
    env BBB_MAINTENANCE_SECRET, env MAINTENANCE_RELAY_SECRET,
    file /etc/rotorsync/maintenance.secret,
    file /home/pi/.rotorsync-maintenance-secret,
    else the development default b"rotorsync-development-maintenance-secret".

  canonical signing input:
    json.dumps({frame without "sig"}, ensure_ascii=False,
               separators=(",", ":"), sort_keys=True).encode("utf-8")

  signature:
    base64.urlsafe_b64encode(HMAC_SHA256(secret, canonical)).rstrip("=")

  verify:
    hmac.compare_digest; a development-default box may bootstrap-adopt a secret
    carried in the frame (maintenance_secret_b64 / relay_secret_b64 /
    bbb_maintenance_secret_b64); expires_at (epoch seconds) is enforced.

We do NOT re-sign anything: the admin server signs, we verify. BLE remains the
untouched fallback (bumble is not modified).
"""

import asyncio
import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import logging
import os
import struct
import termios
import time
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional

logger = logging.getLogger("rotorlink.maintenance")

# How long a maintenance PTY shell survives after its WebSocket drops, waiting
# for the client to reconnect and reattach. On a no-internet trailer AP iOS
# resets the box socket every ~4 min; the client auto-reconnects in ~16s, so a
# shell that respawned each time was never usable. We keep the shell (cwd,
# history, running commands) alive across the gap and reap it only when the
# session is genuinely orphaned. Env-overridable for tests.
ORPHAN_REAP_SECONDS = float(os.environ.get("ROTORLINK_MAINTENANCE_ORPHAN_REAP", "120"))
# Bytes of recent PTY output replayed to a reattaching client so the terminal
# redraws its current screen instead of coming back blank.
REATTACH_REPLAY_BYTES = 16 * 1024

# --- secret loading (mirrors rotorsync_bumble.py exactly) -------------------
MAINTENANCE_SECRET_PATHS = (
    "/etc/rotorsync/maintenance.secret",
    "/home/pi/.rotorsync-maintenance-secret",
)
MAINTENANCE_DEVELOPMENT_SECRET = b"rotorsync-development-maintenance-secret"
MAINTENANCE_USER_SECRET_PATH = "/home/pi/.rotorsync-maintenance-secret"
MAINTENANCE_FRAME_SECRET_FIELDS = (
    "maintenance_secret_b64",
    "relay_secret_b64",
    "bbb_maintenance_secret_b64",
)

# The login shell runs here when the directory exists (same repo dir bumble uses
# so `git`/`./install.sh` land in the right place), else the process cwd.
MAINTENANCE_REPO_DIR = os.environ.get(
    "ROTORLINK_MAINTENANCE_REPO_DIR", "/home/pi/Big-Beautiful-Box"
)

# Cap a single PTY read; the loop reads repeatedly so this only bounds latency,
# not throughput. Full-rate: every byte the shell emits is forwarded as soon as
# it is read, base64-wrapped, with no inter-chunk delay.
PTY_READ_SIZE = 65536


def _provisioned_maintenance_secret_source():
    for env_name in ("BBB_MAINTENANCE_SECRET", "MAINTENANCE_RELAY_SECRET"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return f"env:{env_name}", value.encode("utf-8")

    for path in MAINTENANCE_SECRET_PATHS:
        try:
            with open(path, "rb") as f:
                value = f.read().strip()
            if value:
                return f"file:{path}", value
        except OSError:
            continue

    return None, None


def _maintenance_secret_source():
    source, secret = _provisioned_maintenance_secret_source()
    if secret:
        return source, secret
    return "development-default", MAINTENANCE_DEVELOPMENT_SECRET


def log_maintenance_secret_status() -> None:
    source, _secret = _maintenance_secret_source()
    if source == "development-default":
        logger.warning(
            "maintenance relay secret missing; admin maintenance frames signed "
            "with the fleet secret will be rejected"
        )
    else:
        logger.info("maintenance relay secret source: %s", source)


def _frame_maintenance_secret(frame: dict) -> Optional[bytes]:
    for key in MAINTENANCE_FRAME_SECRET_FIELDS:
        value = frame.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            secret = base64.b64decode(value.strip(), validate=True)
        except Exception as e:  # noqa: BLE001 - match bumble's broad raise
            raise ValueError("invalid maintenance secret bootstrap") from e
        secret = secret.strip()
        if len(secret) < 32 or len(secret) > 4096:
            raise ValueError("invalid maintenance secret bootstrap length")
        return secret
    return None


def _install_maintenance_secret(secret: bytes) -> bool:
    source, _existing = _provisioned_maintenance_secret_source()
    if source:
        return False
    try:
        path = Path(MAINTENANCE_USER_SECRET_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(path.name + ".tmp")
        with open(tmp_path, "wb") as f:
            f.write(secret.strip() + b"\n")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
        logger.info("maintenance relay secret provisioned at %s", path)
        return True
    except OSError as e:
        logger.warning("could not persist bootstrapped maintenance secret: %s", e)
        return False


def _canonical_maintenance_payload(frame: dict) -> bytes:
    unsigned = {key: value for key, value in frame.items() if key != "sig"}
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _maintenance_frame_signature_with_secret(frame: dict, secret: bytes) -> str:
    digest = hmac.new(
        secret,
        _canonical_maintenance_payload(frame),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def verify_maintenance_frame(frame: dict, now: Optional[float] = None) -> None:
    """Raise ValueError unless `frame` carries a valid `sig` and is unexpired.

    Identical semantics to bumble's `_verify_maintenance_frame`: compare_digest
    against the provisioned/dev secret; on a development-default box, fall back
    to a frame-carried bootstrap secret and adopt it; enforce `expires_at`.
    """
    import time

    if not isinstance(frame, dict):
        raise ValueError("maintenance frame must be a JSON object")

    signature = frame.get("sig")
    if not isinstance(signature, str) or not signature:
        raise ValueError("missing frame signature")

    source, secret = _maintenance_secret_source()
    expected = _maintenance_frame_signature_with_secret(frame, secret)
    if hmac.compare_digest(signature, expected):
        pass
    else:
        bootstrap_secret = None
        if source == "development-default":
            bootstrap_secret = _frame_maintenance_secret(frame)
        if not bootstrap_secret:
            raise ValueError("invalid frame signature")
        expected = _maintenance_frame_signature_with_secret(frame, bootstrap_secret)
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid frame signature")
        _install_maintenance_secret(bootstrap_secret)

    if not hmac.compare_digest(signature, expected):
        raise ValueError("invalid frame signature")

    expires_at = frame.get("expires_at")
    if expires_at is not None:
        try:
            expires_at_value = float(expires_at)
        except (TypeError, ValueError) as e:
            raise ValueError("invalid frame expiry") from e
        if (now if now is not None else time.time()) > expires_at_value:
            raise ValueError("expired maintenance frame")


def parse_maintenance_payload(payload_bytes) -> Optional[dict]:
    """JSON-decode a control/stdin payload to a dict, or None if not a JSON object."""
    if isinstance(payload_bytes, (bytes, bytearray)):
        try:
            text = bytes(payload_bytes).decode("utf-8")
        except UnicodeDecodeError:
            return None
    else:
        text = str(payload_bytes)
    try:
        obj = json.loads(text)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


# Output emitter signature: called with a maintenance-output frame dict that the
# server wraps into a `maintenance_output` envelope. For PTY data we set
# enc="pty" and put base64(raw bytes) in `text`; for status events we send plain
# text (enc absent) the same way bumble does.
OutputEmitter = Callable[[dict], Awaitable[None]]


class MaintenanceSession:
    """One PTY login shell, owned by a single RotorLink connection.

    Lifecycle: `open()` spawns `bash -l` on a PTY and starts a full-rate reader
    that pumps every byte out via the emitter (base64, enc="pty"). `write_stdin`
    feeds keystrokes to the PTY; `resize` issues TIOCSWINSZ; `close` terminates
    the shell. All output frames carry the session_id so the admin/xterm side can
    correlate.
    """

    def __init__(self, emit: OutputEmitter, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self._emit = emit
        self._loop = loop or asyncio.get_event_loop()
        self._seq = 0
        self.session_id: str = "unknown"
        self._master_fd: Optional[int] = None
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._pending_rows: Optional[int] = None
        self._pending_cols: Optional[int] = None
        self._closing = False
        # Ring of recent raw PTY bytes, replayed when a reconnecting client
        # reattaches so the terminal redraws instead of coming back blank.
        self._replay_buffer: "deque[bytes]" = deque()
        self._replay_bytes = 0
        # Set by the registry so a shell that exits on its own (or is closed)
        # is dropped from the session store.
        self.on_closed: Optional[Callable[[str], None]] = None

    def rebind_emit(self, emit: OutputEmitter) -> None:
        """Point this live shell's output at a (re)connected client's socket.
        Output produced while detached still went into the replay buffer, so a
        reattaching client can redraw via replay_recent()."""
        self._emit = emit

    def _buffer_output(self, data: bytes) -> None:
        self._replay_buffer.append(data)
        self._replay_bytes += len(data)
        while self._replay_bytes > REATTACH_REPLAY_BYTES and len(self._replay_buffer) > 1:
            self._replay_bytes -= len(self._replay_buffer.popleft())

    async def replay_recent(self) -> None:
        """Re-emit the recent PTY output to the current emitter (post-rebind)."""
        if not self._replay_buffer:
            return
        combined = b"".join(self._replay_buffer)
        with contextlib.suppress(Exception):
            await self._emit_pty_bytes(combined, buffer=False)

    # --- output framing ----------------------------------------------------
    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def _emit_status(self, event_type: str, text: str = "", **extra) -> None:
        frame = {
            "type": event_type,
            "seq": self._next_seq(),
            "session_id": self.session_id,
        }
        if text:
            frame["text"] = text
        for k, v in extra.items():
            if v is not None:
                frame[k] = v
        await self._emit(frame)

    async def _emit_pty_bytes(self, data: bytes, *, buffer: bool = True) -> None:
        # FIXED WIRE CONTRACT: PTY output rides the existing maintenance-frame
        # shape with enc="pty" and base64(raw PTY bytes) in `text`. Full-rate.
        if buffer:
            self._buffer_output(data)
        frame = {
            "type": "output",
            "seq": self._next_seq(),
            "session_id": self.session_id,
            "enc": "pty",
            "text": base64.b64encode(data).decode("ascii"),
        }
        await self._emit(frame)

    # --- lifecycle ---------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def open(self, session_id: Optional[str] = None) -> None:
        if session_id:
            self.session_id = str(session_id)
        if self.is_open:
            return

        import pty

        master_fd, slave_fd = pty.openpty()
        # Non-blocking master so the asyncio reader never stalls the loop.
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Apply any resize that arrived before the shell existed.
        if self._pending_rows and self._pending_cols:
            self._set_winsize(master_fd, self._pending_rows, self._pending_cols)
            self._pending_rows = self._pending_cols = None

        env = dict(os.environ)
        env["TERM"] = env.get("TERM") or "xterm-256color"
        cwd = MAINTENANCE_REPO_DIR if os.path.isdir(MAINTENANCE_REPO_DIR) else os.getcwd()

        try:
            proc = await asyncio.create_subprocess_exec(
                "/bin/bash",
                "-l",
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                env=env,
                start_new_session=True,  # own session/controlling tty on the PTY
            )
        finally:
            # The child holds the slave; we only need the master.
            os.close(slave_fd)

        self._master_fd = master_fd
        self._proc = proc
        self._closing = False
        self._reader_task = self._loop.create_task(self._read_loop())
        await self._emit_status(
            "session_opened", text="Maintenance shell ready\n", enc="pty"
        )
        logger.info("maintenance PTY shell opened (session=%s, pid=%s)", self.session_id, proc.pid)

    async def _read_loop(self) -> None:
        """Full-rate pump of PTY master -> emitter. One add_reader callback feeds
        an asyncio.Queue; we drain and forward without throttling."""
        assert self._master_fd is not None
        fd = self._master_fd
        queue: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue()

        def _on_readable() -> None:
            try:
                data = os.read(fd, PTY_READ_SIZE)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                # PTY closed (shell exited) — EOF sentinel.
                data = b""
            queue.put_nowait(data if data else None)
            if not data:
                # Stop watching a dead fd.
                with contextlib.suppress(Exception):
                    self._loop.remove_reader(fd)

        self._loop.add_reader(fd, _on_readable)
        try:
            while True:
                data = await queue.get()
                if data is None:
                    break
                await self._emit_pty_bytes(data)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("maintenance PTY read error: %s", e)
        finally:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(fd)
            if not self._closing:
                # Shell exited on its own — tear the session down + notify.
                await self.close(reason="shell exited")

    async def write_stdin(self, data: bytes, session_id: Optional[str] = None) -> None:
        if session_id:
            self.session_id = str(session_id)
        if not self.is_open:
            await self.open(session_id)
        if self._master_fd is None:
            raise RuntimeError("maintenance shell PTY unavailable")
        # PTY master is non-blocking; write in a loop in case of EAGAIN.
        view = memoryview(data)
        while view:
            try:
                n = os.write(self._master_fd, view)
                view = view[n:]
            except BlockingIOError:
                await asyncio.sleep(0)
            except OSError as e:
                raise RuntimeError(f"maintenance shell stdin write failed: {e}") from e

    @staticmethod
    def _set_winsize(fd: int, rows: int, cols: int) -> None:
        winsize = struct.pack("HHHH", int(rows), int(cols), 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

    async def resize(self, rows: int, cols: int) -> None:
        rows = int(rows)
        cols = int(cols)
        if rows <= 0 or cols <= 0:
            return
        if self._master_fd is not None and self.is_open:
            try:
                self._set_winsize(self._master_fd, rows, cols)
            except OSError as e:
                logger.warning("maintenance PTY resize failed: %s", e)
        else:
            # Remember it for when the shell opens.
            self._pending_rows, self._pending_cols = rows, cols

    async def close(self, reason: str = "closed") -> None:
        if self._closing:
            return
        self._closing = True

        reader = self._reader_task
        self._reader_task = None
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader

        proc = self._proc
        self._proc = None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()

        if self._master_fd is not None:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(self._master_fd)
            with contextlib.suppress(OSError):
                os.close(self._master_fd)
            self._master_fd = None

        with contextlib.suppress(Exception):
            await self._emit_status("closed", reason=reason)
        logger.info("maintenance PTY shell closed (session=%s, reason=%s)", self.session_id, reason)
        if self.on_closed is not None:
            with contextlib.suppress(Exception):
                self.on_closed(self.session_id)


class MaintenanceSessionRegistry:
    """Server-wide store of live PTY shells keyed by session_id, so a shell
    outlives the WebSocket connection that started it.

    When a connection drops we do NOT kill its shell — we start an orphan-reap
    timer. If the client reconnects (a new WebSocket for the same session_id)
    before the timer fires, its output is rebound to the new connection and the
    recent screen is replayed; the user keeps their shell across the blip. If no
    one reattaches, the shell is reaped (root shells never leak)."""

    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self._loop = loop or asyncio.get_event_loop()
        self._sessions: Dict[str, MaintenanceSession] = {}
        self._reap_handles: Dict[str, asyncio.TimerHandle] = {}

    def attach(self, session_id: str, emit: OutputEmitter) -> tuple:
        """Get-or-create the shell for session_id and bind its output to `emit`.
        Returns (session, reattached) — reattached=True ONLY when a DIFFERENT
        connection resumed an existing shell (caller replays recent output).

        A frame from the SAME connection (e.g. the ~15s heartbeat) must not
        count as a reattach — otherwise every heartbeat would rebind and replay
        the whole screen, flooding the terminal."""
        self._cancel_reap(session_id)
        session = self._sessions.get(session_id)
        if session is not None and not session._closing:
            if session._emit is emit:
                return session, False          # same connection, ordinary frame
            session.rebind_emit(emit)
            logger.info("maintenance session %s reattached", session_id)
            return session, True
        session = MaintenanceSession(emit, self._loop)
        session.session_id = session_id
        session.on_closed = self.note_closed
        self._sessions[session_id] = session
        return session, False

    def detach(self, session_id: str) -> None:
        """The client for this session dropped — arm the orphan-reap timer."""
        session = self._sessions.get(session_id)
        if session is None or session._closing:
            self._sessions.pop(session_id, None)
            return
        self._cancel_reap(session_id)
        logger.info(
            "maintenance session %s detached; reaping in %.0fs if no reattach",
            session_id, ORPHAN_REAP_SECONDS,
        )
        self._reap_handles[session_id] = self._loop.call_later(
            ORPHAN_REAP_SECONDS,
            lambda: self._loop.create_task(self._reap(session_id)),
        )

    async def _reap(self, session_id: str) -> None:
        self._reap_handles.pop(session_id, None)
        session = self._sessions.pop(session_id, None)
        if session is not None:
            logger.info("maintenance session %s orphaned — reaping shell", session_id)
            with contextlib.suppress(Exception):
                await session.close("orphaned (client did not reconnect)")

    def _cancel_reap(self, session_id: str) -> None:
        handle = self._reap_handles.pop(session_id, None)
        if handle is not None:
            handle.cancel()

    async def close(self, session_id: str, reason: str) -> None:
        """Explicitly end a session (remote close) — no reap grace."""
        self._cancel_reap(session_id)
        session = self._sessions.pop(session_id, None)
        if session is not None:
            with contextlib.suppress(Exception):
                await session.close(reason)

    def note_closed(self, session_id: str) -> None:
        """A shell closed itself (exit); drop it from the registry."""
        self._cancel_reap(session_id)
        self._sessions.pop(session_id, None)

    async def shutdown_all(self) -> None:
        for session_id in list(self._reap_handles):
            self._cancel_reap(session_id)
        for session_id, session in list(self._sessions.items()):
            with contextlib.suppress(Exception):
                await session.close("server shutdown")
        self._sessions.clear()


class MaintenanceHandler:
    """Per-connection maintenance dispatcher.

    Verifies signed control frames then acts on them (open/close/resize/
    heartbeat/stdin); the BLE bumble update_* frame types are NOT supported here
    (firmware updates stay on the BLE path). `handle_input` carries raw
    keystrokes and resize frames. The PTY shells themselves live in a shared
    `MaintenanceSessionRegistry` so they survive this connection dropping —
    `shutdown` detaches (arming the orphan reap) rather than killing the shell.
    """

    def __init__(
        self,
        emit: OutputEmitter,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        registry: Optional[MaintenanceSessionRegistry] = None,
    ) -> None:
        self._emit = emit
        self._loop = loop or asyncio.get_event_loop()
        # A shared registry keeps shells alive across reconnects; without one
        # (older call sites / tests) fall back to a private per-connection
        # registry so behaviour is still correct, just not cross-connection.
        self._registry = registry or MaintenanceSessionRegistry(self._loop)
        self._session: Optional[MaintenanceSession] = None
        self._session_id: str = "unknown"

    async def _bind_session(self, session_id: Optional[str]) -> MaintenanceSession:
        sid = str(session_id) if session_id else self._session_id
        self._session_id = sid
        session, reattached = self._registry.attach(sid, self._emit)
        self._session = session
        if reattached:
            await session.replay_recent()
        return session

    async def _emit_error(self, message: str, frame_type: Optional[str] = None) -> None:
        frame = {
            "type": "error",
            "session_id": self._session_id,
            "text": f"{message}\n",
            "reason": message,
        }
        if frame_type:
            frame["ack_type"] = frame_type
            frame["frame_type"] = frame_type
        await self._emit(frame)

    async def handle_control(self, payload) -> None:
        """Process a signed `maintenance_control` payload.

        `payload` may be a dict (already parsed by the server) or raw
        bytes/str (the verbatim signed control payload the iPad relayed)."""
        frame = payload if isinstance(payload, dict) else parse_maintenance_payload(payload)
        if not frame:
            logger.info("maintenance control rejected: unsigned/undecodable payload")
            await self._emit_error("unsigned maintenance control payload rejected")
            return

        frame_type = str(
            frame.get("type") or frame.get("kind") or frame.get("op") or ""
        ).lower()
        session_id = frame.get("session_id") or frame.get("sessionId")

        try:
            verify_maintenance_frame(frame)
        except Exception as e:  # noqa: BLE001
            logger.info("maintenance control signature rejected: %s", e)
            await self._emit_error(str(e), frame_type or None)
            return

        session = await self._bind_session(session_id)

        try:
            if frame_type == "open":
                await session.open(str(session_id) if session_id else None)
            elif frame_type == "close":
                await self._registry.close(self._session_id, "remote close requested")
                self._session = None
            elif frame_type == "resize":
                rows, cols = _frame_rows_cols(frame)
                await session.resize(rows, cols)
            elif frame_type == "heartbeat":
                await session._emit_status("heartbeat")
            elif frame_type in ("stdin", "input"):
                data = _frame_stdin_bytes(frame)
                await session.write_stdin(data, str(session_id) if session_id else None)
            else:
                data = frame.get("data") or frame.get("command") or frame.get("cmd")
                if data:
                    await session.write_stdin(
                        _coerce_bytes(data), str(session_id) if session_id else None
                    )
                else:
                    await self._emit_error(
                        f"unsupported maintenance frame type: {frame_type or 'unknown'}",
                        frame_type or None,
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("maintenance control error: %s", e)
            await self._emit_error(str(e), frame_type or None)

    async def handle_input(self, payload) -> None:
        """Process a signed `maintenance_input` payload (keystrokes or resize)."""
        frame = payload if isinstance(payload, dict) else parse_maintenance_payload(payload)
        if not frame:
            logger.info("maintenance input rejected: unsigned/undecodable payload")
            await self._emit_error("unsigned maintenance input payload rejected")
            return

        frame_type = str(
            frame.get("type") or frame.get("kind") or frame.get("op") or ""
        ).lower()
        session_id = frame.get("session_id") or frame.get("sessionId")

        try:
            verify_maintenance_frame(frame)
        except Exception as e:  # noqa: BLE001
            logger.info("maintenance input signature rejected: %s", e)
            await self._emit_error(str(e), frame_type or None)
            return

        session = await self._bind_session(session_id)
        try:
            if frame_type == "resize":
                rows, cols = _frame_rows_cols(frame)
                await session.resize(rows, cols)
            elif frame_type in ("", "stdin", "input"):
                data = _frame_stdin_bytes(frame)
                await session.write_stdin(data, str(session_id) if session_id else None)
            else:
                raise ValueError(
                    f"unsupported maintenance input frame type: {frame_type or 'unknown'}"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("maintenance input error: %s", e)
            await self._emit_error(str(e), frame_type or None)

    async def shutdown(self) -> None:
        """This connection dropped. Do NOT kill the shell — detach it so the
        registry keeps it alive for a reconnect and reaps it only if orphaned."""
        if self._session is not None and self._session_id != "unknown":
            self._registry.detach(self._session_id)
        self._session = None


# --- frame field helpers ----------------------------------------------------
def _coerce_bytes(value) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return str(value).encode("utf-8")


def _frame_stdin_bytes(frame: dict) -> bytes:
    """Extract raw keystroke bytes from a stdin/input frame.

    A frame may carry base64 (enc="b64"/"base64") so control characters survive
    JSON; otherwise the value is treated as a UTF-8 string (matching bumble,
    which forwards `data` as UTF-8)."""
    data = frame.get("data")
    if data is None:
        data = frame.get("input")
    if data is None:
        data = frame.get("text")
    if data is None:
        data = ""
    enc = str(frame.get("enc") or "").lower()
    if enc in ("b64", "base64") and isinstance(data, str):
        try:
            return base64.b64decode(data)
        except Exception:  # noqa: BLE001
            return _coerce_bytes(data)
    return _coerce_bytes(data)


def _frame_rows_cols(frame: dict) -> tuple:
    rows = frame.get("rows")
    cols = frame.get("cols")
    if rows is None:
        rows = frame.get("height") or frame.get("h")
    if cols is None:
        cols = frame.get("width") or frame.get("w")
    return int(rows or 0), int(cols or 0)
