"""Tests for the LOCAL-ONLY AP dnsmasq drop-in (rotorlink.network_manager).

NetworkManager `ipv4.method shared` defaults to a dnsmasq that advertises the
box (10.42.0.1) as default gateway + DNS in its DHCP offers, so a joined iPad
routes its internet traffic into the AP (which has none) — field reports of
internet dying on the trailer WiFi. ensure_ap_profile() must keep a drop-in in
NM's shared-mode conf-dir that suppresses DHCP options 3/6 and disables the
DNS listener, and it must do so FAIL-SOFT: a broken drop-in path must never
cost the box its AP.
"""

import os

import pytest

from rotorlink import network_manager


@pytest.fixture
def mgr(monkeypatch, tmp_path):
    """A NetworkModeManager with nmcli faked (existing rotorlink-ap profile,
    SSID already in sync) and the drop-in redirected under tmp_path."""
    dropin = tmp_path / "dnsmasq-shared.d" / "rotorlink-local-only.conf"
    monkeypatch.setattr(network_manager, "DNSMASQ_SHARED_DROPIN", str(dropin))

    def fake_run(args, timeout=15):
        if args[:4] == ["nmcli", "-t", "-f", "NAME"]:
            return 0, "%s\nHeadings\n" % network_manager.AP_CON_NAME
        if "802-11-wireless.ssid" in args and args[-1] == network_manager.AP_CON_NAME:
            return 0, network_manager.AP_SSID + "\n"
        return 0, ""

    monkeypatch.setattr(network_manager, "_run", fake_run)
    m = network_manager.NetworkManager()
    m._dropin = dropin  # convenience for tests
    return m


def test_ensure_ap_profile_writes_local_only_dropin(mgr):
    assert mgr.ensure_ap_profile() is True
    content = mgr._dropin.read_text()
    # The three load-bearing lines: no router (3), no DNS (6), DHCP-only.
    assert "dhcp-option=3\n" in content
    assert "dhcp-option=6\n" in content
    assert "port=0\n" in content


def test_stale_dropin_is_resynced(mgr):
    mgr._dropin.parent.mkdir(parents=True)
    mgr._dropin.write_text("# old/edited content\n")
    assert mgr.ensure_ap_profile() is True
    assert mgr._dropin.read_text() == network_manager.DNSMASQ_SHARED_DROPIN_CONTENT


def test_up_to_date_dropin_is_left_alone(mgr):
    mgr._dropin.parent.mkdir(parents=True)
    mgr._dropin.write_text(network_manager.DNSMASQ_SHARED_DROPIN_CONTENT)
    os.utime(mgr._dropin, (1000000, 1000000))  # sentinel mtime
    assert mgr.ensure_ap_profile() is True
    assert os.stat(mgr._dropin).st_mtime == 1000000  # not rewritten


def test_unwritable_dropin_is_fail_soft(mgr, monkeypatch, tmp_path):
    # Point the drop-in "directory" at a regular FILE so mkdir/open fail.
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    monkeypatch.setattr(
        network_manager, "DNSMASQ_SHARED_DROPIN", str(blocker / "x.conf")
    )
    # The AP profile must still be reported usable — local-only is best-effort.
    assert mgr.ensure_ap_profile() is True
