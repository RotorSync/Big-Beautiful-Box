#!/usr/bin/env python3
"""
Simple test runner that works without pytest.
For full test suite, use pytest in CI or with venv.
"""

import sys
import os
import traceback
import tempfile

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def run_basic_tests():
    """Run basic sanity tests."""
    errors = []
    passed = 0
    
    print("=" * 60)
    print("BBB Basic Test Suite")
    print("=" * 60)
    
    # Test 1: Config imports
    print("\n[TEST] Config imports...", end=" ")
    try:
        import config
        assert hasattr(config, 'FLOW_STOPPED_THRESHOLD')
        assert hasattr(config, 'LITERS_TO_GALLONS')
        print("✓ PASS")
        passed += 1
    except Exception as e:
        print(f"✗ FAIL: {e}")
        errors.append(("Config imports", e))
    
    # Test 2: Calculations module
    print("[TEST] Calculations module...", end=" ")
    try:
        from src.calculations import (
            calculate_trigger_threshold,
            calculate_trigger_threshold_gpm,
            liters_to_gallons,
            is_flow_stopped,
            should_trigger_alert
        )
        from src.auto_shutoff import AutoShutoffTuningModel
        import config
        
        # Basic checks
        assert liters_to_gallons(0) == 0
        assert abs(liters_to_gallons(1) - 0.264172) < 0.001
        assert calculate_trigger_threshold(0) >= 0.1
        assert calculate_trigger_threshold_gpm(42.3) > 0.8
        assert calculate_trigger_threshold_gpm(84.4) > calculate_trigger_threshold_gpm(42.3)
        assert is_flow_stopped(0) is True
        assert is_flow_stopped(1.0) is False
        with tempfile.TemporaryDirectory() as temp_dir:
            model = AutoShutoffTuningModel(os.path.join(temp_dir, "auto_tune.json"))
            base_threshold = calculate_trigger_threshold_gpm(60.0)
            manual_result = model.record_confirmed_fill(
                "Manual", 60.0, 100.0, 100.5, base_threshold, 0.0
            )
            assert manual_result.skipped is True
            for _ in range(config.AUTO_TUNE_MIN_SAMPLES):
                learn_result = model.record_confirmed_fill(
                    "Auto", 60.0, 100.0, 100.5, base_threshold, 0.0
                )
                assert learn_result.accepted is True
            tuned = model.calculate_threshold(60.0 / config.LITERS_PER_SEC_TO_GPM)
            assert tuned.final_threshold_gal > tuned.base_threshold_gal
            unstable = model.record_confirmed_fill(
                "Auto",
                60.0,
                100.0,
                100.1,
                base_threshold,
                config.AUTO_TUNE_MAX_FLOW_VARIATION_GPM + 0.5,
            )
            assert unstable.accepted is False
        
        print("✓ PASS")
        passed += 1
    except Exception as e:
        print(f"✗ FAIL: {e}")
        errors.append(("Calculations", e))
    
    # Test 3: State module
    print("[TEST] State module...", end=" ")
    try:
        from src.state import DashboardState, get_state
        
        state = DashboardState()
        assert state.mode.current_mode == "fill"
        
        state.update_flow(totalizer_liters=100.0)
        assert state.flow.totalizer_liters == 100.0
        
        state.add_to_totals(10.0)
        assert state.totals.daily == 10.0
        
        print("✓ PASS")
        passed += 1
    except Exception as e:
        print(f"✗ FAIL: {e}")
        errors.append(("State", e))
    
    # Test 4: Flow handler imports
    print("[TEST] Flow handler imports...", end=" ")
    try:
        from src.flow_handler import FlowHandler, FlowReading
        assert FlowReading is not None
        print("✓ PASS")
        passed += 1
    except Exception as e:
        print(f"✗ FAIL: {e}")
        errors.append(("Flow handler", e))
    
    # Test 5: Serial handler imports
    print("[TEST] Serial handler imports...", end=" ")
    try:
        from src.serial_handler import SerialHandler, SerialCommand
        assert SerialCommand.HEARTBEAT.value == "OK"
        print("✓ PASS")
        passed += 1
    except Exception as e:
        print(f"✗ FAIL: {e}")
        errors.append(("Serial handler", e))
    
    # Test 6: GPIO handler imports
    print("[TEST] GPIO handler imports...", end=" ")
    try:
        from src.gpio_handler import GPIOHandler
        print("✓ PASS")
        passed += 1
    except Exception as e:
        print(f"✗ FAIL: {e}")
        errors.append(("GPIO handler", e))
    
    # Summary
    total = passed + len(errors)
    print("\n" + "=" * 60)
    print(f"Results: {passed}/{total} passed")
    
    if errors:
        print("\nFailures:")
        for name, err in errors:
            print(f"  - {name}: {err}")
        return 1
    else:
        print("\n✓ All basic tests passed!")
        return 0


if __name__ == "__main__":
    sys.exit(run_basic_tests())
