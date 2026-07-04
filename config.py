#!/usr/bin/env python3
"""
IOL Dashboard Configuration File

This file contains all user-configurable settings for the IOL Dashboard system.
Edit values here to customize behavior without modifying the main code.
"""

# =============================================================================
# GPIO PIN ASSIGNMENTS
# =============================================================================

# GPIO pin assignments (BCM mode)
FLOW_RESET_PIN = 0            # GPIO pin for flow meter reset relay (K3)
PUMP_STOP_RELAY_PIN = 27      # GPIO pin for pump stop/alert relay
GREEN_BUTTON_PIN = 22         # GPIO pin for green button (active low with pull-up)

# Flow meter reset timing
FLOW_RESET_DELAY = 30         # Seconds to wait after thumbs up before resetting
FLOW_RESET_DURATION = 0.2     # Seconds to hold reset relay closed


# =============================================================================
# SERIAL COMMUNICATION
# =============================================================================

# RS485 serial interface settings
SERIAL_PORT = "/dev/ttyAMA0"  # Primary UART on GPIO 14/15 (physical pins 8/10)
SERIAL_BAUD = 115200          # Serial baud rate (must match sender device)


# =============================================================================
# RELAY TIMING
# =============================================================================

# Duration settings for relay activation (in seconds)
PUMP_STOP_DURATION = 5       # How long to hold relay on for PS (Pump Stop) command
AUTO_ALERT_DURATION = 5      # How long to hold relay on for auto-alert
RELAY_SLOWDOWN_CHECK_SECONDS = 2.0  # seconds after auto-stop to verify flow is slowing
RELAY_SLOWDOWN_MIN_DROP_GPM = 10.0  # GPM drop required after relay trigger
RELAY_SLOWDOWN_MIN_DROP_FRACTION = 0.20  # fractional drop required after relay trigger
RELAY_SLOWDOWN_ALARM_FLASH_HZ = 20  # target full-screen flash rate
RELAY_SLOWDOWN_ALARM_COLOR_A = "black"
RELAY_SLOWDOWN_ALARM_COLOR_B = "white"


# =============================================================================
# FLOW METER SETTINGS
# =============================================================================

# IO-Link HAT configuration
IOL_PORT = 2  # Flow meter on X1 (port 0)
DATA_LENGTH = 15              # Expected data length from flow meter

# Flow meter monitoring thresholds
FLOW_STOPPED_THRESHOLD = 0.001       # L/s - flow is considered stopped below this
NEW_FILL_CYCLE_THRESHOLD = 0.630902  # L/s - 10 GPM; clear thumbs/pending fill above this
NEW_FILL_CYCLE_HOLD_SECONDS = 3.0    # seconds - require sustained new-fill flow
NEW_FILL_CYCLE_FRESH_GRACE_SECONDS = 0.25  # seconds - latest high-flow sample must be fresh/recent
FLOW_METER_TIMEOUT = 5            # seconds - flow meter considered disconnected after this
FLOW_METER_RECONNECT_FRESH_READS = 3  # healthy status checks required before clearing flow-meter fault latch
FLOW_METER_RECONNECT_STABLE_SECONDS = 10.0  # continuous healthy time before clearing flow-meter fault latch
FLOW_METER_NEGATIVE_TOTALIZER_FAULT_GALLONS = 1.0  # latch pump stop if signed totalizer drifts below this
FLOW_METER_NEGATIVE_TOTALIZER_CLEAR_GALLONS = 0.05  # require reset back near zero before clearing the fault
FLOW_METER_NEGATIVE_FLOW_FAULT_GPM = 0.25  # ignore tiny signed-flow noise around zero
FLOW_METER_NEGATIVE_FLOW_SECONDS = 5.0  # latch if signed flow stays negative this long
FLOW_METER_POSITIVE_DRIFT_FAULT_GALLONS = 3.0  # latch if idle/low-flow totalizer climbs more than this
FLOW_METER_POSITIVE_DRIFT_LOW_FLOW_GPM = 15.0  # below this, totalizer gain is treated as drift
FLOW_METER_POSITIVE_DRIFT_SECONDS = 10.0  # low-flow duration required before positive drift can fault
IOL_STARTUP_WARNING_GRACE_SECONDS = 15.0  # hide startup-only IO-Link warnings while the port settles
IOL_RECONNECT_INTERVAL = 15       # seconds - minimum time between IOL port power-cycle attempts


