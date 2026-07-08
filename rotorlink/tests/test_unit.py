#!/usr/bin/env python3
"""Offline unit tests for rotorlink protocol + arbitration logic (no network,
no dashboard, no equipment).  Run:  python3 rotorlink/tests/test_unit.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import rotorlink.server as srv
from rotorlink import protocol

fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# --- protocol.decode: forward-compat / malformed handling ---
check("decode valid", protocol.decode('{"type":"ping"}') == {"type": "ping"})
check("decode extra fields kept", protocol.decode('{"type":"command","command":"X","future":42}')["future"] == 42)
check("decode malformed -> None", protocol.decode("{not json") is None)
check("decode no type -> None", protocol.decode('{"foo":1}') is None)
check("decode non-dict -> None", protocol.decode('[1,2,3]') is None)
check("decode non-string type -> None", protocol.decode('{"type":5}') is None)

# --- command verb parsing ---
check("verb plain", srv._command_verb("STATE_JSON") == "STATE_JSON")
check("verb args", srv._command_verb("SET_REQUESTED_GALLONS:12.5") == "SET_REQUESTED_GALLONS")
check("verb lowercase->upper", srv._command_verb("stop") == "STOP")

# --- authorization (arbitration) ---
s = srv.RotorLinkServer()
viewer = srv.ClientState(None)
controller = srv.ClientState(None)

# arbitration OFF: everything allowed
srv.ARBITRATION = False
check("arb off: control allowed", s._authorize(viewer, "MODE") is True)

# arbitration ON
srv.ARBITRATION = True
s._controller = controller
check("arb on: read always allowed", s._authorize(viewer, "STATE_JSON") is True)
check("arb on: emergency STOP allowed for anyone", s._authorize(viewer, "STOP") is True)
check("arb on: control denied to non-controller", s._authorize(viewer, "MODE") is False)
check("arb on: control allowed to controller", s._authorize(controller, "MODE") is True)

# controller auto-claim when none set (undeclared role)
s2 = srv.RotorLinkServer()
srv.ARBITRATION = True
s2._controller = None
check("arb on: first commander claims control", s2._authorize(viewer, "MODE") is True)
check("arb on: second commander denied", s2._authorize(controller, "MODE") is False)

# an explicitly-declared viewer is never auto-promoted, even if it commands first
s3 = srv.RotorLinkServer()
srv.ARBITRATION = True
s3._controller = None
explicit_viewer = srv.ClientState(None)
explicit_viewer.role = "viewer"
check("arb on: explicit viewer never auto-promoted", s3._authorize(explicit_viewer, "MODE") is False)
check("arb on: explicit viewer still allowed read", s3._authorize(explicit_viewer, "STATE_JSON") is True)

srv.ARBITRATION = False  # restore

# --- network manager: stranded-in-AP join threshold ---
from rotorlink import network_manager as nm
check("sta holds down to DROP", nm.join_threshold("sta", None) == nm.STA_DROP_SIGNAL)
check("ap normally needs JOIN", nm.join_threshold("ap", None) == nm.STA_JOIN_SIGNAL)
check("ap idle but not stranded yet", nm.join_threshold("ap", nm.STA_STRANDED_AFTER - 1) == nm.STA_JOIN_SIGNAL)
check("stranded ap relaxes to STRANDED_JOIN",
      nm.join_threshold("ap", nm.STA_STRANDED_AFTER) == max(nm.STA_STRANDED_JOIN_SIGNAL, nm.STA_DROP_SIGNAL + 2))
check("stranded bar stays above DROP", nm.join_threshold("ap", nm.STA_STRANDED_AFTER) > nm.STA_DROP_SIGNAL)
check("unknown mode uses JOIN when not stranded", nm.join_threshold("unknown", None) == nm.STA_JOIN_SIGNAL)

print("\nUNIT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
