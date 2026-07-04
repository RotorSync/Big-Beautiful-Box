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
        self._connect_started_at = 0.0
        self._last_connect_result = None

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
        thread = threading.Thread(
            target=self._run_refresh, args=(done,), daemon=True,
            name='wifi-status-refresh',
        )
        thread.start()
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
            self._connect_ssid = str(ssid or '')
            self._connect_started_at = self._now()
            self._last_connect_result = None
            thread = threading.Thread(
                target=self._run_connect,
                args=(ssid, password, hidden),
                daemon=True,
                name='wifi-connect',
            )
            self._connect_thread = thread
        thread.start()
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
        # The radio state just changed; refresh the cache so the next
        # WIFI_STATUS poll reflects the attempt's outcome.
        done = self._kick_or_join_refresh()
        done.wait(10.0)
