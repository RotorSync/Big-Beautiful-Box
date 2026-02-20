#!/usr/bin/env python3
"""
Tests for calculation functions.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from src.calculations import (
    calculate_trigger_threshold,
    liters_to_gallons,
    gallons_to_liters,
    l_per_s_to_gpm,
    gpm_to_l_per_s,
    is_flow_stopped,
    is_over_target,
    should_trigger_alert,
    format_gallons,
)
import config


class TestUnitConversions:
    """Tests for unit conversion functions."""
    
    def test_liters_to_gallons(self):
        assert liters_to_gallons(0) == 0
        assert abs(liters_to_gallons(1) - 0.264172) < 0.0001
        assert abs(liters_to_gallons(3.78541) - 1.0) < 0.001  # ~1 gallon
    
    def test_gallons_to_liters(self):
        assert gallons_to_liters(0) == 0
        assert abs(gallons_to_liters(1) - 3.78541) < 0.001
    
    def test_l_per_s_to_gpm(self):
        assert l_per_s_to_gpm(0) == 0
        # 1 L/s = ~15.85 GPM
        assert abs(l_per_s_to_gpm(1) - 15.850323) < 0.001
    
    def test_gpm_to_l_per_s(self):
        assert gpm_to_l_per_s(0) == 0
        # Round-trip conversion
        assert abs(gpm_to_l_per_s(l_per_s_to_gpm(1.5)) - 1.5) < 0.0001


class TestFlowThreshold:
    """Tests for flow threshold calculations."""
    
    def test_minimum_threshold(self):
        """Threshold should never be below 0.1 gallons."""
        assert calculate_trigger_threshold(0) >= 0.1
        assert calculate_trigger_threshold(-1) >= 0.1
    
    def test_threshold_increases_with_flow(self):
        """Higher flow rate should give higher threshold."""
        low_flow = calculate_trigger_threshold(0.5)  # ~8 GPM
        high_flow = calculate_trigger_threshold(2.0)  # ~32 GPM
        assert high_flow > low_flow
    
    def test_calibration_points(self):
        """Test against known calibration data points."""
        # From config: 22 GPM → 0.45 gal coast, 70 GPM → 1.92 gal coast
        # 22 GPM = 22 / 15.85 = 1.388 L/s
        threshold_22gpm = calculate_trigger_threshold(22 / config.LITERS_PER_SEC_TO_GPM)
        # Should be close to 0.45 (within calibration tolerance)
        assert 0.3 < threshold_22gpm < 0.6
        
        # 70 GPM = 70 / 15.85 = 4.416 L/s
        threshold_70gpm = calculate_trigger_threshold(70 / config.LITERS_PER_SEC_TO_GPM)
        # Should be close to 1.92
        assert 1.5 < threshold_70gpm < 2.3


class TestFlowDetection:
    """Tests for flow state detection."""
    
    def test_flow_stopped_at_zero(self):
        assert is_flow_stopped(0) is True
    
    def test_flow_stopped_below_threshold(self):
        assert is_flow_stopped(config.FLOW_STOPPED_THRESHOLD - 0.0001) is True
    
    def test_flow_active_above_threshold(self):
        assert is_flow_stopped(config.FLOW_STOPPED_THRESHOLD + 0.001) is False
    
    def test_flow_active_at_normal_rate(self):
        assert is_flow_stopped(1.0) is False  # 1 L/s is definitely flowing


class TestTargetDetection:
    """Tests for target/overfill detection."""
    
    def test_not_over_target(self):
        assert is_over_target(50, 60) is False
        assert is_over_target(60, 60) is False
    
    def test_over_target(self):
        assert is_over_target(61, 60) is True
        assert is_over_target(60.1, 60) is True
    
    def test_over_target_with_threshold(self):
        assert is_over_target(61, 60, threshold=2) is False
        assert is_over_target(63, 60, threshold=2) is True


class TestAlertTrigger:
    """Tests for auto-alert trigger logic."""
    
    def test_no_trigger_in_override_mode(self):
        """Override mode should prevent triggering."""
        assert should_trigger_alert(
            actual_gallons=59,
            requested_gallons=60,
            flow_rate_l_per_s=1.0,
            override_mode=True,
            already_triggered=False
        ) is False
    
    def test_no_trigger_if_already_triggered(self):
        """Should not re-trigger if already fired."""
        assert should_trigger_alert(
            actual_gallons=59,
            requested_gallons=60,
            flow_rate_l_per_s=1.0,
            override_mode=False,
            already_triggered=True
        ) is False
    
    def test_trigger_near_target(self):
        """Should trigger when actual approaches target."""
        # At 1 L/s (~16 GPM), threshold is ~0.27 gal
        # So at 59.8 gal with 60 target, should trigger
        assert should_trigger_alert(
            actual_gallons=59.8,
            requested_gallons=60,
            flow_rate_l_per_s=1.0,
            override_mode=False,
            already_triggered=False
        ) is True
    
    def test_no_trigger_well_below_target(self):
        """Should not trigger when well below target."""
        assert should_trigger_alert(
            actual_gallons=50,
            requested_gallons=60,
            flow_rate_l_per_s=1.0,
            override_mode=False,
            already_triggered=False
        ) is False


class TestFormatting:
    """Tests for display formatting."""
    
    def test_format_whole_number(self):
        assert format_gallons(60.0, decimals=1) == "60"
        assert format_gallons(60, decimals=0) == "60"
    
    def test_format_with_decimals(self):
        assert format_gallons(60.5, decimals=1) == "60.5"
        assert format_gallons(60.55, decimals=2) == "60.55"
    
    def test_format_zero(self):
        assert format_gallons(0) == "0"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
