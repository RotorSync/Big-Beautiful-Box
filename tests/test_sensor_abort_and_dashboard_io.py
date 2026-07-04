"""Regression tests for the TR2 pilot-kick crash + BLE loop-blocking fixes.

Crash (seen twice on trailersync-sn001 while Norman was connected,
2026-07-03 21:27:06 and 2026-07-04 08:32:33): bumble cancels in-flight GATT
futures when a sensor link drops (gatt_client.on_disconnection ->
pending_response.cancel()). That CancelledError escaped `except Exception`
in read_sensors, ended the sensor task as *cancelled*, and
_handle_sensor_task_done os._exit(1)'d the whole GATT server -- kicking
every connected pilot. _sensor_cancellation_is_external must tell that
leaked abort apart from a real shutdown cancellation.

Latency: dashboard socket calls used to run synchronously on the Bumble
event loop; a busy dashboard could stall ALL BLE for up to the 2s socket
timeout. They now run on the single-thread dashboard-io executor.
"""
import asyncio
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_maintenance_auth import install_bumble_stubs


class _SysModulesShim:
    """Minimal monkeypatch stand-in so the stub installer works standalone."""

    def setitem(self, mapping, key, value):
        mapping[key] = value


install_bumble_stubs(_SysModulesShim())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rotorsync_bumble as rb


def test_leaked_bumble_abort_is_not_external():
    """A CancelledError raised in the task without task.cancel() (bumble
    future abort) must NOT be treated as an external cancellation."""
    async def main():
        # Simulate the bumble abort: a future we await gets cancelled from
        # the outside while our task was never asked to cancel.
        fut = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_soon(fut.cancel)
        try:
            await fut
        except asyncio.CancelledError:
            return rb._sensor_cancellation_is_external()
        raise AssertionError('future was not cancelled')

    assert asyncio.run(main()) is False


def test_real_task_cancellation_is_external():
    """task.cancel() (shutdown) must still be recognized so it propagates."""
    async def child():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            if rb._sensor_cancellation_is_external():
                raise
            raise AssertionError('real cancel classified as internal abort')

    async def main():
        task = asyncio.create_task(child())
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(main()) is True


def test_run_dashboard_io_runs_off_loop_and_returns_value():
    seen = {}

    def fake_call(arg):
        seen['thread'] = threading.current_thread().name
        seen['arg'] = arg
        return 'RESP:' + arg

    async def main():
        return await rb.run_dashboard_io(fake_call, 'STATE_JSON')

    assert asyncio.run(main()) == 'RESP:STATE_JSON'
    assert seen['arg'] == 'STATE_JSON'
    assert seen['thread'].startswith('dashboard-io')


def test_submit_dashboard_io_runs_and_swallows_errors():
    done = threading.Event()

    def boom():
        done.set()
        raise RuntimeError('dashboard down')

    rb.submit_dashboard_io(boom)
    assert done.wait(5), 'submitted call never ran'
    # A second submit must still work (worker thread survived the error).
    ok = threading.Event()
    rb.submit_dashboard_io(ok.set)
    assert ok.wait(5), 'worker thread died after an exception'


class _FakeDevice:
    def __init__(self):
        self.l2cap_channel_manager = type(
            'FakeManager', (), {'connection_parameters_update_response': None}
        )()


def test_relax_connection_parameters_requests_l2cap_update():
    calls = {}

    class FakeConnection:
        async def update_parameters(self, imin, imax, latency, timeout, **kw):
            calls['args'] = (imin, imax, latency, timeout)
            calls['kw'] = kw

    async def main():
        old_delay = rb.GATT_RELAX_CONN_PARAMS_DELAY_SECONDS
        rb.GATT_RELAX_CONN_PARAMS_DELAY_SECONDS = 0
        rb.active_gatt_connections.add('AA:BB')
        try:
            await rb.relax_gatt_connection_parameters(
                _FakeDevice(), FakeConnection(), 'AA:BB'
            )
        finally:
            rb.GATT_RELAX_CONN_PARAMS_DELAY_SECONDS = old_delay
            rb.active_gatt_connections.discard('AA:BB')

    asyncio.run(main())
    assert calls['args'] == (30.0, 45.0, 0, 5000.0)
    assert calls['kw'].get('use_l2cap') is True


def test_relax_skips_already_disconnected_peer():
    class FakeConnection:
        async def update_parameters(self, *a, **kw):
            raise AssertionError('must not fire for a departed peer')

    async def main():
        old_delay = rb.GATT_RELAX_CONN_PARAMS_DELAY_SECONDS
        rb.GATT_RELAX_CONN_PARAMS_DELAY_SECONDS = 0
        rb.active_gatt_connections.discard('GONE')
        try:
            await rb.relax_gatt_connection_parameters(
                _FakeDevice(), FakeConnection(), 'GONE'
            )
        finally:
            rb.GATT_RELAX_CONN_PARAMS_DELAY_SECONDS = old_delay

    asyncio.run(main())


def test_relax_hang_times_out_and_unpoisons_slot():
    """A link drop mid-request used to hang the relax task forever AND leave
    bumble's single per-device response slot poisoned (every later request
    raises InvalidStateError). The relax must time out and clear a done
    (cancelled) future from the slot, while leaving a PENDING future (a
    concurrent legit request) untouched."""

    class HangingConnection:
        async def update_parameters(self, *a, **kw):
            await asyncio.sleep(3600)

    async def main():
        old_delay = rb.GATT_RELAX_CONN_PARAMS_DELAY_SECONDS
        rb.GATT_RELAX_CONN_PARAMS_DELAY_SECONDS = 0
        rb.active_gatt_connections.add('CC:DD')
        device = _FakeDevice()
        # Simulate the poisoned slot: wait_for's cancellation leaves a
        # cancelled (done) future behind.
        poisoned = asyncio.get_running_loop().create_future()
        poisoned.cancel()
        device.l2cap_channel_manager.connection_parameters_update_response = poisoned
        orig_wait_for = asyncio.wait_for

        async def fast_wait_for(awaitable, timeout):
            return await orig_wait_for(awaitable, timeout=0.05)

        asyncio.wait_for = fast_wait_for
        try:
            await rb.relax_gatt_connection_parameters(
                device, HangingConnection(), 'CC:DD'
            )
        finally:
            asyncio.wait_for = orig_wait_for
            rb.GATT_RELAX_CONN_PARAMS_DELAY_SECONDS = old_delay
            rb.active_gatt_connections.discard('CC:DD')
        assert (
            device.l2cap_channel_manager.connection_parameters_update_response
            is None
        ), 'done future not cleared from the slot'

        # A pending future (someone else's in-flight request) must survive.
        pending = asyncio.get_running_loop().create_future()
        device.l2cap_channel_manager.connection_parameters_update_response = pending
        rb.active_gatt_connections.add('CC:DD')
        asyncio.wait_for = fast_wait_for
        try:
            await rb.relax_gatt_connection_parameters(
                device, HangingConnection(), 'CC:DD'
            )
        finally:
            asyncio.wait_for = orig_wait_for
            rb.active_gatt_connections.discard('CC:DD')
        assert (
            device.l2cap_channel_manager.connection_parameters_update_response
            is pending
        ), 'pending (foreign) future must not be cleared'

    asyncio.run(main())


if __name__ == '__main__':
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print(f'PASS {name}')
            except Exception as e:
                print(f'FAIL {name}: {type(e).__name__}: {e}')
                fails.append(name)
    print('SENSOR-ABORT/DASH-IO:', 'ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}')
    sys.exit(1 if fails else 0)
