"""swbox r2 CAN consumer: frames built per swbox-fw docs/swbox.dbc."""
import ast
import struct
from pathlib import Path

from src import swbox_can as sc

DASH = Path(__file__).resolve().parents[1] / "dashboard.py"


def status(ovr=0, ps=0, fill_mix=0, tu=0, enc_sw=0, valid=1, ps_latched=0, enc=0, seq=0, ps_seq=0):
    d0 = ovr | ps << 1 | fill_mix << 2 | tu << 3 | enc_sw << 4
    d1 = valid | ps_latched << 5
    return sc.ID_STATUS, bytes([d0, d1, 0]) + struct.pack("<h", enc) + bytes([seq, ps_seq, 0])


def event(src, state, seq, long=0, enc_moved=0):
    return sc.ID_EVENT, bytes([src, state, seq]) + struct.pack("<I", 1234) + bytes([long | enc_moved << 1])


def ps(state, ps_seq, copy, latched=1, long=0):
    return sc.ID_PS, bytes([state, ps_seq, copy, latched | long << 1]) + struct.pack("<I", 99)


def enc(count, step=1, mod=0, seq=0):
    return sc.ID_ENC, struct.pack("<hbBB", count, step, mod, seq) + b"\0\0\0"


def boot():
    return sc.ID_BOOT, bytes([2, 2, 0, 1, 0, 1, 0, 0])


class Rig:
    def __init__(self):
        self.tokens, self.sent, self.t = [], [], 100.0
        self.c = sc.SwitchboxCanConsumer(self.tokens.append, lambda i, d: self.sent.append((i, bytes(d))),
                                         clock=lambda: self.t)

    def feed(self, *frames):
        for f in frames:
            self.c.handle(*f)

    def cmds(self):
        return [t for t in self.tokens if t != "OK"]


def test_ids_match_dbc():
    assert sc.ID_PS == 0x04FF3320 and sc.ID_STATUS == 0x18FF3020
    assert sc.ID_EVENT == 0x18FF3120 and sc.ID_ENC == 0x18FF3220 and sc.ID_BOOT == 0x18FF3420
    assert sc.cmd_req_id(0xF9) == 0x18EF20F9 and sc.cmd_rep_id(0xF9) == 0x18EFF920


def test_socketcan_frame_roundtrip():
    raw = sc.pack_frame(sc.ID_STATUS, b"\x01\x02")
    assert len(raw) == sc.CAN_FRAME_SIZE
    assert sc.unpack_frame(raw) == (sc.ID_STATUS, b"\x01\x02")
    std = struct.pack(sc.CAN_FRAME_FMT, 0x123, 1, b"\0" * 8)
    assert sc.unpack_frame(std)[0] is None


def test_status_is_heartbeat_with_3s_timeout():
    r = Rig()
    assert not r.c.heartbeat_ok()
    r.feed(status())
    assert r.tokens[0] == "OK" and r.c.heartbeat_ok()
    r.t += 3.0
    assert r.c.heartbeat_ok()
    r.t += 0.1
    assert not r.c.heartbeat_ok()


def test_fill_mix_initial_and_edges():
    r = Rig()
    r.feed(status(fill_mix=0), status(fill_mix=0), status(fill_mix=1), event(sc.SRC_FILL_MIX, 1, 5),
           event(sc.SRC_FILL_MIX, 0, 6), status(fill_mix=0))
    assert r.cmds() == ["FILL", "MIX", "FILL"]


def test_ov_tu_press_only_and_dedup():
    r = Rig()
    r.feed(event(sc.SRC_OVR, 1, 1), event(sc.SRC_OVR, 1, 1), event(sc.SRC_OVR, 0, 2),
           event(sc.SRC_TU, 1, 3), event(sc.SRC_OVR, 1, 4, long=1), event(sc.SRC_OVR, 2, 5),
           event(sc.SRC_ENC_SW, 1, 6), event(sc.SRC_PS, 1, 7))
    assert r.cmds() == ["OV", "TU"]


