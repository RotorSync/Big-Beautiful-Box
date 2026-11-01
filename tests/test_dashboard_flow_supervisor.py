"""The pump-shutoff thread supervisor logs, latches safe state and restarts."""
import ast
import sys
import threading
import time
import traceback
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "dashboard.py").read_text()


def _load(names, ns):
    tree = ast.parse(SRC)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module([node], []), "dashboard.py", "exec"), ns)
    return ns


def test_supervisor_restarts_after_crash_and_latches_fault():
    calls, logs, holds = [], [], []
    stop = threading.Event()

    def loop():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("boom")
        stop.set()

    ns = {"time": time, "sys": sys, "traceback": traceback, "flow_control_stop_event": stop,
          "flow_control_loop": loop, "log_flow_control": logs.append,
          "set_pump_stop_fault_hold": lambda a, r: holds.append((a, r))}
    _load({"_flow_control_supervised"}, ns)
    ns["flow_control_stop_event"].wait = lambda t: None
    ns["_flow_control_supervised"]()
    assert len(calls) == 3
    assert sum("thread_crash" in m for m in logs) == 2
    assert holds and all(h[0] is True for h in holds)


def test_log_flow_control_reports_write_failures(capsys, tmp_path):
    class Cfg:
        FLOW_CONTROL_LOG_FILE = str(tmp_path / "nodir" / "x.log")
    ns = {"time": time, "sys": sys, "config": Cfg,
          "flow_control_log_write_failures": 0, "_flow_control_log_last_error_report": 0.0}
    _load({"log_flow_control"}, ns)
    ns["log_flow_control"]("a")
    ns["log_flow_control"]("b")
    assert ns["flow_control_log_write_failures"] == 2
    assert capsys.readouterr().err.count("flow_control.log write failed") == 1


def test_no_bare_excepts_left():
    assert not [l for l in SRC.splitlines() if l.strip() == "except:"]