# =============================================================================
# FLOW MEASUREMENT & CALIBRATION
# =============================================================================

# Unit conversion factors
LITERS_TO_GALLONS = 0.264172      # Liters to gallons conversion
LITERS_PER_SEC_TO_GPM = 15.850323 # L/s to GPM conversion (60 * 0.264172)

# Flow-based shutoff coast calibration
# Use a short rolling average of flow rather than a single instant sample.
FLOW_AVERAGING_SAMPLES = 3  # 3 fresh Picomag samples; independent of control-loop poll rate

# Safety-critical auto-shutoff loop. Keep IO-Link reads and relay decisions out
# of the Tk render loop so GUI timing cannot move the pump stop point.
FLOW_CONTROL_THREAD_ENABLED = True
FLOW_CONTROL_INTERVAL = 0.020  # seconds; test 50 Hz polling against ~12.7 Hz fresh Picomag data
FLOW_CONTROL_PREDICTION_SECONDS = 0.0  # keep curve behavior unchanged by default
FLOW_CONTROL_AUDIT_INTERVAL = 5.0  # seconds between flow-meter freshness summaries

# Piecewise coast model refit from 25 thumbs-up-confirmed Auto fills logged
# after 2026-05-28 17:03 during the curve test:
#   Low band (<= 70 GPM): 38.4, 39.5, 39.1, 47.9, 48.8, 47.1,
#                         47.8, 58.9, 58.8, 58.8
#   High band (> 70 GPM): 73.1, 72.6, 72.8, 72.6, 72.7, 72.2, 73.0,
#                         86.7, 87.3, 86.6, 85.9, 85.5, 100.7, 100.6, 100.4
# Desired threshold per run is estimated as:
#   corrected_threshold = logged_threshold + (actual - requested)
# so underfills reduce the threshold and overfills increase it.
FLOW_CURVE_SPLIT_GPM = 70.0
FLOW_CURVE_LOW_SLOPE = 0.028133462493840494
FLOW_CURVE_LOW_INTERCEPT = 0.006145734423797622
FLOW_CURVE_HIGH_SLOPE = 0.02735502035885965
FLOW_CURVE_HIGH_INTERCEPT = 0.13052774666967393

# Runtime curve learning. Factory values above remain the fallback; learned
# values are saved outside the repo so they can be reset in the field.
FLOW_CURVE_OVERRIDE_FILE = "/home/pi/flow_curve_override.json"
FLOW_CURVE_SAMPLES_FILE = "/home/pi/flow_curve_samples.json"
FLOW_CURVE_PROPOSAL_FILE = "/home/pi/flow_curve_proposal.json"
FLOW_CURVE_LEARN_SAMPLE_COUNT = 3
FLOW_CURVE_MAX_LEARNED_OFFSET_GAL = 0.75
FLOW_CURVE_MAX_SAMPLE_ERROR_GAL = 5.0


# =============================================================================
# FILL CONTROL
# =============================================================================

# Target fill amount and warnings
REQUESTED_GALLONS = 60        # Default target fill amount (can be changed via serial)
WARNING_THRESHOLD = 2         # Gallons before target to show warning color


# =============================================================================
# DISPLAY & GUI SETTINGS
# =============================================================================

# User interface settings
UPDATE_INTERVAL = 150         # Milliseconds between GUI updates (lower = more responsive)


# =============================================================================
# FILE PATHS & LOGGING
# =============================================================================

# Log file locations (relative to /home/pi/)
MAIN_LOG_FILE = "/home/pi/iol_dashboard.log"
SERIAL_DEBUG_LOG = "/home/pi/serial_debug.log"
RELAY_TEST_LOG = "/home/pi/relay_test.log"
BUTTON_DEBUG_LOG = "/home/pi/button_debug.log"
FLOW_CONTROL_LOG_FILE = "/home/pi/flow_control.log"


# =============================================================================
# SYSTEM PATHS
# =============================================================================

# Path configurations
IOL_HAT_PATH = "/home/pi/Big-Beautiful-Box"  # Path to IOL-HAT Python library
RPI_GPIO_PATH = "/home/pi/Big-Beautiful-Box"                          # Path to RPi.GPIO wrapper
