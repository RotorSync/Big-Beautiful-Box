"""Set the Pi clock from a client_hello time — shared by BLE and WiFi paths.

The box often boots with no internet (field trailer): no NTP, clock wrong,
fill-history timestamps wrong. The app stamps a wall-clock `time` into every
client_hello; when the kernel clock has never been disciplined by NTP, that
time is the best source available and we set the clock from it.

Rules (accuracy-first, identical to the original BLE implementation in
rotorsync_bumble._maybe_apply_hello_time):
  * No/invalid/out-of-range time in the hello -> do nothing.
  * Kernel clock NTP-synchronized (adjtimex) -> NTP is authoritative; never
    touch the clock, only log large disagreements.
  * Clock already set from a hello this boot -> do nothing. The latch is a
    marker file in /run (tmpfs, clears on reboot) so the BLE server
    (rotorsync_bumble) and the WiFi server (rotorlink) can't both step the
    clock in one boot.
  * Otherwise set the clock (needs root/CAP_SYS_TIME; both services run as
    root) and log what changed.
"""

import ctypes
import ctypes.util
import os
import time

TIME_SYNC_MIN_EPOCH = 1704067200.0  # 2024-01-01: anything earlier is garbage
TIME_SYNC_MAX_EPOCH = 2051222400.0  # 2035-01-01: anything later is garbage
TIME_SYNC_DISCREPANCY_LOG_SECONDS = 120.0

CLOCK_SET_MARKER_PATH = '/run/rotorsync-hello-clock-set'

_STA_UNSYNC = 0x0040
_TIME_ERROR = 5


class _Timex(ctypes.Structure):
    # struct timex (Linux). We only read .status plus the adjtimex() return
    # code, but the full layout must match so the kernel writes status to the
    # right slot.
    _fields_ = [
        ("modes", ctypes.c_int), ("offset", ctypes.c_long), ("freq", ctypes.c_long),
        ("maxerror", ctypes.c_long), ("esterror", ctypes.c_long), ("status", ctypes.c_int),
        ("constant", ctypes.c_long), ("precision", ctypes.c_long), ("tolerance", ctypes.c_long),
        ("time_sec", ctypes.c_long), ("time_usec", ctypes.c_long), ("tick", ctypes.c_long),
        ("ppsfreq", ctypes.c_long), ("jitter", ctypes.c_long), ("shift", ctypes.c_int),
        ("stabil", ctypes.c_long), ("jitcnt", ctypes.c_long), ("calcnt", ctypes.c_long),
        ("errcnt", ctypes.c_long), ("stbcnt", ctypes.c_long), ("tai", ctypes.c_int),
        ("pad", ctypes.c_int * 11),
    ]


def kernel_clock_is_synchronized():
    """True if the kernel considers the system clock NTP-synchronized.

    Daemon-agnostic (chrony/ntpd/timesyncd): reads adjtimex(). On any error we
    conservatively report False (clock untrustworthy) so the hello can help.
    """
    try:
        libc = ctypes.CDLL(ctypes.util.find_library('c') or 'libc.so.6', use_errno=True)
        t = _Timex()
        ret = libc.adjtimex(ctypes.byref(t))
        if ret < 0 or ret == _TIME_ERROR:
            return False
        return not bool(t.status & _STA_UNSYNC)
    except Exception:
        return False


def clock_already_set_this_boot(marker_path=CLOCK_SET_MARKER_PATH):
    return os.path.exists(marker_path)


def mark_clock_set(marker_path=CLOCK_SET_MARKER_PATH):
    try:
        with open(marker_path, 'w') as f:
            f.write(str(time.time()))
    except Exception as e:
        # Not worth failing the hello over, but a broken latch means both
        # servers may step the clock repeatedly — make it visible.
        print(
            f'hello_time: could not write clock-set marker {marker_path} '
            f'({type(e).__name__}: {e}); once-per-boot latch inactive',
            flush=True,
        )


def maybe_apply_hello_time(
    command,
    who,
    log,
    *,
    now_fn=time.time,
    set_clock=None,
    is_synchronized=None,
    marker_path=CLOCK_SET_MARKER_PATH,
):
    """Apply a client_hello's wall-clock time per the rules above.

    command: the hello dict (reads `time`, falling back to `epoch`).
    who: short human string for logs ("Norman (iPhone) via wifi").
    log: callable taking one string; only called for meaningful events.
    Returns a short action string for callers/tests:
      'applied' | 'ntp-authoritative' | 'already-set' | 'no-time' |
      'bad-time' | 'failed'
    """
    set_clock = set_clock or (lambda epoch: time.clock_settime(time.CLOCK_REALTIME, epoch))
    is_synchronized = is_synchronized if is_synchronized is not None else kernel_clock_is_synchronized

    raw = command.get('time')
    if raw is None:
        raw = command.get('epoch')
    if raw is None:
        log(f'client_hello from {who} carried no time field; clock unchanged')
        return 'no-time'

    try:
        epoch = float(raw)
    except (TypeError, ValueError):
        log(f'Ignoring client_hello time: not a number ({raw!r})')
        return 'bad-time'

    if not (TIME_SYNC_MIN_EPOCH <= epoch <= TIME_SYNC_MAX_EPOCH):
        log(f'Ignoring client_hello time {epoch}: outside sane range')
        return 'bad-time'

    delta = epoch - now_fn()

    if is_synchronized():
        if abs(delta) >= TIME_SYNC_DISCREPANCY_LOG_SECONDS:
            log(
                f'Time DISCREPANCY (clock kept, NTP authoritative): app time '
                f'differs by {delta:+.1f}s. App said '
                f'{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))} from {who}'
            )
        return 'ntp-authoritative'

    if clock_already_set_this_boot(marker_path):
        log(f'client_hello time from {who} ignored: clock already set this boot')
        return 'already-set'

    try:
        set_clock(epoch)
    except PermissionError:
        log(
            f'FAILED to set clock (need root/CAP_SYS_TIME). Wanted '
            f'{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))} from {who}'
        )
        return 'failed'
    except Exception as e:
        log(f'FAILED to set clock ({type(e).__name__}: {e}) from {who}')
        return 'failed'

    mark_clock_set(marker_path)
    new_local = time.strftime('%A %Y-%m-%d %H:%M:%S %Z', time.localtime(epoch))
    log(
        f'Clock set to {new_local} (corrected {delta:+.1f}s, '
        f'source=client_hello) by {who}'
    )
    return 'applied'
