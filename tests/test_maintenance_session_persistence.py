"""The maintenance PTY shell must survive its WebSocket dropping and reattach
on reconnect (no-internet trailer AP resets the socket every ~4 min), and must
be reaped when the session is genuinely orphaned so a root shell never leaks.
"""
import asyncio
import time

import pytest

from rotorlink import maintenance_handler as mh
from rotorlink.maintenance_handler import (
    MAINTENANCE_DEVELOPMENT_SECRET,
    MaintenanceHandler,
    MaintenanceSessionRegistry,
    _maintenance_frame_signature_with_secret,
)


def _signed(**frame):
    frame.setdefault("session_id", "sess-1")
    frame.setdefault("seq", 1)
    frame.setdefault("expires_at", time.time() + 60)
    frame["sig"] = _maintenance_frame_signature_with_secret(
        frame, MAINTENANCE_DEVELOPMENT_SECRET
    )
    return frame


class _Sink:
    """A connection emitter: collects output frames; can be 'disconnected'."""
    def __init__(self):
        self.frames = []
        self.alive = True

    async def emit(self, frame):
        if self.alive:
            self.frames.append(frame)

    def pty_text(self):
        import base64
        out = b""
        for f in self.frames:
            if f.get("type") == "output" and f.get("enc") == "pty":
                out += base64.b64decode(f["text"])
        return out.decode("utf-8", "ignore")


async def _drain(seconds=0.4):
    deadline = time.time() + seconds
    while time.time() < deadline:
        await asyncio.sleep(0.02)


def test_registry_reattach_keeps_same_session():
    async def run():
        reg = MaintenanceSessionRegistry(asyncio.get_running_loop())
        a = _Sink()
        session1, reattached1 = reg.attach("sess-1", a.emit)
        assert reattached1 is False
        b = _Sink()
        session2, reattached2 = reg.attach("sess-1", b.emit)
        assert reattached2 is True
        assert session2 is session1          # same shell object
        assert session2._emit == b.emit      # output rebound to the new client
    asyncio.run(run())


def test_same_connection_frames_do_not_replay():
    async def run():
        reg = MaintenanceSessionRegistry(asyncio.get_running_loop())
        conn = _Sink()
        # A connection binds ONE stable emit (the handler stores self._emit
        # once). A repeat attach with that same object — e.g. the ~15s
        # heartbeat — must NOT count as a reattach, else every heartbeat
        # replays the whole screen.
        emit = conn.emit
        _s1, r1 = reg.attach("sess-1", emit)
        assert r1 is False
        _s2, r2 = reg.attach("sess-1", emit)
        assert r2 is False
        _s3, r3 = reg.attach("sess-1", emit)
        assert r3 is False
    asyncio.run(run())


def test_shell_survives_reconnect_same_pid():
    async def run():
        reg = MaintenanceSessionRegistry(asyncio.get_running_loop())

        # First connection: open a real shell, run a command.
        conn1 = _Sink()
        h1 = MaintenanceHandler(conn1.emit, asyncio.get_running_loop(), registry=reg)
        await h1.handle_control(_signed(type="open"))
        await _drain(0.3)
        pid1 = reg._sessions["sess-1"]._proc.pid
        await h1.handle_input(_signed(type="input", data="echo MARKER_ALPHA\n"))
        await _drain(0.4)
        assert "MARKER_ALPHA" in conn1.pty_text()

        # Socket drops — detach (must NOT kill the shell).
        conn1.alive = False
        await h1.shutdown()
        await asyncio.sleep(0.05)
        assert "sess-1" in reg._sessions          # still alive
        assert reg._sessions["sess-1"].is_open

        # Reconnect: new connection, same session id — reattaches to same shell.
        conn2 = _Sink()
        h2 = MaintenanceHandler(conn2.emit, asyncio.get_running_loop(), registry=reg)
        await h2.handle_input(_signed(type="input", data="echo MARKER_BETA\n"))
        await _drain(0.4)
        pid2 = reg._sessions["sess-1"]._proc.pid
        assert pid2 == pid1                       # SAME bash, not a respawn
        # New command's output reaches the reconnected client...
        assert "MARKER_BETA" in conn2.pty_text()
        # ...and recent scrollback was replayed on reattach.
        assert "MARKER_ALPHA" in conn2.pty_text()

        await reg.close("sess-1", "test done")
    asyncio.run(run())


def test_orphaned_session_is_reaped(monkeypatch):
    async def run():
        monkeypatch.setattr(mh, "ORPHAN_REAP_SECONDS", 0.2)
        reg = MaintenanceSessionRegistry(asyncio.get_running_loop())
        conn = _Sink()
        h = MaintenanceHandler(conn.emit, asyncio.get_running_loop(), registry=reg)
        await h.handle_control(_signed(type="open"))
        await _drain(0.3)
        assert reg._sessions["sess-1"].is_open

        # Client drops and never comes back → reaped after the grace window.
        conn.alive = False
        await h.shutdown()
        assert "sess-1" in reg._sessions          # still there during grace
        await asyncio.sleep(0.5)                    # past ORPHAN_REAP_SECONDS
        assert "sess-1" not in reg._sessions        # reaped — no leaked shell
    asyncio.run(run())


def test_reattach_within_grace_cancels_reap(monkeypatch):
    async def run():
        monkeypatch.setattr(mh, "ORPHAN_REAP_SECONDS", 0.4)
        reg = MaintenanceSessionRegistry(asyncio.get_running_loop())
        conn1 = _Sink()
        h1 = MaintenanceHandler(conn1.emit, asyncio.get_running_loop(), registry=reg)
        await h1.handle_control(_signed(type="open"))
        await _drain(0.3)
        pid1 = reg._sessions["sess-1"]._proc.pid

        conn1.alive = False
        await h1.shutdown()                          # arms reap (0.4s)
        await asyncio.sleep(0.15)                     # reconnect before it fires
        conn2 = _Sink()
        h2 = MaintenanceHandler(conn2.emit, asyncio.get_running_loop(), registry=reg)
        await h2.handle_input(_signed(type="input", data="true\n"))
        await asyncio.sleep(0.5)                      # well past the original 0.4s
        assert "sess-1" in reg._sessions             # reap was cancelled
        assert reg._sessions["sess-1"]._proc.pid == pid1
        await reg.close("sess-1", "test done")
    asyncio.run(run())


def test_remote_close_ends_session_immediately():
    async def run():
        reg = MaintenanceSessionRegistry(asyncio.get_running_loop())
        conn = _Sink()
        h = MaintenanceHandler(conn.emit, asyncio.get_running_loop(), registry=reg)
        await h.handle_control(_signed(type="open"))
        await _drain(0.3)
        await h.handle_control(_signed(type="close"))
        await asyncio.sleep(0.1)
        assert "sess-1" not in reg._sessions
    asyncio.run(run())
