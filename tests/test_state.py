#!/usr/bin/env python3
"""
Tests for thread-safe state management.
"""

import sys
import os
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from src.state import DashboardState, get_state


class TestDashboardState:
    """Tests for DashboardState class."""
    
    def test_initial_state(self):
        """State should initialize with defaults."""
        state = DashboardState()
        
        assert state.flow.totalizer_liters == 0.0
        assert state.flow.is_connected is False
        assert state.serial.is_connected is False
        assert state.mode.current_mode == "fill"
        assert state.mode.override_enabled is False
        assert state.totals.daily == 0.0
        assert state.totals.season == 0.0
    
    def test_context_manager(self):
        """Context manager should acquire/release lock."""
        state = DashboardState()
        
        with state as s:
            s.flow.totalizer_liters = 100.0
        
        assert state.flow.totalizer_liters == 100.0
    
    def test_flow_update(self):
        """Flow state should update correctly."""
        state = DashboardState()
        
        state.update_flow(
            totalizer_liters=50.0,
            flow_rate=1.5,
            connected=True
        )
        
        assert state.flow.totalizer_liters == 50.0
        assert state.flow.flow_rate_l_per_s == 1.5
        assert state.flow.is_connected is True
    
    def test_flow_properties(self):
        """Flow computed properties should work."""
        state = DashboardState()
        state.update_flow(totalizer_liters=3.78541, flow_rate=1.0)
        
        # ~1 gallon
        assert abs(state.flow.totalizer_gallons - 1.0) < 0.01
        # ~15.85 GPM
        assert abs(state.flow.flow_rate_gpm - 15.85) < 0.1
        assert state.flow.is_flowing is True
    
    def test_serial_heartbeat(self):
        """Serial heartbeat should update timestamp."""
        state = DashboardState()
        
        assert state.serial.heartbeat_ok is False
        
        state.update_serial(connected=True, heartbeat=True)
        
        assert state.serial.is_connected is True
        assert state.serial.heartbeat_ok is True
    
    def test_mode_switching(self):
        """Mode switching should save/restore presets."""
        state = DashboardState()
        
        # Start in fill mode with 60 gallons
        state.fill.requested_gallons = 60
        state.mode.fill_preset = 60
        state.mode.mix_preset = 40
        
        # Switch to mix
        state.switch_mode("mix")
        
        assert state.mode.current_mode == "mix"
        assert state.fill.requested_gallons == 40
        
        # Switch back to fill
        state.switch_mode("fill")
        
        assert state.mode.current_mode == "fill"
        assert state.fill.requested_gallons == 60
    
    def test_adjust_requested(self):
        """Requested gallons adjustment should work."""
        state = DashboardState()
        state.fill.requested_gallons = 60
        
        new_val = state.adjust_requested(10)
        assert new_val == 70
        
        new_val = state.adjust_requested(-10)
        assert new_val == 60
        
        # Should not go below 0
        new_val = state.adjust_requested(-100)
        assert new_val == 0
    
    def test_totals(self):
        """Totals tracking should work."""
        state = DashboardState()
        
        state.add_to_totals(10.5)
        assert state.totals.daily == 10.5
        assert state.totals.season == 10.5
        
        state.add_to_totals(5.0)
        assert state.totals.daily == 15.5
        assert state.totals.season == 15.5
        
        state.reset_daily_total()
        assert state.totals.daily == 0.0
        assert state.totals.season == 15.5
        
        state.reset_season_total()
        assert state.totals.season == 0.0
    
    def test_override_mode(self):
        """Override mode should track enable time."""
        state = DashboardState()
        
        state.set_override(True)
        assert state.mode.override_enabled is True
        assert state.mode.override_enabled_time > 0
        
        state.set_override(False)
        assert state.mode.override_enabled is False


class TestThreadSafety:
    """Tests for thread safety."""
    
    def test_concurrent_updates(self):
        """Concurrent updates should not corrupt state."""
        state = DashboardState()
        errors = []
        iterations = 100
        
        def updater(thread_id):
            try:
                for i in range(iterations):
                    state.update_flow(totalizer_liters=float(i + thread_id * 1000))
                    state.add_to_totals(0.1)
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=updater, args=(i,)) for i in range(5)]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0
        # 5 threads * 100 iterations * 0.1 = 50.0
        assert abs(state.totals.daily - 50.0) < 0.01


class TestGlobalState:
    """Tests for global state singleton."""
    
    def test_singleton(self):
        """get_state should return same instance."""
        s1 = get_state()
        s2 = get_state()
        assert s1 is s2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
