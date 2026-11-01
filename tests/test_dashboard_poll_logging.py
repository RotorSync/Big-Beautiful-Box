"""Poll responses (STATE_JSON/HISTORY) must not be logged every call."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_maintenance_auth import install_bumble_stubs


class _Shim:
    def setitem(self, mapping, key, value):
        mapping[key] = value


install_bumble_stubs(_Shim())
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rotorsync_bumble as rb


def test_state_json_poll_logging_is_rate_limited(capsys):
    rb._last_poll_log_at.clear()
    assert rb._log_dashboard_command('STATE_JSON', '{"x":1}', now=1000.0) is True
    for i in range(50):
        assert rb._log_dashboard_command('STATE_JSON', '{"x":1}', now=1000.0 + i) is False
    assert rb._log_dashboard_command('STATE_JSON', '{}', now=1000.0 + rb.DASHBOARD_POLL_LOG_INTERVAL) is True
    out = capsys.readouterr().out
    assert out.count('Dashboard command: STATE_JSON') == 2
    assert '{"x":1}' not in out


def test_non_poll_commands_still_logged_every_time(capsys):
    for _ in range(3):
        assert rb._log_dashboard_command('SET_TARGET 100', 'OK', now=5.0) is True
    assert capsys.readouterr().out.count('Dashboard command:') == 3
