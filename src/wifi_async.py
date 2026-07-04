"""Non-blocking WiFi control for the dashboard's :9999 command listener.

The listener processes commands serially on one thread. Every command is
fast except the two WiFi ops, which shell out to nmcli: a status query can
take ~8s and a connect up to ~31s (profile delete + `nmcli --wait 20`,
timeout 25). Run inline, they park EVERY following command -- including a
pump command from another client -- behind the radio work.

This module moves only the nmcli work onto daemon threads while keeping
the listener itself serial:

  * status(): serves a cached snapshot; when stale it kicks (or joins) a
    single background refresh and waits a bounded time for it, so the
    listener never stalls more than ``status_wait_seconds``.
  * request_connect(): starts the connect on a background thread and
    returns immediately with code=ACCEPTED (or BUSY while one is already
    running). The outcome lands in status() as ``last_connect`` and the
    status cache is refreshed when the attempt finishes.

Callers never saw slow replies anyway -- the box side gives up after 2s --
so serving cache/ACCEPTED is strictly more truthful than the old inline
behavior. Nothing here touches dashboard/Tk state: injected functions run
nmcli and return plain dicts.
"""

import threading
import time


class AsyncWifiControl:
    def __init__(
        self,
        status_fn,
        connect_fn,
        *,
        cache_fresh_seconds=8.0,
        status_wait_seconds=1.5,
        now=time.time,
    ):
        self._status_fn = status_fn
        self._connect_fn = connect_fn
        self._cache_fresh_seconds = cache_fresh_seconds
        self._status_wait_seconds = status_wait_seconds
        self._now = now

        self._lock = threading.Lock()
        self._status_cache = None
        self._status_cache_at = 0.0
        self._refresh_done = None  # Event for the in-flight refresh, if any
        self._connect_thread = None
        self._connect_ssid = ''
        self._last_connect_result = None

    def _start_daemon(self, target, name, args=()):
        """Start a daemon thread; None (never raises) if the OS refuses.

        Thread creation can fail on a long-running Pi (thread/memory
        exhaustion). Callers must treat failure as 'work not started' and
        leave no state pointing at it -- a wedged Event here would freeze
        WiFi status until a dashboard restart."""
        thread = threading.Thread(target=target, args=args, daemon=True, name=name)
        try:
            thread.start()
        except Exception:
            return None
        return thread

    # -- status -----------------------------------------------------------

    def status(self):
        """Current WiFi status, never blocking past status_wait_seconds."""
        with self._lock:
            cached = self._status_cache
            fresh = (
                cached is not None
                and (self._now() - self._status_cache_at) < self._cache_fresh_seconds
            )
        if not fresh:
            done = self._kick_or_join_refresh()
            done.wait(self._status_wait_seconds)

        with self._lock:
            base = dict(self._status_cache) if self._status_cache is not None else None
            connecting = (
                self._connect_thread is not None and self._connect_thread.is_alive()
            )
            target_ssid = self._connect_ssid
            last = self._last_connect_result

        if base is None:
            base = {'ok': False, 'connected': False, 'ssid': '', 'ip': '', 'pending': True}
        if connecting:
            base['connecting'] = True
            base['target_ssid'] = target_ssid
        if last is not None:
            base['last_connect'] = {
                'ok': bool(last.get('ok')),
                'code': last.get('code', ''),
            }
        return base

    def _kick_or_join_refresh(self):
        with self._lock:
            if self._refresh_done is not None:
                return self._refresh_done
            done = threading.Event()
            self._refresh_done = done
        if self._start_daemon(self._run_refresh, 'wifi-status-refresh', (done,)) is None:
            with self._lock:
                if self._refresh_done is done:
                    self._refresh_done = None
            done.set()
        return done

    def _run_refresh(self, done):
        try:
            status = self._status_fn()
        except Exception as e:
            status = {'ok': False, 'connected': False, 'error': str(e)}
        with self._lock:
            self._status_cache = status
            self._status_cache_at = self._now()
            self._refresh_done = None
        done.set()

    # -- connect ----------------------------------------------------------

    def request_connect(self, ssid, password, hidden=False):
        """Start a connect attempt in the background.

        Returns {'ok': True, 'code': 'ACCEPTED', ...} when started, or
        {'ok': False, 'code': 'BUSY', ...} while a previous attempt runs.
        """
        with self._lock:
            if self._connect_thread is not None and self._connect_thread.is_alive():
                return {
                    'ok': False,
                    'code': 'BUSY',
                    'message': f'Connect to {self._connect_ssid!r} already in progress',
                }
            self._connect_ssid = str(ssid or '').strip()
            self._last_connect_result = None
        thread = self._start_daemon(
            self._run_connect, 'wifi-connect', (ssid, password, hidden)
        )
        if thread is None:
            with self._lock:
                self._last_connect_result = {
                    'ok': False,
                    'code': 'THREAD_ERROR',
                    'message': 'could not start connect worker',
                }
            return {
                'ok': False,
                'code': 'NMCLI_ERROR',
                'message': 'could not start connect worker',
            }
        with self._lock:
            self._connect_thread = thread
        return {
            'ok': True,
            'code': 'ACCEPTED',
            'pending': True,
            'ssid': str(ssid or ''),
            'message': 'Connect started; poll WIFI_STATUS for the result',
        }

    def _run_connect(self, ssid, password, hidden):
        try:
            result = self._connect_fn(ssid, password, hidden)
            if not isinstance(result, dict):
                result = {'ok': False, 'code': 'NMCLI_ERROR', 'message': 'no result'}
        except Exception as e:
            result = {'ok': False, 'code': 'NMCLI_ERROR', 'message': str(e)}
        with self._lock:
            self._last_connect_result = result
        # The radio state just changed. Refresh the cache on a detached
        # thread so BUSY clears as soon as nmcli returns, and make sure the
        # sample lands AFTER any refresh that started before/during the
        # connect (joining one of those would stamp pre-connect state as
        # fresh for cache_fresh_seconds).
        self._start_daemon(self._post_connect_refresh, 'wifi-post-connect-refresh')

    def _post_connect_refresh(self):
        with self._lock:
            inflight = self._refresh_done
        if inflight is not None:
            inflight.wait(9.0)
        with self._lock:
            self._status_cache_at = 0.0
        self._kick_or_join_refresh()
