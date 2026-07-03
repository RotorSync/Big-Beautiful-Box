"""Regression tests for WiFi pilot identity (field report: loads uploaded via a
RotorLink WiFi link lost the pilot name).

Two gaps are pinned here:
  * rotorlink's fill-history parser drifted from bumble's and dropped the
    Pilot / FlowStart / FlowEnd fields, so loads fetched over WiFi reached the
    backend without pilot attribution even when the box had stamped it.
  * rotorlink never told the dashboard a pilot was connected, so loads filled
    while the pilot was on WiFi were stamped "Unknown". The server now pushes
    WIFI_PILOT_CONNECTED/WIFI_PILOT_DISCONNECTED (distinct verbs from bumble's
    PILOT_* so a WiFi drop can never clear a still-connected BLE pilot).
"""
import asyncio

from rotorlink.config_handler import _fill_history_item_from_line
from rotorlink.server import RotorLinkServer, ClientState, _sanitize_pilot_name


FULL_LINE = (
    "2026-07-03 09:15:22 | Requested: 60.000 gal | Actual: 65.040 gal"
    " | Diff: +5.040 gal | Auto shutoff | Temp: 71.2F | StopToThumb: 3.2s"
    " | FlowStart: 2026-07-03 09:12:01 | FlowEnd: 2026-07-03 09:14:55"
    " | Pilot: Cody"
)
LEGACY_LINE = (
    "2026-07-03 09:15:22 | Requested: 60.000 gal | Actual: 65.040 gal"
    " | Diff: +5.040 gal | Auto shutoff | Temp: 71.2F"
)


def test_history_item_carries_pilot_and_flow_window():
    item = _fill_history_item_from_line(FULL_LINE)
    assert item["pl"] == "Cody"
    assert item["fs"] is not None and item["fe"] is not None
    assert item["fe"] > item["fs"]


def test_history_item_tolerates_legacy_lines():
    item = _fill_history_item_from_line(LEGACY_LINE)
    assert item is not None
    assert item["pl"] is None
    assert item["fs"] is None and item["fe"] is None


class _FakeWS:
    remote_address = ("10.42.0.2", 5555)


class _FakeDashboard:
    def __init__(self):
        self.commands = []

    async def send_command(self, cmd):
        self.commands.append(cmd)
        return "PILOT_OK"


def _server_with_clients(*states):
    server = RotorLinkServer.__new__(RotorLinkServer)
    server.dashboard = _FakeDashboard()
    server.clients = {object(): s for s in states}
    server._last_pushed_pilot = None
    return server


def _client(role, user, hello_at=1.0):
    state = ClientState(_FakeWS())
    state.role = role
    state.user = user
    state.hello_at = hello_at
    return state


def test_pilot_hello_pushes_wifi_pilot_connected():
    server = _server_with_clients(_client("pilot", "Cody"))
    asyncio.run(server._push_pilot_status())
    assert server.dashboard.commands == ["WIFI_PILOT_CONNECTED:Cody"]


def test_push_only_on_change():
    server = _server_with_clients(_client("pilot", "Cody"))
    asyncio.run(server._push_pilot_status())
    asyncio.run(server._push_pilot_status())
    assert server.dashboard.commands == ["WIFI_PILOT_CONNECTED:Cody"]


def test_last_pilot_drop_pushes_disconnected():
    server = _server_with_clients(_client("pilot", "Cody"))
    asyncio.run(server._push_pilot_status())
    server.clients.clear()
    asyncio.run(server._push_pilot_status())
    assert server.dashboard.commands == [
        "WIFI_PILOT_CONNECTED:Cody",
        "WIFI_PILOT_DISCONNECTED:Cody",
    ]


def test_non_pilot_roles_never_push():
    server = _server_with_clients(
        _client("controller", "Ground Crew iPad"), _client("viewer", "Admin")
    )
    asyncio.run(server._push_pilot_status())
    assert server.dashboard.commands == []


def test_most_recent_pilot_hello_wins():
    server = _server_with_clients(
        _client("pilot", "Cody", hello_at=1.0), _client("pilot", "Norman", hello_at=2.0)
    )
    asyncio.run(server._push_pilot_status())
    assert server.dashboard.commands == ["WIFI_PILOT_CONNECTED:Norman"]


def test_pilot_name_sanitized_for_line_protocol():
    assert _sanitize_pilot_name("Co|dy\nX") == "Co/dy X"
    server = _server_with_clients(_client("pilot", "Co|dy\nX"))
    asyncio.run(server._push_pilot_status())
    assert server.dashboard.commands == ["WIFI_PILOT_CONNECTED:Co/dy X"]


def test_pilot_loc_update_forwarded_to_dashboard():
    pilot = _client("pilot", "Cody")
    server = _server_with_clients(pilot)
    asyncio.run(server._apply_loc(pilot, {"lat": 41.1234567, "lon": -95.65, "acc": 9.34}))
    assert server.dashboard.commands == ["WIFI_PILOT_LOC:41.123457,-95.65,9.3"]
    assert pilot.loc["lat"] == 41.123457


def test_non_pilot_loc_stored_but_not_forwarded():
    crew = _client("controller", "Ground Crew iPad")
    server = _server_with_clients(crew)
    asyncio.run(server._apply_loc(crew, {"lat": 1.0, "lon": 2.0}))
    assert server.dashboard.commands == []
    assert crew.loc == {"lat": 1.0, "lon": 2.0, "ts": crew.loc["ts"]}


def test_malformed_loc_ignored():
    pilot = _client("pilot", "Cody")
    server = _server_with_clients(pilot)
    asyncio.run(server._apply_loc(pilot, {"lat": "n/a", "lon": None}))
    asyncio.run(server._apply_loc(pilot, "41,-95"))
    assert pilot.loc is None
    assert server.dashboard.commands == []
