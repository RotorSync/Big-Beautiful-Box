"""Diagnostics writes in button/menu handlers must never abort the handler.

Locks the guard for the documented fleet condition of a full or read-only SD
card (AGENTS.md pitfall #13): the button/menu debug logs used to be written
with bare open() inside the handlers, so an OSError mid-press could kill the
thumbs-up/green path before record_pending_fill() ran (lost load record) and
could silently break menu navigation from the physical switch box. All such
writes now go through append_debug_log(), which swallows I/O errors.
"""
import ast
import time
from pathlib import Path

import config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_PATH = PROJECT_ROOT / "dashboard.py"


class DiskFullOpen:
    """Stands in for open() on a full or read-only SD card."""

    def __init__(self):
        self.attempts = 0

    def __call__(self, *args, **kwargs):
        self.attempts += 1
        raise OSError(28, "No space left on device")


def _exec_namespace(names, ns):
    """Load only the wanted dashboard functions without starting the Tk app."""
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(DASHBOARD_PATH))
    module = ast.Module(
        body=[
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    exec(compile(module, str(DASHBOARD_PATH), "exec"), ns)
    return ns


def test_thumbs_up_green_path_survives_unwritable_debug_log():
    disk_full_open = DiskFullOpen()
    fills = []
    draws = []
    ns = {
        "config": config,
        "time": time,
        "open": disk_full_open,
        "calibration_mode": False,
        "last_flow_rate": 0.0,
        "serial_command_received": False,
        "last_totalizer_liters": 45.0 / config.LITERS_TO_GALLONS,
        "requested_gallons": 45.0,
        "colors_are_green": False,
        "batch_mix_layout_active": False,
        "thumbs_up_animation_id": None,
        "thumbs_up_label": None,
        "thumbs_up_frames": [],
        "log_serial_debug": lambda message: None,
        "record_pending_fill": lambda: fills.append(True),
        "target_display_color": lambda actual: "green",
        "draw_requested_number": lambda text, color: draws.append(("requested", text)),
        "draw_actual_number": lambda text, color: draws.append(("actual", text)),
    }
    _exec_namespace(
        {"append_debug_log", "handle_thumbs_up_press", "change_colors_to_green"},
        ns,
    )

    ns["handle_thumbs_up_press"]("GPIO button")

    assert disk_full_open.attempts > 0, "guard was never exercised"
    assert ns["colors_are_green"] is True
    assert fills == [True], "pending fill was not recorded"
    assert ("requested", "45") in draws


def test_thumbs_up_while_flowing_survives_unwritable_debug_log():
    disk_full_open = DiskFullOpen()
    fills = []
    ns = {
        "config": config,
        "time": time,
        "open": disk_full_open,
        "calibration_mode": False,
        "last_flow_rate": config.FLOW_STOPPED_THRESHOLD * 2,
        "log_serial_debug": lambda message: None,
        "change_colors_to_green": lambda from_button=False: None,
        "record_pending_fill": lambda: fills.append(True),
    }
    _exec_namespace({"append_debug_log", "handle_thumbs_up_press"}, ns)

    ns["handle_thumbs_up_press"]("serial TU")

    assert disk_full_open.attempts == 1
    assert fills == []


def test_menu_navigation_survives_unwritable_debug_log():
    disk_full_open = DiskFullOpen()
    redraws = []
    ns = {
        "time": time,
        "open": disk_full_open,
        "menu_selected_index": 0,
        "MENU_ITEMS": ["Logs", "Fill History", "Calibration"],
        "schedule_menu_highlight_update": lambda: redraws.append(True),
    }
    _exec_namespace({"append_debug_log", "menu_navigate_down", "menu_navigate_up"}, ns)

    ns["menu_navigate_down"]()
    assert ns["menu_selected_index"] == 1
    ns["menu_navigate_up"]()
    assert ns["menu_selected_index"] == 0

    assert disk_full_open.attempts == 2
    assert redraws == [True, True]


def test_menu_select_survives_unwritable_debug_log():
    disk_full_open = DiskFullOpen()
    opened = []
    ns = {
        "time": time,
        "open": disk_full_open,
        "menu_selected_index": 0,
        "MENU_ITEMS": ["Logs"],
        "show_log_viewer": lambda: opened.append("logs"),
    }
    _exec_namespace({"append_debug_log", "menu_select"}, ns)

    ns["menu_select"]()

    assert disk_full_open.attempts == 1
    assert opened == ["logs"]


def test_no_bare_opens_of_button_or_menu_debug_logs():
    """Every button/menu debug-log write must route through append_debug_log."""
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(DASHBOARD_PATH))
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "append_debug_log"
    )
    offenders = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "open"
        ):
            continue
        if helper.lineno <= node.lineno <= helper.end_lineno:
            continue
        arg = node.args[0] if node.args else None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            if "button_debug.log" in arg.value or "menu_debug.log" in arg.value:
                offenders.append(node.lineno)
        elif isinstance(arg, ast.Name) and arg.id == "button_log":
            offenders.append(node.lineno)
        elif (
            isinstance(arg, ast.Attribute)
            and arg.attr in ("BUTTON_DEBUG_LOG", "MENU_DEBUG_LOG")
            and isinstance(arg.value, ast.Name)
            and arg.value.id == "config"
        ):
            offenders.append(node.lineno)
    assert offenders == [], (
        f"bare open() of button/menu debug log at lines {offenders}; "
        "route it through append_debug_log so a full SD card cannot kill the handler"
    )