def test_ps_three_copies_one_dispatch_and_ack():
    r = Rig()
    r.feed(status(), ps(1, 7, 0), ps(1, 7, 1), ps(1, 7, 2), ps(0, 7, 0, latched=1), ps(1, 7, 0, long=1))
    assert r.cmds() == ["FILL", "PS"]
    assert r.sent == [(0x18EF20F9, bytes([sc.OP_PS_ACK, 7]))]
    # latch still set -> ACK resent (rate limited), not a second PS
    r.feed(status(ps_latched=1, ps_seq=7))
    assert len(r.sent) == 1
    r.t += 1
    r.feed(status(ps_latched=1, ps_seq=7))
    assert len(r.sent) == 2 and r.cmds().count("PS") == 1
    r.feed(status(ps_latched=0, ps_seq=7))
    assert r.c.ps_ack_seq is None
    r.feed(ps(1, 8, 0))
    assert r.cmds().count("PS") == 2 and r.sent[-1][1] == bytes([5, 8])


def test_ps_frames_lost_status_latch_recovers_press():
    r = Rig()
    r.feed(status(), status(ps_latched=1, ps_seq=3))
    assert r.cmds() == ["FILL", "PS"] and r.sent[-1][1] == bytes([5, 3])


def test_ps_latched_at_host_start_not_replayed():
    r = Rig()
    r.feed(status(ps_latched=1, ps_seq=9))
    assert "PS" not in r.cmds() and r.sent[-1][1] == bytes([5, 9])


def test_encoder_steps_and_x10():
    r = Rig()
    r.feed(enc(0), enc(1), enc(3), enc(2), enc(12, 10, 1), enc(-8, -10, 1), enc(-8))
    assert r.cmds() == ["+1", "+1", "+1", "-1", "+10", "-10", "-10"]


def test_encoder_int16_wrap_and_status_count():
    r = Rig()
    r.feed(enc(32766), enc(-32767))     # +3 across wrap
    assert r.cmds() == ["+1"] * 3
    r.feed(status(enc=-32766))
    assert r.cmds()[-1] == "+1"


def test_boot_rebases():
    r = Rig()
    r.feed(enc(50), boot(), enc(0), enc(1))
    assert r.cmds() == ["BOOT", "+1"]


# ---- wiring into dashboard -------------------------------------------------

def _funcs():
    t = ast.parse(DASH.read_text(encoding="utf-8"))
    return {n.name: n for n in t.body if isinstance(n, ast.FunctionDef)}


def test_serial_and_can_share_one_dispatcher():
    f = _funcs()
    for name in ("serial_listener", "can_switchbox_listener"):
        src = ast.unparse(f[name])
        assert "handle_switchbox_line(" in src
    assert "exit_confirm_window" not in ast.unparse(f["serial_listener"])
    assert "exit_confirm_window" in ast.unparse(f["_handle_switchbox_line_locked"])


def test_can_listener_retries_instead_of_crashing():
    src = ast.unparse(_funcs()["can_switchbox_listener"])
    assert "except (OSError, AttributeError)" in src and "time.sleep(retry)" in src
    assert "while True" in src


def test_heartbeat_timeout_configurable():
    import config
    assert config.SWITCHBOX_HEARTBEAT_TIMEOUT == 3.0
    assert "SWITCHBOX_HEARTBEAT_TIMEOUT" in DASH.read_text(encoding="utf-8")


def _exec_dispatcher(**state):
    """Run the real dispatcher with stub handlers (no tkinter)."""
    f = _funcs()
    calls = []
    ns = dict(state)
    ns.update(time=__import__("time"), print=lambda *a, **k: None,
              append_debug_log=lambda *a: None, log_serial_debug=lambda *a: None,
              _log_ov_diag=lambda *a: None, should_ignore_menu_ov_bounce=lambda *a: False,
              handle_box_pump_stop_button=lambda why: calls.append(("ack_reset", why)),
              start_pump_stop_thread=lambda d: calls.append(("pump_stop", d)))
    code = ast.Module(body=[f["_handle_switchbox_line_locked"]], type_ignores=[])
    exec(compile(code, "dashboard.py", "exec"), ns)
    return ns, calls


def test_can_ps_reaches_physical_reset_ack_path():
    ns, calls = _exec_dispatcher(physical_reset_safety_active=True, last_heartbeat_time=0,
                                 last_operator_input_ts=0)
    r = Rig()
    r.c.fill_mix = 0  # FILL already latched; exercise heartbeat + PS only
    r.c.dispatch = lambda tok: ns["_handle_switchbox_line_locked"](tok, "CAN", None)
    r.feed(status(), ps(1, 1, 0), ps(1, 1, 1))
    assert ns["last_heartbeat_time"] > 0
    assert [c[0] for c in calls].count("ack_reset") == 1
