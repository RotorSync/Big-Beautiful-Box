#!/usr/bin/env python3
"""
Tests for the self-tuning auto-shutoff model.
"""

import json
import os
import sys

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from src.auto_shutoff import AutoShutoffTuningModel, calculate_base_trigger_threshold_gpm


class TestBaseThreshold:
    """Tests for the shared piecewise base curve."""

    def test_low_band_matches_config(self):
        flow_gpm = 42.3
        expected = max(
            config.AUTO_TUNE_MIN_THRESHOLD_GAL,
            min(
                config.AUTO_TUNE_MAX_THRESHOLD_GAL,
                config.FLOW_CURVE_LOW_SLOPE * flow_gpm
                + config.FLOW_CURVE_LOW_INTERCEPT,
            ),
        )
        assert calculate_base_trigger_threshold_gpm(flow_gpm) == pytest.approx(expected)

    def test_high_band_matches_config(self):
        flow_gpm = 84.4
        expected = max(
            config.AUTO_TUNE_MIN_THRESHOLD_GAL,
            min(
                config.AUTO_TUNE_MAX_THRESHOLD_GAL,
                config.FLOW_CURVE_HIGH_SLOPE * flow_gpm
                + config.FLOW_CURVE_HIGH_INTERCEPT,
            ),
        )
        assert calculate_base_trigger_threshold_gpm(flow_gpm) == pytest.approx(expected)


