#!/usr/bin/env python3
"""Flow shutoff curve calculation and conservative runtime learning."""

import json
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import config


@dataclass(frozen=True)
class FlowCurve:
    split_gpm: float
    low_slope: float
    low_intercept: float
    high_slope: float
    high_intercept: float

    @classmethod
    def factory(cls) -> "FlowCurve":
        return cls(
            split_gpm=float(config.FLOW_CURVE_SPLIT_GPM),
            low_slope=float(config.FLOW_CURVE_LOW_SLOPE),
            low_intercept=float(config.FLOW_CURVE_LOW_INTERCEPT),
            high_slope=float(config.FLOW_CURVE_HIGH_SLOPE),
            high_intercept=float(config.FLOW_CURVE_HIGH_INTERCEPT),
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FlowCurve":
        return cls(
            split_gpm=float(data["split_gpm"]),
            low_slope=float(data["low_slope"]),
            low_intercept=float(data["low_intercept"]),
            high_slope=float(data["high_slope"]),
            high_intercept=float(data["high_intercept"]),
        )

    def as_dict(self) -> Dict[str, float]:
        return {
            "split_gpm": self.split_gpm,
            "low_slope": self.low_slope,
            "low_intercept": self.low_intercept,
            "high_slope": self.high_slope,
            "high_intercept": self.high_intercept,
        }

    def threshold_gpm(self, flow_gpm: float) -> float:
        if flow_gpm <= self.split_gpm:
            predicted = self.low_slope * flow_gpm + self.low_intercept
        else:
            predicted = self.high_slope * flow_gpm + self.high_intercept
        return max(predicted, 0.1)

    def threshold_l_per_s(self, flow_rate_l_per_s: float) -> float:
        return self.threshold_gpm(flow_rate_l_per_s * config.LITERS_PER_SEC_TO_GPM)


def calculate_trigger_threshold(
    flow_rate_l_per_s: float,
    curve: Optional[FlowCurve] = None,
) -> float:
    """Calculate the shutoff trigger threshold from an L/s flow reading."""
    return (curve or FlowCurve.factory()).threshold_l_per_s(flow_rate_l_per_s)


def calculate_trigger_threshold_gpm(
    flow_gpm: float,
    curve: Optional[FlowCurve] = None,
) -> float:
    """Calculate the shutoff trigger threshold from a GPM flow reading."""
    return (curve or FlowCurve.factory()).threshold_gpm(flow_gpm)


def load_curve_override(path: str) -> Tuple[FlowCurve, Dict[str, Any]]:
    """Load a learned curve override, falling back to factory on any error."""
    factory_curve = FlowCurve.factory()
    try:
        with open(path, "r") as f:
            payload = json.load(f)
        curve = FlowCurve.from_dict(payload["curve"])
        _validate_curve(curve)
        metadata = dict(payload)
        metadata.pop("curve", None)
        metadata["source"] = "learned"
        return curve, metadata
    except FileNotFoundError:
        return factory_curve, {"source": "factory", "reason": "no override"}
    except Exception as exc:
        return factory_curve, {"source": "factory", "reason": f"override invalid: {exc}"}


def make_confirmed_auto_sample(
    *,
    requested_gallons: float,
    actual_gallons: float,
    flow_gpm: float,
    threshold_gallons: float,
    shutoff_type: str,
    timestamp: Optional[float] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Return a learning sample only for usable confirmed Auto thumbs-up fills."""
    if shutoff_type != "Auto":
        return None, f"not auto shutoff ({shutoff_type or 'unknown'})"
    if not _finite_positive(requested_gallons):
        return None, "requested gallons invalid"
    if not _finite_positive(actual_gallons):
        return None, "actual gallons invalid"
    if not _finite_positive(flow_gpm):
        return None, "flow snapshot invalid"
    if not _finite_positive(threshold_gallons):
        return None, "threshold snapshot invalid"

    diff_gallons = actual_gallons - requested_gallons
    max_error = float(config.FLOW_CURVE_MAX_SAMPLE_ERROR_GAL)
    if abs(diff_gallons) > max_error:
        return None, f"diff {diff_gallons:+.3f} gal exceeds {max_error:.1f} gal guard"

    corrected_threshold = max(threshold_gallons + diff_gallons, 0.1)
    sample = {
        "timestamp": float(timestamp if timestamp is not None else time.time()),
        "requested_gallons": float(requested_gallons),
        "actual_gallons": float(actual_gallons),
        "diff_gallons": float(diff_gallons),
        "flow_gpm": float(flow_gpm),
        "threshold_gallons": float(threshold_gallons),
        "corrected_threshold_gallons": float(corrected_threshold),
        "shutoff_type": shutoff_type,
    }
    return sample, "accepted"


def record_learning_sample(
    sample_path: str,
    proposal_path: str,
    sample: Dict[str, Any],
) -> Dict[str, Any]:
    """Store the sample and save a learned proposal after the last N samples."""
    sample_count = int(config.FLOW_CURVE_LEARN_SAMPLE_COUNT)
    samples_payload = _read_json(sample_path, {"version": 1, "samples": []})
    samples = [
        existing
        for existing in samples_payload.get("samples", [])
        if _valid_sample(existing)
    ]
    samples.append(sample)
    samples = samples[-sample_count:]

    _write_json(sample_path, {
        "version": 1,
        "updated_at": sample["timestamp"],
        "samples": samples,
    })

    result: Dict[str, Any] = {
        "accepted": True,
        "sample_count": len(samples),
        "required_sample_count": sample_count,
        "proposal_saved": False,
    }

    if len(samples) < sample_count:
        return result

    curve, learning = learn_curve_from_samples(samples)
    proposal_payload = {
        "version": 1,
        "source": "pending_last_confirmed_auto_fills",
        "updated_at": sample["timestamp"],
        "sample_count": len(samples),
        "learning": learning,
        "curve": curve.as_dict(),
        "samples": samples,
    }
    _write_json(proposal_path, proposal_payload)
    result.update({
        "proposal_saved": True,
        "curve": curve.as_dict(),
        "learning": learning,
    })
    return result


def load_curve_proposal(path: str) -> Optional[Dict[str, Any]]:
    """Load a pending learned curve proposal if one is valid."""
    try:
        with open(path, "r") as f:
            payload = json.load(f)
        curve = FlowCurve.from_dict(payload["curve"])
        _validate_curve(curve)
        return payload
    except Exception:
        return None


def accept_curve_proposal(proposal_path: str, override_path: str) -> Dict[str, Any]:
    """Promote a pending proposal to the active learned override file."""
    proposal = load_curve_proposal(proposal_path)
    if proposal is None:
        raise FileNotFoundError("no valid learned curve proposal")

    payload = dict(proposal)
    payload["source"] = "learned_last_confirmed_auto_fills"
    payload["accepted_at"] = time.time()
    _write_json(override_path, payload)
    try:
        os.remove(proposal_path)
    except FileNotFoundError:
        pass
    return payload


def learn_curve_from_samples(samples: List[Dict[str, Any]]) -> Tuple[FlowCurve, Dict[str, Any]]:
    """Build a conservative learned curve by shifting factory intercepts only."""
    factory_curve = FlowCurve.factory()
    offsets = []
    for sample in samples:
        flow_gpm = float(sample["flow_gpm"])
        corrected = float(sample["corrected_threshold_gallons"])
        offsets.append(corrected - factory_curve.threshold_gpm(flow_gpm))

    raw_offset = sum(offsets) / len(offsets)
    max_offset = float(config.FLOW_CURVE_MAX_LEARNED_OFFSET_GAL)
    applied_offset = max(-max_offset, min(max_offset, raw_offset))

    curve = FlowCurve(
        split_gpm=factory_curve.split_gpm,
        low_slope=factory_curve.low_slope,
        low_intercept=factory_curve.low_intercept + applied_offset,
        high_slope=factory_curve.high_slope,
        high_intercept=factory_curve.high_intercept + applied_offset,
    )
    learning = {
        "method": "last_3_auto_fills_global_intercept_offset",
        "raw_offset_gallons": raw_offset,
        "applied_offset_gallons": applied_offset,
        "max_offset_gallons": max_offset,
        "sample_flow_gpm": [float(sample["flow_gpm"]) for sample in samples],
        "sample_diff_gallons": [float(sample["diff_gallons"]) for sample in samples],
    }
    return curve, learning


def reset_learning(sample_path: str, proposal_path: str, override_path: str) -> List[str]:
    """Archive learned files so the next load returns to the factory curve."""
    archived = []
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for path in (sample_path, proposal_path, override_path):
        if os.path.exists(path):
            archive_path = f"{path}.reset_{stamp}"
            os.replace(path, archive_path)
            archived.append(archive_path)
    return archived


def _validate_curve(curve: FlowCurve) -> None:
    for value in curve.as_dict().values():
        if not math.isfinite(value):
            raise ValueError("curve contains non-finite value")
    for gpm in (10.0, curve.split_gpm, 100.0):
        threshold = curve.threshold_gpm(gpm)
        if not math.isfinite(threshold) or threshold < 0.1 or threshold > 10.0:
            raise ValueError(f"curve threshold out of range at {gpm:.1f} GPM")


def _finite_positive(value: float) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def _valid_sample(sample: Dict[str, Any]) -> bool:
    required = [
        "timestamp",
        "requested_gallons",
        "actual_gallons",
        "diff_gallons",
        "flow_gpm",
        "threshold_gallons",
        "corrected_threshold_gallons",
        "shutoff_type",
    ]
    return all(key in sample for key in required)


def _read_json(path: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return fallback
    except Exception:
        return fallback


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(temp_path, path)
