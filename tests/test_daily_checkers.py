"""The daily 1 AM total-reset and 2 AM reminder checkers must actually fire.

Locks the fix for a fleet-wide silent failure: daily_reminder_checker() read
`current_time.tm_minute` from a time.struct_time (the real attribute is
`tm_min`), so EVERY loop iteration raised AttributeError, the blanket
`except Exception` swallowed it ("Error in daily_reminder_checker:
'time.struct_time' object has no attribute 'tm_minute'" repeating in the
log), and the 2 AM reminders never showed on any box. The sibling
daily_total_checker uses datetime (`.hour`/`.minute`) and was not affected.

These tests execute one real iteration of each checker's loop body against a
REAL time.struct_time / datetime.datetime, so an invalid attribute access
fails the test exactly the way it failed in the field - and the "no swallowed
error" asserts catch it even if the checker still limps to its except path.
"""
import ast
import builtins as real_builtins
import datetime as real_datetime
import time as real_time
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_PATH = PROJECT_ROOT / "dashboard.py"


class LoopExit(BaseException):
    """Ends a checker's `while True` after one pass.

    Raised from the fake time.sleep(); derives from BaseException so the
    checkers' blanket `except Exception` (the swallow that hid the field
    bug) cannot eat it.
    """


class FakeTimeModule:
    """Stands in for the `time` module inside the extracted checkers.

    localtime() returns a REAL time.struct_time so an attribute typo
    (tm_minute) raises exactly like production; sleep() records the request
    and raises LoopExit so one iteration is one call.
    """

    def __init__(self, struct=None):
        self.struct = struct
        self.sleeps = []

    def localtime(self):
        return self.struct

    def strftime(self, fmt):
        return real_time.strftime(fmt, self.struct)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        raise LoopExit


class FakeRoot:
    def __init__(self):
        self.scheduled = []

    def after(self, delay, callback, *args):
        self.scheduled.append((delay, callback, args))
        return f"after#{len(self.scheduled)}"


def _extract(names):
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
    return compile(module, str(DASHBOARD_PATH), "exec")


def _struct_at(hour, minute):
    # 2026-07-10 (Friday, day 191) at hour:minute, via the real struct_time
    # type so only genuine tm_* attributes exist.
    return real_time.struct_time((2026, 7, 10, hour, minute, 0, 4, 191, -1))


def _run_one_iteration(checker):
    try:
        checker()
    except LoopExit:
        pass


def _swallowed_errors(prints, checker_name):
    return [p for p in prints if p.startswith(f"Error in {checker_name}")]


def _reminder_namespace(struct, last_reminder_date="", reminders_mode=False):
    fake_time = FakeTimeModule(struct)
    root = FakeRoot()
    prints = []
    show_daily_reminders = object()  # sentinel; the checker only schedules it
    ns = {
        "time": fake_time,
        "root": root,
        "print": lambda *args, **kwargs: prints.append(" ".join(str(a) for a in args)),
        "show_daily_reminders": show_daily_reminders,
        "last_reminder_date": last_reminder_date,
        "reminders_mode": reminders_mode,
    }
    exec(_extract({"daily_reminder_checker"}), ns)
    return ns, fake_time, root, prints, show_daily_reminders


def _total_namespace(now_dt, last_reset_date=""):
    """daily_total_checker does `import datetime` inside its loop, so the
    fake module is injected through __import__ rather than the namespace."""
    fake_time = FakeTimeModule()
    prints = []
    resets = []
    fake_datetime_module = types.SimpleNamespace(
        datetime=types.SimpleNamespace(now=lambda: now_dt)
    )

    def fake_import(name, *args, **kwargs):
        if name == "datetime":
            return fake_datetime_module
        return real_builtins.__import__(name, *args, **kwargs)

    patched_builtins = dict(vars(real_builtins))
    patched_builtins["__import__"] = fake_import

    ns = {
        "__builtins__": patched_builtins,
        "time": fake_time,
        "print": lambda *args, **kwargs: prints.append(" ".join(str(a) for a in args)),
        "reset_daily_total": lambda: resets.append(True),
        "last_reset_date": last_reset_date,
    }
    exec(_extract({"daily_total_checker"}), ns)
    return ns, fake_time, prints, resets


def test_reminder_checker_fires_show_daily_reminders_at_2am():
    ns, fake_time, root, prints, show = _reminder_namespace(_struct_at(2, 0))

    _run_one_iteration(ns["daily_reminder_checker"])

    assert root.scheduled == [(0, show, ())]
    assert fake_time.sleeps == [61]
    assert _swallowed_errors(prints, "daily_reminder_checker") == []


def test_reminder_checker_off_hour_iteration_is_clean():
    # The field bug errored on EVERY pass (error print + 60s error sleep),
    # not just at 2 AM: a clean pass sleeps 30 and schedules nothing.
    ns, fake_time, root, prints, _ = _reminder_namespace(_struct_at(14, 37))

    _run_one_iteration(ns["daily_reminder_checker"])

    assert root.scheduled == []
    assert fake_time.sleeps == [30]
    assert _swallowed_errors(prints, "daily_reminder_checker") == []


def test_reminder_checker_does_not_refire_same_day_or_while_reminders_open():
    already_shown = _reminder_namespace(_struct_at(2, 0), last_reminder_date="2026-07-10")
    reminders_open = _reminder_namespace(_struct_at(2, 0), reminders_mode=True)

    for ns, fake_time, root, prints, _ in (already_shown, reminders_open):
        _run_one_iteration(ns["daily_reminder_checker"])

        assert root.scheduled == []
        assert fake_time.sleeps == [61]
        assert _swallowed_errors(prints, "daily_reminder_checker") == []


def test_total_checker_resets_daily_total_at_1am():
    now = real_datetime.datetime(2026, 7, 10, 1, 0, 0)
    ns, fake_time, prints, resets = _total_namespace(now)

    _run_one_iteration(ns["daily_total_checker"])

    assert resets == [True]
    assert fake_time.sleeps == [61]
    assert _swallowed_errors(prints, "daily_total_checker") == []


def test_total_checker_off_hour_and_same_day_iterations_are_clean():
    off_hour = _total_namespace(real_datetime.datetime(2026, 7, 10, 14, 37, 0))
    already_reset = _total_namespace(
        real_datetime.datetime(2026, 7, 10, 1, 0, 0), last_reset_date="2026-07-10"
    )

    for (ns, fake_time, prints, resets), expected_sleep in (
        (off_hour, 30),
        (already_reset, 61),
    ):
        _run_one_iteration(ns["daily_total_checker"])

        assert resets == []
        assert fake_time.sleeps == [expected_sleep]
        assert _swallowed_errors(prints, "daily_total_checker") == []
