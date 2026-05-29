#!/usr/bin/env python3
"""Tests for BatchMix payload contract parsing."""

from src.batchmix_payload import (
    batchmix_validation_error,
    parse_field_color,
    scaled_batchmix_payload,
)


def test_accepts_solid_and_striped_field_colors():
    payload = {
        "products": [
            {"name": "Miravis Ace", "amount_oz": 265},
        ],
        "field_colors": [
            {"color": "#00FF00"},
            {"color": "#FF0000/#0000FF"},
        ],
        "water_needed": 36.0,
        "product_count": 1,
    }

    assert batchmix_validation_error(payload) is None
    assert parse_field_color("#00FF00") == ("solid", "#00FF00")
    assert parse_field_color("#FF0000/#0000FF") == ("stripe", "#FF0000", "#0000FF")


def test_rejects_invalid_field_color():
    payload = {
        "products": [
            {"name": "Miravis Ace", "amount_oz": 265},
        ],
        "field_colors": [
            {"color": "#00FF00/blue"},
        ],
        "water_needed": 36.0,
        "product_count": 1,
    }

    assert batchmix_validation_error(payload) == (
        "field_colors[1].color must be #RRGGBB or #RRGGBB/#RRGGBB"
    )


def test_rejects_legacy_jug_fields():
    payload = {
        "products": [
            {"name": "Miravis Ace", "amount_oz": 265, "jug_size": "2.5 gal"},
        ],
        "product_count": 1,
    }

    assert batchmix_validation_error(payload) == "Product 1 has legacy jug field: jug_size"


def test_scales_batchmix_payload_for_new_acres():
    payload = {
        "products": [
            {"name": "Miravis Ace", "amount_oz": 630},
            {"name": "AMS", "amount_lb": 20},
        ],
        "field_colors": [{"color": "#00FF00"}],
        "water_needed": 63.0,
        "total_acres": 63.0,
        "gallons_per_acre": 2.0,
        "total_liquid": 67.0,
        "product_count": 2,
    }

    scaled = scaled_batchmix_payload(payload, 64.0)

    assert scaled["total_acres"] == 64.0
    assert scaled["gallons_per_acre"] == 2.0
    assert abs(scaled["water_needed"] - 64.0) < 0.0001
    assert abs(scaled["total_liquid"] - (67.0 * 64.0 / 63.0)) < 0.0001
    assert abs(scaled["products"][0]["amount_oz"] - 640.0) < 0.0001
    assert abs(scaled["products"][1]["amount_lb"] - (20.0 * 64.0 / 63.0)) < 0.0001
    assert payload["total_acres"] == 63.0
