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


# =============================================================================
# FLOW METER SETTINGS
# =============================================================================

# IO-Link HAT configuration
IOL_PORT = 2  # Flow meter on X1 (port 0)
DATA_LENGTH = 15              # Expected data length from flow meter

# Flow meter monitoring thresholds
FLOW_STOPPED_THRESHOLD = 0.001    # L/s - flow is considered stopped below this
FLOW_METER_TIMEOUT = 5            # seconds - flow meter considered disconnected after this
IOL_RECONNECT_INTERVAL = 15       # seconds - minimum time between IOL port power-cycle attempts


# =============================================================================
# FLOW MEASUREMENT & CALIBRATION
# =============================================================================

# Unit conversion factors
LITERS_TO_GALLONS = 0.264172      # Liters to gallons conversion
LITERS_PER_SEC_TO_GPM = 15.850323 # L/s to GPM conversion (60 * 0.264172)

# Flow-based shutoff coast calibration
# Use a short rolling average of flow rather than a single instant sample.
FLOW_AVERAGING_SAMPLES = 3  # 3 x 200 ms updates = ~0.6 s average

# Piecewise coast model derived from usable March 2026 auto-shutoff samples.
# Low band samples:
#   - 42.3 GPM -> 1.02 gal coast
#   - 48.1 GPM -> 1.18 gal coast
#   - 59.9 GPM -> 1.45 gal coast
#   - 65.7 GPM -> 1.62 gal coast
# High band samples:
#   - 80.6 GPM -> 1.98 gal coast
#   - 84.4 GPM -> 2.09 gal coast
#   - 85.0 GPM -> 2.12 gal coast
# Refit from the last 11 usable auto loads in fill_calibration.log:
#   Low band (<= 70 GPM): 29.8, 36.8, 49.0, 59.0, 64.4, 65.0, 68.9
#   High band (> 70 GPM): 73.2, 75.4, 78.8, 83.1
# Desired threshold per run is estimated as:
#   corrected_threshold = logged_threshold + (actual - requested)
# so underfills reduce the threshold and overfills increase it.
FLOW_CURVE_SPLIT_GPM = 70.0
FLOW_CURVE_LOW_SLOPE = 0.02543409299521162
FLOW_CURVE_LOW_INTERCEPT = -0.12819618255920154
FLOW_CURVE_HIGH_SLOPE = 0.030867814806530995
FLOW_CURVE_HIGH_INTERCEPT = -0.38336412435696915

# Self-tuning auto-shutoff overlay
# Learns only from confirmed auto fills and stores recent accepted samples.
AUTO_TUNE_ENABLED = True
AUTO_TUNE_MODEL_FILE = "/home/pi/auto_shutoff_tuning.json"
AUTO_TUNE_MIN_SAMPLES = 3
AUTO_TUNE_FULL_CONFIDENCE_SAMPLES = 8
AUTO_TUNE_MAX_SAMPLES = 50
AUTO_TUNE_FLOW_BANDWIDTH_GPM = 12.0
AUTO_TUNE_STABILITY_WINDOW_SECONDS = 3.0
AUTO_TUNE_MAX_FLOW_VARIATION_GPM = 5.0
AUTO_TUNE_MAX_DELTA_GAL = 0.75
AUTO_TUNE_MIN_THRESHOLD_GAL = 0.1
AUTO_TUNE_MAX_THRESHOLD_GAL = 4.0
AUTO_TUNE_TARGET_ERROR_GAL = 0.0
AUTO_TUNE_MAX_SAMPLE_ERROR_GAL = 3.0


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
UPDATE_INTERVAL = 200         # Milliseconds between GUI updates (lower = more responsive)


# =============================================================================
# FILE PATHS & LOGGING
# =============================================================================

# Log file locations (relative to /home/pi/)
MAIN_LOG_FILE = "/home/pi/iol_dashboard.log"
SERIAL_DEBUG_LOG = "/home/pi/serial_debug.log"
RELAY_TEST_LOG = "/home/pi/relay_test.log"


# =============================================================================
# SYSTEM PATHS
# =============================================================================

# Path configurations
IOL_HAT_PATH = "/home/pi/Big-Beautiful-Box"  # Path to IOL-HAT Python library
RPI_GPIO_PATH = "/home/pi/Big-Beautiful-Box"                          # Path to RPi.GPIO wrapper
