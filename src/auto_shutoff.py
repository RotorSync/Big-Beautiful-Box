#!/usr/bin/env python3
"""
Shared auto-shutoff threshold calculations and self-tuning model.

The base shutoff curve is pure and deterministic. The adaptive model layers a
bounded correction on top of that base curve using confirmed auto-fill results.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

import config


MODEL_VERSION = 1


def clamp(value: float, minimum: float, maximum: float) -> float:
    """Clamp value to the inclusive range [minimum, maximum]."""
    return max(minimum, min(maximum, value))


def _is_finite_number(value: object) -> bool:
    """Return True when value can be safely treated as a finite float."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def calculate_base_trigger_threshold_gpm(flow_rate_gpm: float) -> float:
    """
    Calculate the base shutoff threshold from the calibrated piecewise curve.

    Args:
        flow_rate_gpm: Flow rate in gallons per minute

    Returns:
        Base gallons-before-target threshold
    """
    flow_rate_gpm = float(flow_rate_gpm) if _is_finite_number(flow_rate_gpm) else 0.0

    if flow_rate_gpm <= config.FLOW_CURVE_SPLIT_GPM:
        predicted_coast = (
            config.FLOW_CURVE_LOW_SLOPE * flow_rate_gpm
            + config.FLOW_CURVE_LOW_INTERCEPT
        )
    else:
        predicted_coast = (
            config.FLOW_CURVE_HIGH_SLOPE * flow_rate_gpm
            + config.FLOW_CURVE_HIGH_INTERCEPT
        )

    return clamp(
        predicted_coast,
        config.AUTO_TUNE_MIN_THRESHOLD_GAL,
        config.AUTO_TUNE_MAX_THRESHOLD_GAL,
    )


def calculate_base_trigger_threshold(flow_rate_l_per_s: float) -> float:
    """Calculate the base shutoff threshold from liters/second input."""
    flow_rate_gpm = float(flow_rate_l_per_s) * config.LITERS_PER_SEC_TO_GPM
    return calculate_base_trigger_threshold_gpm(flow_rate_gpm)


def calculate_tuning_confidence(sample_count: int) -> float:
    """
    Convert accepted sample count into a tuning confidence from 0.0 to 1.0.

    Confidence stays at 0 below AUTO_TUNE_MIN_SAMPLES, then ramps linearly until
    AUTO_TUNE_FULL_CONFIDENCE_SAMPLES.
    """
    sample_count = max(0, int(sample_count))
    min_samples = max(1, int(config.AUTO_TUNE_MIN_SAMPLES))
    full_samples = max(min_samples, int(config.AUTO_TUNE_FULL_CONFIDENCE_SAMPLES))

    if sample_count < min_samples:
        return 0.0
    if full_samples == min_samples:
        return 1.0

    return clamp(
        (sample_count - min_samples + 1) / (full_samples - min_samples + 1),
        0.0,
        1.0,
    )


@dataclass
class AdaptiveSample:
    """A confirmed auto-fill outcome used for self-tuning."""

    timestamp: str
    flow_gpm: float
    requested_gallons: float
    actual_gallons: float
    applied_threshold_gal: float
    corrected_threshold_gal: float
    delta_from_base_gal: float

    @classmethod
    def from_dict(cls, data: dict) -> "AdaptiveSample":
        """Build an AdaptiveSample from persisted JSON data."""
        return cls(
            timestamp=str(data["timestamp"]),
            flow_gpm=float(data["flow_gpm"]),
            requested_gallons=float(data["requested_gallons"]),
            actual_gallons=float(data["actual_gallons"]),
            applied_threshold_gal=float(data["applied_threshold_gal"]),
            corrected_threshold_gal=float(data["corrected_threshold_gal"]),
            delta_from_base_gal=float(data["delta_from_base_gal"]),
        )

    def to_dict(self) -> dict:
        """Convert sample to JSON-safe dict."""
        return asdict(self)


@dataclass
class ThresholdCalculation:
    """Detailed threshold calculation output for observability."""

    flow_gpm: float
    base_threshold_gal: float
    adaptive_delta_gal: float
    confidence: float
    final_threshold_gal: float
    sample_count: int


@dataclass
class LearningResult:
    """Result of trying to learn from a confirmed fill."""

    accepted: bool
    reason: str
    sample_count: int
    skipped: bool = False
    base_threshold_gal: float = 0.0
    corrected_threshold_gal: float = 0.0
    delta_from_base_gal: float = 0.0
    flow_variation_gpm: float = 0.0
    save_error: str = ""
    sample: Optional[AdaptiveSample] = None