class TestAutoShutoffTuningModel:
    """Tests for the adaptive overlay."""

    def _model(self, tmp_path):
        return AutoShutoffTuningModel(tmp_path / "auto_shutoff_tuning.json")

    def _learn(self, model, flow_gpm, actual_offset, applied_threshold=None):
        if applied_threshold is None:
            applied_threshold = calculate_base_trigger_threshold_gpm(flow_gpm)
        return model.record_confirmed_fill(
            shutoff_type="Auto",
            flow_gpm=flow_gpm,
            requested_gallons=100.0,
            actual_gallons=100.0 + actual_offset,
            applied_threshold_gal=applied_threshold,
            flow_variation_gpm=0.0,
        )

    def test_no_samples_uses_base_only(self, tmp_path):
        model = self._model(tmp_path)
        flow_gpm = 60.0
        details = model.calculate_threshold(flow_gpm / config.LITERS_PER_SEC_TO_GPM)
        assert details.sample_count == 0
        assert details.confidence == 0.0
        assert details.adaptive_delta_gal == 0.0
        assert details.final_threshold_gal == pytest.approx(details.base_threshold_gal)

    def test_fewer_than_min_samples_do_not_tune(self, tmp_path):
        model = self._model(tmp_path)
        for _ in range(config.AUTO_TUNE_MIN_SAMPLES - 1):
            result = self._learn(model, flow_gpm=60.0, actual_offset=0.5)
            assert result.accepted

        details = model.calculate_threshold(60.0 / config.LITERS_PER_SEC_TO_GPM)
        assert details.sample_count == config.AUTO_TUNE_MIN_SAMPLES - 1
        assert details.confidence == 0.0
        assert details.adaptive_delta_gal > 0.0
        assert details.final_threshold_gal == pytest.approx(details.base_threshold_gal)

    def test_overfill_increases_future_threshold(self, tmp_path):
        model = self._model(tmp_path)
        for _ in range(config.AUTO_TUNE_MIN_SAMPLES):
            result = self._learn(model, flow_gpm=60.0, actual_offset=0.5)
            assert result.accepted

        details = model.calculate_threshold(60.0 / config.LITERS_PER_SEC_TO_GPM)
        assert details.confidence > 0.0
        assert details.final_threshold_gal > details.base_threshold_gal

    def test_underfill_decreases_future_threshold(self, tmp_path):
        model = self._model(tmp_path)
        for _ in range(config.AUTO_TUNE_MIN_SAMPLES):
            result = self._learn(model, flow_gpm=60.0, actual_offset=-0.5)
            assert result.accepted

        details = model.calculate_threshold(60.0 / config.LITERS_PER_SEC_TO_GPM)
        assert details.confidence > 0.0
        assert details.final_threshold_gal < details.base_threshold_gal

    def test_manual_fills_do_not_tune(self, tmp_path):
        model = self._model(tmp_path)
        base_threshold = calculate_base_trigger_threshold_gpm(60.0)

        result = model.record_confirmed_fill(
            shutoff_type="Manual",
            flow_gpm=60.0,
            requested_gallons=100.0,
            actual_gallons=100.5,
            applied_threshold_gal=base_threshold,
            flow_variation_gpm=0.0,
        )

        assert result.accepted is False
        assert result.skipped is True
        assert model.sample_count == 0

    def test_outlier_fill_is_rejected(self, tmp_path):
        model = self._model(tmp_path)
        result = self._learn(model, flow_gpm=60.0, actual_offset=4.1)
        assert result.accepted is False
        assert "max sample error" in result.reason
        assert model.sample_count == 0

    def test_unstable_flow_is_rejected(self, tmp_path):
        model = self._model(tmp_path)
        result = model.record_confirmed_fill(
            shutoff_type="Auto",
            flow_gpm=60.0,
            requested_gallons=100.0,
            actual_gallons=100.2,
            applied_threshold_gal=calculate_base_trigger_threshold_gpm(60.0),
            flow_variation_gpm=config.AUTO_TUNE_MAX_FLOW_VARIATION_GPM + 0.1,
        )
        assert result.accepted is False
        assert "flow variation" in result.reason
        assert model.sample_count == 0

    def test_nearby_flow_samples_influence_more_than_far_away_samples(self, tmp_path):
        model = self._model(tmp_path)
        for _ in range(3):
            assert self._learn(model, flow_gpm=40.0, actual_offset=0.6).accepted
            assert self._learn(model, flow_gpm=80.0, actual_offset=-0.6).accepted

        low_flow_details = model.calculate_threshold(42.0 / config.LITERS_PER_SEC_TO_GPM)
        high_flow_details = model.calculate_threshold(78.0 / config.LITERS_PER_SEC_TO_GPM)

        assert low_flow_details.adaptive_delta_gal > 0.0
        assert high_flow_details.adaptive_delta_gal < 0.0

    def test_threshold_and_delta_clamps_are_enforced(self, tmp_path):
        model = self._model(tmp_path)
        flow_gpm = 60.0
        applied_threshold = config.AUTO_TUNE_MAX_THRESHOLD_GAL

        result = self._learn(
            model,
            flow_gpm=flow_gpm,
            actual_offset=3.0,
            applied_threshold=applied_threshold,
        )

        assert result.accepted
        assert result.corrected_threshold_gal == config.AUTO_TUNE_MAX_THRESHOLD_GAL
        assert result.delta_from_base_gal == pytest.approx(config.AUTO_TUNE_MAX_DELTA_GAL)

    def test_missing_model_file_falls_back_cleanly(self, tmp_path):
        model = self._model(tmp_path)
        assert model.load_status == "missing"

        details = model.calculate_threshold(60.0 / config.LITERS_PER_SEC_TO_GPM)
        assert details.final_threshold_gal == pytest.approx(details.base_threshold_gal)

    def test_corrupted_model_file_falls_back_cleanly(self, tmp_path):
        model_path = tmp_path / "auto_shutoff_tuning.json"
        model_path.write_text("{not-json", encoding="utf-8")

        model = AutoShutoffTuningModel(model_path)
        details = model.calculate_threshold(60.0 / config.LITERS_PER_SEC_TO_GPM)

        assert model.load_status == "error"
        assert model.sample_count == 0
        assert details.final_threshold_gal == pytest.approx(details.base_threshold_gal)

    def test_persisted_samples_round_trip(self, tmp_path):
        model_path = tmp_path / "auto_shutoff_tuning.json"
        model = AutoShutoffTuningModel(model_path)
        assert self._learn(model, flow_gpm=60.0, actual_offset=0.5).accepted

        payload = json.loads(model_path.read_text(encoding="utf-8"))
        assert payload["version"] == 1
        assert payload["sample_count"] == 1

        reloaded = AutoShutoffTuningModel(model_path)
        assert reloaded.load_status == "loaded"
        assert reloaded.sample_count == 1
