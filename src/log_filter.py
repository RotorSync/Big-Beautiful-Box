#!/usr/bin/env python3
import os
import re
import sys
import time
from pathlib import Path


NOISY_PATTERNS = (
    "iolink_14819_isr:",
    "Interrupt; REG =",
    ">>> rcv IOLINK_PL_EVENT_RXRDY",
    "generic_run0(..)",
    "status[cmd0.port].pdInLength=",
    "PDIn data is invalid for port",
    "Accept ok",
    "READ ",
    "buffer[0]=",
    "CMD_PD",
    "Close socket",
    "Listen on port 12011",
)

PREFIX_RE = re.compile(
    r"^\[(?P<ts>\d{2}:\d{2}:\d{2})\s+(?P<level>[A-Z]+)\]\s+"
)


def normalize(line: str) -> str:
    line = line.rstrip("\n")
    return PREFIX_RE.sub("", line).strip()


def format_summary(sample_line: str, repeats: int) -> str:
    m = PREFIX_RE.match(sample_line)
    level = (m.group("level") if m else "INFO").ljust(5)
    body = normalize(sample_line)
    return f"[{time.strftime('%H:%M:%S')} {level}] Suppressed {repeats} repeats of: {body}\n"


def is_noisy(line: str) -> bool:
    body = normalize(line)
    return any(pattern in body for pattern in NOISY_PATTERNS)


SUMMARY_INTERVAL_SEC = 60.0
SUMMARY_REPEAT_THRESHOLD = 25
# Env override exists so tests can exercise the reopen-recovery path quickly.
try:
    REOPEN_RETRY_SEC = float(os.environ.get("LOG_FILTER_REOPEN_SEC", "30"))
except ValueError:
    REOPEN_RETRY_SEC = 30.0


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: log_filter.py <logfile>", file=sys.stderr)
        return 2

    # This process is the reader end of the dashboard's stdout pipe. If it
    # dies, every print() in the dashboard raises BrokenPipeError; if it stops
    # reading, the pipe fills and print() blocks the Tk mainloop (both froze
    # the box display in the field while BLE kept working). So the two
    # invariants of this loop: never exit while stdin is open, and never let a
    # log-file problem (full/read-only SD) stop the stdin drain - drop lines
    # instead and retry the file later.
    try:
        sys.stdin.reconfigure(errors="replace")
    except Exception:
        pass

    log_path = Path(sys.argv[1])
    handle = None
    reopen_after = 0.0

    def ensure_handle(now):
        nonlocal handle, reopen_after
        if handle is not None or now < reopen_after:
            return
        reopen_after = now + REOPEN_RETRY_SEC
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handle = log_path.open("a", buffering=1)
        except Exception:
            handle = None

    def emit(text):
        nonlocal handle
        if handle is None:
            return
        try:
            handle.write(text)
            handle.flush()
        except Exception:
            # Write failed (ENOSPC/EROFS/...): drop the line, close the
            # handle, and let ensure_handle retry later.
            try:
                handle.close()
            except Exception:
                pass
            handle = None

    noisy_state = {}

    def flush_noisy(key=None, now=None):
        nonlocal noisy_state
        if now is None:
            now = time.time()

        keys = [key] if key is not None else list(noisy_state.keys())
        for current_key in keys:
            state = noisy_state.get(current_key)
            if not state:
                continue
            repeats = state["count"] - 1
            if repeats > 0:
                emit(format_summary(state["sample"], repeats))
            noisy_state.pop(current_key, None)

    ensure_handle(time.time())

    for raw_line in sys.stdin:
        line = raw_line if raw_line.endswith("\n") else raw_line + "\n"
        now = time.time()
        ensure_handle(now)

        try:
            if not is_noisy(line):
                flush_noisy(now=now)
                emit(line)
                continue

            key = normalize(line)
            state = noisy_state.get(key)
            if state is None:
                noisy_state[key] = {"sample": line, "count": 1, "first": now}
                emit(line)
                continue

            state["count"] += 1
            if state["count"] >= SUMMARY_REPEAT_THRESHOLD or (now - state["first"]) >= SUMMARY_INTERVAL_SEC:
                flush_noisy(key=key, now=now)
                noisy_state[key] = {"sample": line, "count": 1, "first": now}
                emit(line)
        except Exception:
            # A malformed line must never kill the drain loop.
            continue

    flush_noisy()
    if handle is not None:
        try:
            handle.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
