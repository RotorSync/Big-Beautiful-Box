"""Safety helpers for interpreting Picomag flow-meter readings."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NegativeTotalizerStatus:
    signed_gallons: float
    fault: bool
    reset_clear: bool
    reason: str


@dataclass(frozen=True)
class PositiveDriftStatus:
    drift_gallons: float
    flow_gpm: float
    low_flow: bool
    fault: bool
    reason: str


@dataclass(frozen=True)
class NegativeFlowStatus:
    flow_gpm: float
    negative_flow: bool
    fault: bool
    reason: str


def negative_totalizer_status(
    signed_totalizer_liters: float,
    liters_to_gallons: float,
    fault_threshold_gallons: float,
    clear_threshold_gallons: float,
) -> NegativeTotalizerStatus:
    """Evaluate whether a signed totalizer reading is an unsafe idle-drift fault."""
    signed_gallons = float(signed_totalizer_liters) * float(liters_to_gallons)
    fault_threshold = abs(float(fault_threshold_gallons))
    clear_threshold = abs(float(clear_threshold_gallons))
    fault = signed_gallons <= -fault_threshold
    reset_clear = abs(signed_gallons) <= clear_threshold
    reason = ""
    if fault:
        reason = f"NEGATIVE FLOW METER {signed_gallons:.1f} GAL - RESET REQUIRED"
    return NegativeTotalizerStatus(
        signed_gallons=signed_gallons,
        fault=fault,
        reset_clear=reset_clear,
        reason=reason,
    )


def negative_flow_status(
    flow_rate_l_per_s: float,
    negative_flow_elapsed_seconds: float,
    liters_per_sec_to_gpm: float,
    fault_threshold_gpm: float,
    min_negative_flow_seconds: float,
) -> NegativeFlowStatus:
    """Evaluate sustained reverse flow from the signed Picomag flow-rate field."""
    flow_gpm = float(flow_rate_l_per_s) * float(liters_per_sec_to_gpm)
    fault_threshold = abs(float(fault_threshold_gpm))
    min_negative_flow = max(0.0, float(min_negative_flow_seconds))
    negative_flow = flow_gpm <= -fault_threshold
    fault = negative_flow and float(negative_flow_elapsed_seconds) >= min_negative_flow
    reason = ""
    if fault:
        reason = (
            f"NEGATIVE FLOW METER {flow_gpm:.1f} GPM "
            f"FOR {min_negative_flow:.0f}S - GALLON RESET REQUIRED"
        )
    return NegativeFlowStatus(
        flow_gpm=flow_gpm,
        negative_flow=negative_flow,
        fault=fault,
        reason=reason,
    )


def positive_drift_status(
    baseline_totalizer_liters: float,
    current_totalizer_liters: float,
    flow_rate_l_per_s: float,
    low_flow_elapsed_seconds: float,
    liters_to_gallons: float,
    liters_per_sec_to_gpm: float,
    low_flow_threshold_gpm: float,
    drift_threshold_gallons: float,
    min_low_flow_seconds: float,
) -> PositiveDriftStatus:
    """Evaluate unsafe positive totalizer drift while the meter says flow is low."""
    drift_gallons = (
        float(current_totalizer_liters) - float(baseline_totalizer_liters)
    ) * float(liters_to_gallons)
    flow_gpm = float(flow_rate_l_per_s) * float(liters_per_sec_to_gpm)
    low_flow_threshold = abs(float(low_flow_threshold_gpm))
    drift_threshold = abs(float(drift_threshold_gallons))
    min_low_flow = max(0.0, float(min_low_flow_seconds))
    low_flow = flow_gpm < low_flow_threshold
    fault = (
        low_flow
        and float(low_flow_elapsed_seconds) >= min_low_flow
        and drift_gallons > drift_threshold
    )
    reason = ""
    if fault:
        reason = (
            f"FLOW METER DRIFT +{drift_gallons:.1f} GAL "
            f"BELOW {low_flow_threshold:.0f} GPM - GALLON RESET REQUIRED"
        )
    return PositiveDriftStatus(
        drift_gallons=drift_gallons,
        flow_gpm=flow_gpm,
        low_flow=low_flow,
        fault=fault,
        reason=reason,
    )
