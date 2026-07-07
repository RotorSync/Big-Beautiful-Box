"""Regression test: SELECT_TRAILER clear semantics over WiFi (rotorlink).

Field report 2026-07-07: the app's Unassign Trailer sent SELECT_TRAILER
trailer=0 over WiFi and rotorlink answered 'Trailer 0 not found' — its
handler lacked the clear branch bumble has (_is_clear_trailer_value). The
box could be unassigned over BLE but not over WiFi.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rotorlink import config_handler as ch


def test_clear_values_recognized():
    for v in (None, '', '0', 0, 'none', 'NULL', ' Clear ', 'unassigned'):
        assert ch._is_clear_trailer_value(v), repr(v)
    for v in (1, '12', 'TR3'):
        assert not ch._is_clear_trailer_value(v), repr(v)


def test_clear_writes_unconfigured_state(monkeypatch):
    saved = {}
    monkeypatch.setattr(ch, '_load_config', lambda: {
        'box_mode': 'fleet', 'assigned_trailer': 11, 'trailer': 11,
        'display_name': 'TrailerSync-TR11', 'front_id': 'AA', 'back_id': 'BB',
    })
    monkeypatch.setattr(ch, '_save_config', lambda cfg: saved.update(cfg))
    assert ch._clear_trailer_assignment() is None
    assert saved['assigned_trailer'] is None
    assert saved['trailer'] is None
    assert saved['display_name'] == ''
    assert saved['front_id'] == '' and saved['back_id'] == ''
    assert saved['box_mode'] == 'fleet'  # never clobber the mode


def test_select_trailer_zero_returns_ok(monkeypatch):
    monkeypatch.setattr(ch, '_box_mode_uses_trailer_list', lambda: True)
    monkeypatch.setattr(ch, '_clear_trailer_assignment', lambda: None)
    handler = ch.ConfigHandler.__new__(ch.ConfigHandler)
    response = handler._select_trailer({'op': 'SELECT_TRAILER', 'trailer': 0}, 'req-1')
    assert response['ok'] is True
    assert response['op'] == 'SELECT_TRAILER'
    assert response['request_id'] == 'req-1'


def test_select_trailer_real_number_still_looks_up(monkeypatch):
    monkeypatch.setattr(ch, '_box_mode_uses_trailer_list', lambda: True)
    monkeypatch.setattr(ch, '_apply_trailer', lambda n: None)  # not in CSV
    handler = ch.ConfigHandler.__new__(ch.ConfigHandler)
    response = handler._select_trailer({'op': 'SELECT_TRAILER', 'trailer': 99}, 'req-2')
    assert response['ok'] is False
    assert 'not found' in response['error']
