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
PUMP_STOP_RELAY_PIN = 27      # GPIO pin for pump stop/alert relay
GREEN_BUTTON_PIN = 22         # GPIO pin for green button (active low with pull-up)


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
AUTO_ALERT_DURATION = 10      # How long to hold relay on for auto-alert


# =============================================================================
# FLOW METER SETTINGS
# =============================================================================

# IO-Link HAT configuration
IOL_PORT = 1                  # Port 2 in the system (Port 1 in code - 0-indexed)
DATA_LENGTH = 15              # Expected data length from flow meter

# Flow meter monitoring thresholds
FLOW_STOPPED_THRESHOLD = 0.001    # L/s - flow is considered stopped below this
FLOW_METER_TIMEOUT = 5            # seconds - flow meter considered disconnected after this


# =============================================================================
# FLOW MEASUREMENT & CALIBRATION
# =============================================================================

# Unit conversion factors
LITERS_TO_GALLONS = 0.264172      # Liters to gallons conversion
LITERS_PER_SEC_TO_GPM = 15.850323 # L/s to GPM conversion (60 * 0.264172)

# Flow-based shutoff curve coefficients (based on calibration data)
# Coast distance (gallons) = FLOW_CURVE_SLOPE * flow_rate_gpm + FLOW_CURVE_INTERCEPT
# Calibration data:
#   - 22 GPM → 0.45 gal coast
#   - 70 GPM → 1.75 gal coast
# (Shut off 0.05 gal later at low flow, 0.05 gal sooner at high flow)
FLOW_CURVE_SLOPE = 0.0270833333       # Slope of coast distance vs flow rate
FLOW_CURVE_INTERCEPT = -0.14583333    # Y-intercept of coast distance curve


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

# Log file locations (relative to /home/user/)
MAIN_LOG_FILE = "/home/user/iol_dashboard.log"
SERIAL_DEBUG_LOG = "/home/user/serial_debug.log"
RELAY_TEST_LOG = "/home/user/relay_test.log"


# =============================================================================
# SYSTEM PATHS
# =============================================================================

# Path configurations
IOL_HAT_PATH = "/home/user/iol-hat/examples/python"  # Path to IOL-HAT Python library
RPI_GPIO_PATH = "/home/user"                          # Path to RPi.GPIO wrapper
