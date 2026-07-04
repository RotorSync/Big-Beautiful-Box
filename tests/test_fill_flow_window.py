"""Regression tests for the TR12 "FlowStart equals FlowEnd" load corruption.

Field report 2026-07-04 (Bruce's fleet, TR12): recent loads uploaded with
flowStartedAt == flowEndedAt (0-1s windows) while healthy 70-gal fills show
~51-54s. Root cause: after auto-shutoff, a brief post-shutoff dribble crossed
the flow threshold; its falling edge re-ran the pending-fill capture and
REPLACED the real fill's flow window (and shutoff type / FlowAtStop /
stop-to-thumb) with the dribble's. _is_fill_flow_continuation detects that
case so the dribble folds into the pending fill instead.
"""
import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DASHBOARD_PATH = Path(__file__).resolve().parents[1] / "dashboard.py"


def _namespace():
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(DASHBOARD_PATH))
    module = ast.Module(
        body=[
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_is_fill_flow_continuation"
        ],
        type_ignores=[],
    )
    namespace = {}
    exec(compile(module, str(DASHBOARD_PATH), "exec"), namespace)
    return namespace


CONT = _namespace()["_is_fill_flow_continuation"]


def test_post_shutoff_dribble_is_a_continuation():
    # 70-gal fill staged at 69.9; dribble creeps the totalizer to 70.3.
    assert CONT(69.9, 70.0, 1000.0, 70.0, 70.3) is True


def test_zero_extra_volume_is_a_continuation():
    assert CONT(70.0, 70.0, 1000.0, 70.0, 70.0) is True


def test_new_fill_after_reset_is_not_a_continuation():
    # Totalizer reset for the next load: actual restarts below the pending
    # gallons, so this is a fresh fill segment, not a dribble.
    assert CONT(70.0, 70.0, 1000.0, 70.0, 3.2) is False


def test_changed_target_is_not_a_continuation():
    assert CONT(70.0, 70.0, 1000.0, 24.0, 70.1) is False


def test_large_topoff_keeps_current_behavior():
    # More than max_extra_gallons since staging: a real second flow event.
    assert CONT(70.0, 70.0, 1000.0, 70.0, 75.5) is False


def test_no_pending_fill_is_not_a_continuation():
    assert CONT(0.0, 0.0, 0.0, 70.0, 70.1) is False
    # Pending existed but its flow start was never observed (box restarted
    # mid-fill): keep the existing replace behavior.
    assert CONT(69.9, 70.0, 0.0, 70.0, 70.1) is False


def test_garbage_inputs_are_not_a_continuation():
    assert CONT(None, 70.0, 1000.0, 70.0, 70.1) is False
    assert CONT("x", 70.0, 1000.0, 70.0, 70.1) is False
