"""Regression tests for cursor-less PAGE over RotorLink WiFi.

App builds <=81 send PAGE without cursor_request_id for the trailer/sensor/
calibration lists. bumble's _cmd_page falls back to the connection's last
page set (config_response_pages) in that case; rotorlink's ConfigHandler
originally served PAGE ONLY from the per-request cache, so multi-page lists
failed over WiFi with "No paginated data available" (field report 8dc3b3f9,
"Trailer assign error"). These tests pin the bumble-parity fallback:
  * cursor-less PAGE after a LIST serves from the last page set
  * the cursor path is untouched (survives a later list replacing the fallback)
  * cursor-less PAGE with no prior LIST still errors
  * any non-PAGE command clears the fallback (bumble clears
    config_response_pages in every non-paginated handler)
"""
import asyncio

import pytest

from rotorlink import config_handler


def _fake_sensor_rows(n_trailers=13):
    """Sensor CSV rows for n trailers (front+back Mopekas each), padded so the
    trailer list paginates into multiple 450-byte pages like a real fleet."""
    rows = []
    for i in range(1, n_trailers + 1):
        for tank in ("Front", "Back"):
            rows.append(
                {
                    "Trailer": str(i),
                    "Man": f"Manufacturer-Longname-{i:02d}",
                    "Tank": tank,
                    "Mopeka ID": f"aa:bb:cc:dd:ee:{i:02x}",
                }
            )
    return rows


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setattr(config_handler, "_load_sensor_csv", _fake_sensor_rows)
    monkeypatch.setattr(config_handler, "_box_mode_uses_trailer_list", lambda cfg=None: True)
    return config_handler.ConfigHandler(dashboard=None)


def _handle(handler, cmd):
    return asyncio.run(handler.handle(cmd))


def test_cursorless_page_serves_last_list_pages(handler):
    first = _handle(handler, {"op": "LIST_TRAILERS", "request_id": "r1"})
    assert "error" not in first
    assert first["total_pages"] >= 3, "test data must paginate to exercise PAGE"
    assert first["total_items"] == 13

    items = list(first["items"])
    for page_no in range(2, first["total_pages"] + 1):
        # Old-app behavior: PAGE with NO cursor_request_id.
        resp = _handle(handler, {"op": "PAGE", "page": page_no, "request_id": f"p{page_no}"})
        assert resp.get("ok") is not False, resp
        assert resp["op"] == "LIST_TRAILERS"
        assert resp["page"] == page_no
        assert resp["request_id"] == f"p{page_no}"
        assert "cursor_request_id" not in resp
        items.extend(resp["items"])
    # The full 13-trailer list assembles across pages.
    assert [it["trailer"] for it in items] == list(range(1, 14))
    assert resp["has_more"] is False


def test_cursor_path_unchanged_and_survives_newer_list(handler):
    first = _handle(handler, {"op": "LIST_TRAILERS", "request_id": "trailers-1"})
    assert first["total_pages"] >= 3

    # A newer paginated list replaces the cursor-less fallback...
    _handle(handler, {"op": "LIST_SENSORS", "request_id": "sensors-1"})

    # ...but the cursor path still serves the ORIGINAL request's page set.
    resp = _handle(
        handler,
        {"op": "PAGE", "page": 2, "cursor_request_id": "trailers-1", "request_id": "p2"},
    )
    assert resp["op"] == "LIST_TRAILERS"
    assert resp["page"] == 2
    assert resp["cursor_request_id"] == "trailers-1"
    assert resp["request_id"] == "p2"

    # And the cursor-less fallback now reflects the most recent list.
    fallback = _handle(handler, {"op": "PAGE", "page": 2, "request_id": "p2b"})
    assert fallback["op"] == "LIST_SENSORS"

    # Unknown cursor still errors (cursor path exactly as before).
    missing = _handle(
        handler, {"op": "PAGE", "page": 2, "cursor_request_id": "nope", "request_id": "p3"}
    )
    assert missing["ok"] is False
    assert missing["error"] == "No paginated data available"


def test_cursorless_page_without_prior_list_still_errors(handler):
    resp = _handle(handler, {"op": "PAGE", "page": 1, "request_id": "p1"})
    assert resp["ok"] is False
    assert resp["error"] == "No paginated data available"


def test_non_page_command_clears_cursorless_fallback(handler):
    # bumble parity: every non-paginated command resets config_response_pages,
    # so a cursor-less PAGE after an intervening command errors again.
    _handle(handler, {"op": "LIST_TRAILERS", "request_id": "r1"})
    _handle(handler, {"op": "GET_TRAILER", "request_id": "g1"})
    resp = _handle(handler, {"op": "PAGE", "page": 2, "request_id": "p2"})
    assert resp["ok"] is False
    assert resp["error"] == "No paginated data available"

    # The cursor cache is NOT cleared by intervening commands (as in bumble).
    cursor = _handle(
        handler, {"op": "PAGE", "page": 2, "cursor_request_id": "r1", "request_id": "p3"}
    )
    assert cursor["op"] == "LIST_TRAILERS"
    assert cursor["page"] == 2


def test_out_of_range_cursorless_page(handler):
    first = _handle(handler, {"op": "LIST_TRAILERS", "request_id": "r1"})
    too_far = first["total_pages"] + 1
    resp = _handle(handler, {"op": "PAGE", "page": too_far, "request_id": "p"})
    assert resp["ok"] is False
    assert f"out of range (1-{first['total_pages']})" in resp["error"]
