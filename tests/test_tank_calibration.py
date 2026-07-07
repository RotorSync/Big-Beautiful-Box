"""Tests for src/tank_calibration.py — the calibration wizard's math."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tank_calibration import (
    compute_point_targets,
    expected_level_in,
    offset_adjustment_inches,
)


def test_full_mode_stops_one_step_early():
    # Operator's example: 300 gal, 10 points -> fills at 30..270, never 300.
    targets = compute_point_targets('full', total_capacity=300, points=10)
    assert targets == [30.0, 60.0, 90.0, 120.0, 150.0, 180.0, 210.0, 240.0, 270.0]
    assert max(targets) < 300


def test_full_mode_uneven_division():
    targets = compute_point_targets('full', total_capacity=1070, points=8)
    assert len(targets) == 7
    assert targets[0] == pytest.approx(133.75)
    assert targets[-1] == pytest.approx(936.25)


def test_full_mode_validation():
    with pytest.raises(ValueError):
        compute_point_targets('full', total_capacity=0, points=10)
    with pytest.raises(ValueError):
        compute_point_targets('full', total_capacity=300, points=1)


def test_offset_mode_reaches_user_max():
    # Offset mode: user chose the ceiling, so the last target IS max_gallons.
    targets = compute_point_targets('offset', points=4, max_gallons=100)
    assert targets == [25.0, 50.0, 75.0, 100.0]


def test_offset_mode_validation():
    with pytest.raises(ValueError):
        compute_point_targets('offset', points=0, max_gallons=100)
    with pytest.raises(ValueError):
        compute_point_targets('offset', points=3, max_gallons=0)


def test_unknown_mode():
    with pytest.raises(ValueError):
        compute_point_targets('sideways', total_capacity=1, points=2)


# Curve inversion: table rows are (tank_level_in, gallons), inches measured
# from the sensor at the top (bigger = emptier), like the profile CSVs.
CURVE = [(56.0, 0.0), (40.0, 300.0), (20.0, 700.0), (5.0, 1000.0)]


def test_expected_level_at_table_points():
    assert expected_level_in(CURVE, 0) == 56.0
    assert expected_level_in(CURVE, 300) == 40.0
    assert expected_level_in(CURVE, 1000) == 5.0


def test_expected_level_interpolates():
    assert expected_level_in(CURVE, 150) == pytest.approx(48.0)   # halfway 56->40
    assert expected_level_in(CURVE, 500) == pytest.approx(30.0)   # halfway 40->20


def test_expected_level_clamps_out_of_range():
    assert expected_level_in(CURVE, -5) == 56.0
    assert expected_level_in(CURVE, 5000) == 5.0


def test_expected_level_needs_two_rows():
    with pytest.raises(ValueError):
        expected_level_in([(56.0, 0.0)], 10)


def test_offset_adjustment_averages():
    # Sensor reads 0.5" too empty at every point -> add +0.5 to the offset.
    assert offset_adjustment_inches([0.4, 0.5, 0.6]) == pytest.approx(0.5)
    assert offset_adjustment_inches([-0.2, 0.2]) == 0.0


def test_offset_adjustment_empty_raises():
    with pytest.raises(ValueError):
        offset_adjustment_inches([])
