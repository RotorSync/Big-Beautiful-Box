#!/usr/bin/env python3
"""
Pure calculation functions for the IOL Dashboard.

These functions have no side effects and are fully testable.
"""

import config


def calculate_trigger_threshold(flow_rate_l_per_s: float) -> float:
    """
    Calculate shutoff trigger threshold based on flow rate.
    
    Uses calibration curve to predict coast distance after relay activation.
    
    Args:
        flow_rate_l_per_s: Current flow rate in liters per second
        
    Returns:
        Gallons before target to trigger shutoff (minimum 0.1)
    """
    flow_rate_gpm = flow_rate_l_per_s * config.LITERS_PER_SEC_TO_GPM
    predicted_coast = config.FLOW_CURVE_SLOPE * flow_rate_gpm + config.FLOW_CURVE_INTERCEPT
    return max(predicted_coast, 0.1)


def liters_to_gallons(liters: float) -> float:
    """Convert liters to gallons."""
    return liters * config.LITERS_TO_GALLONS


def gallons_to_liters(gallons: float) -> float:
    """Convert gallons to liters."""
    return gallons / config.LITERS_TO_GALLONS


def l_per_s_to_gpm(l_per_s: float) -> float:
    """Convert liters per second to gallons per minute."""
    return l_per_s * config.LITERS_PER_SEC_TO_GPM


def gpm_to_l_per_s(gpm: float) -> float:
    """Convert gallons per minute to liters per second."""
    return gpm / config.LITERS_PER_SEC_TO_GPM


def is_flow_stopped(flow_rate_l_per_s: float) -> bool:
    """Check if flow is considered stopped."""
    return flow_rate_l_per_s < config.FLOW_STOPPED_THRESHOLD


def is_over_target(actual: float, requested: float, threshold: float = 0.0) -> bool:
    """Check if actual gallons exceeds target by threshold."""
    return actual > requested + threshold


def should_trigger_alert(
    actual_gallons: float,
    requested_gallons: float,
    flow_rate_l_per_s: float,
    override_mode: bool = False,
    already_triggered: bool = False
) -> bool:
    """
    Determine if auto-alert should be triggered.
    
    Args:
        actual_gallons: Current totalizer reading
        requested_gallons: Target fill amount
        flow_rate_l_per_s: Current flow rate
        override_mode: Whether override is enabled
        already_triggered: Whether alert already fired this cycle
        
    Returns:
        True if alert should trigger
    """
    if override_mode or already_triggered:
        return False
    
    threshold = calculate_trigger_threshold(flow_rate_l_per_s)
    return actual_gallons >= (requested_gallons - threshold)


def format_gallons(gallons: float, decimals: int = 1) -> str:
    """Format gallons for display."""
    if decimals == 0 or gallons == int(gallons):
        return f"{int(gallons)}"
    return f"{gallons:.{decimals}f}"
