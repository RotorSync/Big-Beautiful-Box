#!/usr/bin/env python3
"""Tests for BatchMix payload contract parsing."""

from src.batchmix_payload import (
    batchmix_validation_error,
    parse_field_color,
    scaled_batchmix_payload_for_water,
)


def test_accepts_solid_and_striped_field_colors():
    payload = {
        "products": [
            {
                "name": "Miravis Ace",
                "amount_oz": 265,
                "rate_per_acre": 26.5,
                "rate_unit": "oz/ac",
            },
            {
                "name": "AMS",
                "amount_lb": 20,
                "rate_per_acre": 2,
                "rate_unit": "lb/ac",
            },
        ],
        "field_colors": [
            {"color": "#00FF00"},
            {"color": "#FF0000/#0000FF"},
        ],
        "water_needed": 36.0,
        "product_count": 2,
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


def test_rejects_rate_unit_mismatch():
    payload = {
        "products": [
            {
                "name": "Miravis Ace",
                "amount_oz": 265,
                "rate_per_acre": 26.5,
                "rate_unit": "lb/ac",
            },
        ],
        "product_count": 1,
    }

    assert batchmix_validation_error(payload) == "Product 1 rate_unit must be oz/ac"


def test_scales_batchmix_payload_for_new_water_target():
    payload = {
        "products": [
            {
                "name": "Miravis Ace",
                "amount_oz": 630,
                "rate_per_acre": 20,
                "rate_unit": "oz/ac",
            },
            {
                "name": "AMS",
                "amount_lb": 20,
                "rate_per_acre": 1,
                "rate_unit": "lb/ac",
            },
        ],
        "field_colors": [{"color": "#00FF00"}],
        "water_needed": 63.0,
        "total_acres": 31.5,
        "gallons_per_acre": 2.0,
        "total_liquid": 67.0,
        "product_count": 2,
    }

    scaled = scaled_batchmix_payload_for_water(payload, 64.0)

    assert scaled["water_needed"] == 64.0
    assert scaled["gallons_per_acre"] == 2.0
    assert abs(scaled["total_acres"] - 32.0) < 0.0001
    assert abs(scaled["total_liquid"] - (67.0 * 64.0 / 63.0)) < 0.0001
    assert abs(scaled["products"][0]["amount_oz"] - 640.0) < 0.0001
    assert abs(scaled["products"][1]["amount_lb"] - 32.0) < 0.0001
    assert scaled["products"][0]["rate_per_acre"] == 20
    assert scaled["products"][1]["rate_unit"] == "lb/ac"
    assert payload["water_needed"] == 63.0


def test_scales_batchmix_payload_from_one_gallon_minimum():
    payload = {
        "products": [
            {"name": "Miravis Ace", "amount_oz": 10},
        ],
        "water_needed": 1.0,
        "total_acres": 0.5,
        "gallons_per_acre": 2.0,
        "total_liquid": 1.2,
        "product_count": 1,
    }

    scaled = scaled_batchmix_payload_for_water(payload, 2.0)

    assert scaled["water_needed"] == 2.0
    assert abs(scaled["total_acres"] - 1.0) < 0.0001
    assert abs(scaled["products"][0]["amount_oz"] - 20.0) < 0.0001
