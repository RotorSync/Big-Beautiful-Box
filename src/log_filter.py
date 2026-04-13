#!/usr/bin/env python3
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


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: log_filter.py <logfile>", file=sys.stderr)
        return 2

    log_path = Path(sys.argv[1])
    log_path.parent.mkdir(parents=True, exist_ok=True)

    noisy_state = {}

    def flush_noisy(handle, key=None, now=None):
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
                handle.write(format_summary(state["sample"], repeats))
                handle.flush()
            noisy_state.pop(current_key, None)

    with log_path.open("a", buffering=1) as handle:
        for raw_line in sys.stdin:
            line = raw_line if raw_line.endswith("\n") else raw_line + "\n"
            now = time.time()

            if not is_noisy(line):
                flush_noisy(handle)
                handle.write(line)
                handle.flush()
                continue

            key = normalize(line)
            state = noisy_state.get(key)
            if state is None:
                noisy_state[key] = {"sample": line, "count": 1, "first": now}
                handle.write(line)
                handle.flush()
                continue

            state["count"] += 1
            if state["count"] >= SUMMARY_REPEAT_THRESHOLD or (now - state["first"]) >= SUMMARY_INTERVAL_SEC:
                flush_noisy(handle, key=key, now=now)
                noisy_state[key] = {"sample": line, "count": 1, "first": now}
                handle.write(line)
                handle.flush()

        flush_noisy(handle)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