class AutoShutoffTuningModel:
    """File-backed adaptive overlay for the base auto-shutoff curve."""

    def __init__(self, model_path: Optional[object] = None):
        self.model_path = Path(model_path or config.AUTO_TUNE_MODEL_FILE)
        self.samples: List[AdaptiveSample] = []
        self.updated_at: str = ""
        self.load_status = "missing"
        self.last_load_error = ""
        self.last_save_error = ""
        self.load()

    @property
    def sample_count(self) -> int:
        """Return the number of active adaptive samples."""
        return len(self.samples)

    def load(self) -> bool:
        """Load tuning state from disk if it exists."""
        self.samples = []
        self.updated_at = ""
        self.last_load_error = ""
        self.last_save_error = ""

        if not self.model_path.exists():
            self.load_status = "missing"
            return False

        try:
            with self.model_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)

            if not isinstance(payload, dict):
                raise ValueError("root payload must be an object")

            raw_samples = payload.get("samples", [])
            if not isinstance(raw_samples, list):
                raise ValueError("samples must be a list")

            max_samples = max(1, int(config.AUTO_TUNE_MAX_SAMPLES))
            self.samples = [
                AdaptiveSample.from_dict(sample_data)
                for sample_data in raw_samples[-max_samples:]
            ]
            self.updated_at = str(payload.get("updated_at", "") or "")
            self.load_status = "loaded"
            return True
        except Exception as exc:
            self.samples = []
            self.updated_at = ""
            self.load_status = "error"
            self.last_load_error = str(exc)
            return False

    def _payload(self) -> dict:
        """Serialize model state for persistence."""
        return {
            "version": MODEL_VERSION,
            "updated_at": self.updated_at,
            "sample_count": self.sample_count,
            "samples": [sample.to_dict() for sample in self.samples],
        }

    def save(self) -> bool:
        """Persist tuning state to disk."""
        self.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.last_save_error = ""

        try:
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            with self.model_path.open("w", encoding="utf-8") as handle:
                json.dump(self._payload(), handle, indent=2)
            self.load_status = "loaded"
            self.last_load_error = ""
            return True
        except Exception as exc:
            self.last_save_error = str(exc)
            return False

    def adaptive_delta(self, flow_gpm: float) -> float:
        """Return the weighted adaptive delta for the requested flow."""
        if not self.samples:
            return 0.0

        flow_gpm = float(flow_gpm) if _is_finite_number(flow_gpm) else 0.0
        bandwidth = max(0.001, float(config.AUTO_TUNE_FLOW_BANDWIDTH_GPM))

        total_weight = 0.0
        weighted_delta = 0.0
        for sample in self.samples:
            distance = (flow_gpm - sample.flow_gpm) / bandwidth
            weight = 1.0 / (1.0 + distance * distance)
            total_weight += weight
            weighted_delta += weight * sample.delta_from_base_gal

        if total_weight <= 0:
            return 0.0

        return clamp(
            weighted_delta / total_weight,
            -config.AUTO_TUNE_MAX_DELTA_GAL,
            config.AUTO_TUNE_MAX_DELTA_GAL,
        )

    def calculate_threshold(self, flow_rate_l_per_s: float) -> ThresholdCalculation:
        """Calculate the live trigger threshold for the provided flow rate."""
        flow_rate_l_per_s = (
            float(flow_rate_l_per_s)
            if _is_finite_number(flow_rate_l_per_s)
            else 0.0
        )
        flow_gpm = max(0.0, flow_rate_l_per_s * config.LITERS_PER_SEC_TO_GPM)
        base_threshold = calculate_base_trigger_threshold_gpm(flow_gpm)

        adaptive_delta = 0.0
        confidence = 0.0
        if config.AUTO_TUNE_ENABLED:
            adaptive_delta = self.adaptive_delta(flow_gpm)
            confidence = calculate_tuning_confidence(self.sample_count)

        final_threshold = clamp(
            base_threshold + confidence * adaptive_delta,
            config.AUTO_TUNE_MIN_THRESHOLD_GAL,
            config.AUTO_TUNE_MAX_THRESHOLD_GAL,
        )

        return ThresholdCalculation(
            flow_gpm=flow_gpm,
            base_threshold_gal=base_threshold,
            adaptive_delta_gal=adaptive_delta,
            confidence=confidence,
            final_threshold_gal=final_threshold,
            sample_count=self.sample_count,
        )

    def record_confirmed_fill(
        self,
        shutoff_type: str,
        flow_gpm: float,
        requested_gallons: float,
        actual_gallons: float,
        applied_threshold_gal: float,
        flow_variation_gpm: float = 0.0,
    ) -> LearningResult:
        """
        Learn from a confirmed fill when it is eligible for auto-tuning.

        Manual fills are ignored by design.
        """
        if str(shutoff_type).strip().lower() != "auto":
            return LearningResult(
                accepted=False,
                skipped=True,
                reason=f"skipped {shutoff_type or 'unknown'} fill",
                sample_count=self.sample_count,
            )

        return self.learn_from_auto_fill(
            flow_gpm=flow_gpm,
            requested_gallons=requested_gallons,
            actual_gallons=actual_gallons,
            applied_threshold_gal=applied_threshold_gal,
            flow_variation_gpm=flow_variation_gpm,
        )

    def learn_from_auto_fill(
        self,
        flow_gpm: float,
        requested_gallons: float,
        actual_gallons: float,
        applied_threshold_gal: float,
        flow_variation_gpm: float = 0.0,
    ) -> LearningResult:
        """Learn a new adaptive sample from a confirmed auto fill."""
        if not config.AUTO_TUNE_ENABLED:
            return LearningResult(
                accepted=False,
                reason="auto-tune disabled",
                sample_count=self.sample_count,
            )

        if not _is_finite_number(flow_gpm) or float(flow_gpm) <= 0:
            return LearningResult(
                accepted=False,
                reason="missing or non-positive flow",
                sample_count=self.sample_count,
            )

        if not _is_finite_number(applied_threshold_gal) or float(applied_threshold_gal) <= 0:
            return LearningResult(
                accepted=False,
                reason="missing or non-positive applied threshold",
                sample_count=self.sample_count,
            )

        if not _is_finite_number(flow_variation_gpm) or float(flow_variation_gpm) < 0:
            return LearningResult(
                accepted=False,
                reason="invalid flow variation",
                sample_count=self.sample_count,
            )

        if not _is_finite_number(requested_gallons) or not _is_finite_number(actual_gallons):
            return LearningResult(
                accepted=False,
                reason="invalid gallons in confirmed fill",
                sample_count=self.sample_count,
            )

        flow_gpm = float(flow_gpm)
        requested_gallons = float(requested_gallons)
        actual_gallons = float(actual_gallons)
        applied_threshold_gal = float(applied_threshold_gal)
        flow_variation_gpm = float(flow_variation_gpm)
        fill_error = actual_gallons - requested_gallons

        if abs(fill_error) > config.AUTO_TUNE_MAX_SAMPLE_ERROR_GAL:
            return LearningResult(
                accepted=False,
                reason=(
                    "fill error exceeds max sample error "
                    f"({fill_error:+.3f} gal)"
                ),
                sample_count=self.sample_count,
                flow_variation_gpm=flow_variation_gpm,
            )

        if flow_variation_gpm > config.AUTO_TUNE_MAX_FLOW_VARIATION_GPM:
            return LearningResult(
                accepted=False,
                reason=(
                    "flow variation exceeds max stability window "
                    f"({flow_variation_gpm:.2f} GPM)"
                ),
                sample_count=self.sample_count,
                flow_variation_gpm=flow_variation_gpm,
            )

        base_threshold = calculate_base_trigger_threshold_gpm(flow_gpm)
        corrected_threshold = clamp(
            applied_threshold_gal
            + (fill_error - config.AUTO_TUNE_TARGET_ERROR_GAL),
            config.AUTO_TUNE_MIN_THRESHOLD_GAL,
            config.AUTO_TUNE_MAX_THRESHOLD_GAL,
        )
        delta_from_base = clamp(
            corrected_threshold - base_threshold,
            -config.AUTO_TUNE_MAX_DELTA_GAL,
            config.AUTO_TUNE_MAX_DELTA_GAL,
        )
        corrected_threshold = clamp(
            base_threshold + delta_from_base,
            config.AUTO_TUNE_MIN_THRESHOLD_GAL,
            config.AUTO_TUNE_MAX_THRESHOLD_GAL,
        )

        sample = AdaptiveSample(
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            flow_gpm=flow_gpm,
            requested_gallons=requested_gallons,
            actual_gallons=actual_gallons,
            applied_threshold_gal=applied_threshold_gal,
            corrected_threshold_gal=corrected_threshold,
            delta_from_base_gal=delta_from_base,
        )

        self.samples.append(sample)
        max_samples = max(1, int(config.AUTO_TUNE_MAX_SAMPLES))
        if len(self.samples) > max_samples:
            self.samples = self.samples[-max_samples:]

        save_error = ""
        if not self.save():
            save_error = self.last_save_error

        return LearningResult(
            accepted=True,
            reason="accepted",
            sample_count=self.sample_count,
            base_threshold_gal=base_threshold,
            corrected_threshold_gal=corrected_threshold,
            delta_from_base_gal=delta_from_base,
            flow_variation_gpm=flow_variation_gpm,
            save_error=save_error,
            sample=sample,
        )
