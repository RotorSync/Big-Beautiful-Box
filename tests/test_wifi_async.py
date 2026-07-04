"""Tests for src/wifi_async.py — nmcli work off the serial :9999 listener.

The regression being prevented: WIFI_SET ran `nmcli --wait 20` (up to ~31s
with the profile delete) inline in the dashboard's single-threaded command
listener, so a pump command from another client queued behind the radio
work. The async control must (a) never block status() past its bounded
wait even while a connect hangs, (b) reject concurrent connects with BUSY,
(c) surface the finished attempt's outcome, and (d) coalesce concurrent
status refreshes into one underlying nmcli call.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.wifi_async import AsyncWifiControl


def make_control(status_fn, connect_fn, **kw):
    kw.setdefault('cache_fresh_seconds', 8.0)
    kw.setdefault('status_wait_seconds', 0.3)
    return AsyncWifiControl(status_fn, connect_fn, **kw)


def test_status_returns_fast_result_inline():
    control = make_control(
        lambda: {'ok': True, 'connected': True, 'ssid': 'Shop', 'ip': '1.2.3.4'},
        lambda *a: {'ok': True},
    )
    status = control.status()
    assert status['connected'] is True
    assert status['ssid'] == 'Shop'


def test_status_bounded_when_nmcli_hangs():
    release = threading.Event()

    def slow_status():
        release.wait(30)
        return {'ok': True, 'connected': False, 'ssid': ''}

    control = make_control(slow_status, lambda *a: {'ok': True})
    started = time.monotonic()
    status = control.status()
    elapsed = time.monotonic() - started
    release.set()
    assert elapsed < 1.0, f'status() blocked {elapsed:.2f}s'
    assert status.get('pending') is True
    assert status['ok'] is False


def test_status_served_from_cache_when_fresh():
    calls = []

    def counting_status():
        calls.append(1)
        return {'ok': True, 'connected': True, 'ssid': 'Shop'}

    control = make_control(counting_status, lambda *a: {'ok': True})
    control.status()
    control.status()
    control.status()
    assert len(calls) == 1, f'expected 1 nmcli status call, saw {len(calls)}'


def test_concurrent_status_calls_share_one_refresh():
    gate = threading.Event()
    calls = []

    def gated_status():
        calls.append(1)
        gate.wait(5)
        return {'ok': True, 'connected': True, 'ssid': 'Shop'}

    control = make_control(gated_status, lambda *a: {'ok': True},
                           status_wait_seconds=2.0)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(control.status()))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join(5)
    assert len(calls) == 1, f'refresh not coalesced: {len(calls)} calls'
    assert all(r.get('ssid') == 'Shop' for r in results)


def test_connect_does_not_block_and_reports_busy_then_result():
    release = threading.Event()

    def slow_connect(ssid, password, hidden):
        release.wait(30)
        return {'ok': False, 'code': 'AUTH_FAILED', 'message': 'bad password'}

    control = make_control(
        lambda: {'ok': True, 'connected': False, 'ssid': ''},
        slow_connect,
    )

    started = time.monotonic()
    first = control.request_connect('Hangar', 'pw', False)
    accept_elapsed = time.monotonic() - started
    assert accept_elapsed < 0.5, f'request_connect blocked {accept_elapsed:.2f}s'
    assert first['code'] == 'ACCEPTED' and first['ok'] is True

    # While the connect hangs: status() stays fast and shows progress.
    started = time.monotonic()
    status = control.status()
    assert time.monotonic() - started < 1.0
    assert status.get('connecting') is True
    assert status.get('target_ssid') == 'Hangar'

    # A second connect while one runs is refused, not queued.
    second = control.request_connect('Other', 'pw', False)
    assert second['ok'] is False and second['code'] == 'BUSY'

    # Let it finish: the outcome must surface via status().
    release.set()
    deadline = time.time() + 5
    last = None
    while time.time() < deadline:
        last = control.status().get('last_connect')
        if last is not None:
            break
        time.sleep(0.05)
    assert last == {'ok': False, 'code': 'AUTH_FAILED'}

    # And a new attempt is allowed again.
    third = control.request_connect('Hangar', 'pw2', False)
    assert third['code'] == 'ACCEPTED'
    release.set()


def test_connect_exception_is_captured_not_raised():
    def exploding_connect(ssid, password, hidden):
        raise RuntimeError('nmcli missing')

    control = make_control(
        lambda: {'ok': True, 'connected': False, 'ssid': ''},
        exploding_connect,
    )
    accepted = control.request_connect('X', '', False)
    assert accepted['code'] == 'ACCEPTED'
    deadline = time.time() + 5
    last = None
    while time.time() < deadline:
        last = control.status().get('last_connect')
        if last is not None:
            break
        time.sleep(0.05)
    assert last is not None and last['ok'] is False


def test_status_fn_exception_becomes_error_status():
    def exploding_status():
        raise RuntimeError('nmcli missing')

    control = make_control(exploding_status, lambda *a: {'ok': True},
                           status_wait_seconds=2.0)
    status = control.status()
    assert status['ok'] is False
    assert 'error' in status
