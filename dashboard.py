#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk
import time
import sys
import struct
import socket
import serial
import threading
import subprocess
import os
from PIL import Image, ImageTk

# Version
VERSION = "v1.9.4"

# Import configuration
import config

# Set up rotating loggers
from src.logger import get_main_logger, get_serial_logger, get_button_logger, get_relay_logger
main_logger = get_main_logger()
serial_logger = get_serial_logger()
button_logger = get_button_logger()
relay_logger = get_relay_logger()

# Add paths for libraries
sys.path.insert(0, config.RPI_GPIO_PATH)
sys.path.insert(0, config.IOL_HAT_PATH)

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("WARNING: RPi.GPIO not available, relay control disabled")

import iolhat

# Global variables
last_totalizer_liters = 0.0
last_flow_rate = 0.0
connection_error = False
error_message = ""
requested_gallons = config.REQUESTED_GALLONS
serial_connected = False
override_mode = False
last_alert_triggered = False
last_successful_read_time = time.time()
was_flowing = False  # Track if flow was active in previous update (for detecting flow stop)
colors_are_green = False  # Track if colors have been changed to green
last_reminder_date = None  # Track the last date reminders were shown (YYYY-MM-DD format)
reminders_mode = False  # Track if we're showing reminders
reminders_window = None  # Reference to reminders window
menu_mode = False  # Track if we're in menu mode
menu_window = None  # Reference to menu window
menu_selected_index = 0  # Currently selected menu item (0=logs, 1=self-test, 2=update, 3=shutdown, 4=reboot, 5=exit-desktop, 6=exit-menu)
menu_buttons = []  # List of menu button widgets
menu_arrows = []  # List of arrow label widgets
menu_daily_label = None  # Reference to daily total label in menu
menu_season_label = None  # Reference to season total label in menu
menu_position_label = None  # Reference to position indicator label
log_viewer_mode = False  # Track if we're in log viewer
log_viewer_window = None  # Reference to log viewer window
log_viewer_text = None  # Reference to log text widget
fill_history_mode = False  # Track if we're in fill history viewer
fill_history_window = None  # Reference to fill history window
fill_history_text = None  # Reference to fill history text widget
self_test_mode = False  # Track if we're in self-test
self_test_window = None  # Reference to self-test window
full_test_mode = False  # Track if we're in full-test
full_test_window = None  # Reference to full-test window
update_mode = False  # Track if we're in update screen
update_window = None  # Reference to update window
serial_command_received = False  # Track if any serial command has been received (for color change)
exit_confirm_window = None  # Reference to exit confirmation window
exit_confirm_handler = None  # Function to call on confirmation
exit_cancel_handler = None  # Function to call on cancel
reset_season_confirm_window = None  # Reference to reset season confirmation window
reset_season_confirm_handler = None  # Function to call on confirmation
reset_season_cancel_handler = None  # Function to call on cancel
daily_total = 0.0  # Total gallons pumped today
season_total = 0.0  # Total gallons pumped this season (until manually reset)
last_reset_date = None  # Track last daily reset date
pending_fill_gallons = 0.0  # Gallons from last fill, waiting for thumbs up confirmation
pending_fill_requested = 0.0  # Requested gallons from last fill
pending_fill_shutoff_type = ""  # Shutoff type from last fill
last_heartbeat_time = time.time()  # Last time we received OK heartbeat from switch box
heartbeat_disconnected = False  # Track if heartbeat has timed out
consecutive_identical_raw = 0  # Track byte-for-byte identical reads
last_raw_data = None  # Previous raw bytes for stale detection
STALE_RAW_THRESHOLD = 25  # Identical raw reads before flagging (25 * 200ms = 5 seconds)
last_power_cycle_time = 0         # Timestamp of last IOL power-cycle attempt
iol_power_cycle_in_progress = False  # Flag to prevent overlapping power-cycle threads
override_enabled_time = 0  # Timestamp when override mode was last enabled
# Mix/Fill mode variables
current_mode = "fill"  # Current mode: "fill" or "mix"
fill_requested_gallons = config.REQUESTED_GALLONS  # Preset for fill mode
mix_requested_gallons = 40  # Preset for mix mode (default 40)
mode_indicator_label = None  # Label to display "MIX" in corner

# Mopeka tank level display
mopeka1_gallons = 0
mopeka2_gallons = 0
mopeka1_quality = 0
mopeka2_quality = 0
mopeka_connected = False


# Batch mix data from iPad (cached)
batch_mix_data = None  # Cached JSON data from iPad
batch_mix_overlay = None  # Reference to batch mix overlay frame

def calculate_trigger_threshold(flow_rate_l_per_s):
    """
    Calculate how many gallons before target to trigger shutoff based on flow rate.
    Uses calibration data to predict coast distance after relay activation.

    Args:
        flow_rate_l_per_s: Current flow rate in liters per second

    Returns:
        Gallons before target to trigger shutoff (predicted coast distance)
    """
    # Convert L/s to GPM
    flow_rate_gpm = flow_rate_l_per_s * config.LITERS_PER_SEC_TO_GPM

    # Calculate predicted coast distance using calibration curve
    # coast_distance = 0.0270833333 * flow_rate_gpm - 0.14583333
    predicted_coast = config.FLOW_CURVE_SLOPE * flow_rate_gpm + config.FLOW_CURVE_INTERCEPT

    # Ensure we don't have negative threshold (minimum 0.1 gallon before target)
    threshold = max(predicted_coast, 0.1)

    return threshold

def load_totals():
    """Load daily and season totals from files"""
    global daily_total, season_total, last_reset_date

    # Load daily total
    try:
        with open('/home/pi/daily_total.txt', 'r') as f:
            lines = f.readlines()
            if len(lines) >= 2:
                daily_total = float(lines[0].strip())
                last_reset_date = lines[1].strip()
    except Exception:
        daily_total = 0.0
        last_reset_date = None

    # Load season total
    try:
        with open('/home/pi/season_total.txt', 'r') as f:
            season_total = float(f.read().strip())
    except Exception:
        season_total = 0.0

def save_totals():
    """Save daily and season totals to files"""
    global daily_total, season_total, last_reset_date

    # Save daily total with date
    try:
        with open('/home/pi/daily_total.txt', 'w') as f:
            f.write(f"{daily_total}\n")
            f.write(f"{last_reset_date}\n")
    except Exception as e:
        print(f"Error saving daily total: {e}")

    # Save season total
    try:
        with open('/home/pi/season_total.txt', 'w') as f:
            f.write(f"{season_total}\n")
    except Exception as e:
        print(f"Error saving season total: {e}")

def load_mode_presets():
    """Load fill and mix mode gallon presets from file"""
    global fill_requested_gallons, mix_requested_gallons, current_mode, batch_mix_data

    try:
        with open('/home/pi/mode_presets.txt', 'r') as f:
            lines = f.readlines()
            if len(lines) >= 3:
                fill_requested_gallons = float(lines[0].strip())
                mix_requested_gallons = float(lines[1].strip())
                current_mode = lines[2].strip()
                if current_mode not in ['fill', 'mix']:
                    current_mode = 'fill'
    except Exception:
        fill_requested_gallons = config.REQUESTED_GALLONS
        mix_requested_gallons = 40
        current_mode = 'fill'

def save_mode_presets():
    """Save fill and mix mode gallon presets to file"""
    global fill_requested_gallons, mix_requested_gallons, current_mode, batch_mix_data

    try:
        with open('/home/pi/mode_presets.txt', 'w') as f:
            f.write(f"{fill_requested_gallons}\n")
            f.write(f"{mix_requested_gallons}\n")
            f.write(f"{current_mode}\n")
    except Exception as e:
        print(f"Error saving mode presets: {e}")

def switch_mode(new_mode):
    """Switch between fill and mix modes"""
    global current_mode, requested_gallons, fill_requested_gallons, mix_requested_gallons
    global mode_indicator_label, colors_are_green, serial_command_received, batch_mix_layout_active

    if new_mode == current_mode:
        return  # Already in this mode

    # Save current requested gallons to the current mode
    if current_mode == 'fill':
        fill_requested_gallons = requested_gallons
    else:
        mix_requested_gallons = requested_gallons

    # Switch to new mode and load its preset
    current_mode = new_mode
    if current_mode == 'fill':
        requested_gallons = fill_requested_gallons
    else:
        requested_gallons = mix_requested_gallons

    # Reset color state for new fill
    colors_are_green = False
    serial_command_received = False

    # Update mode indicator
    if mode_indicator_label:
        if current_mode == 'mix':
            mode_indicator_label.place(relx=0.02, rely=0.02, anchor="nw")
        else:
            mode_indicator_label.place_forget()

    # Save presets
    save_mode_presets()

    # Update the display
    draw_requested_number(f"{requested_gallons:.0f}", "red")

    print(f"Switched to {current_mode.upper()} mode - requested gallons: {requested_gallons}")

    # Show/hide batch mix overlay based on mode
    update_batch_mix_overlay()

# Track if batch mix layout is active
batch_mix_layout_active = False

# Cache for preventing flicker - only redraw when values change
_last_requested_text = None
_last_requested_color = None
_last_actual_text = None
_last_actual_color = None

def update_batch_mix_overlay():
    """Update the batch mix screen layout based on mode and data"""
    global batch_mix_layout_active, batch_mix_data

    # Only show batch mix layout in mix mode with data
    if current_mode == "mix" and batch_mix_data is not None:
        if not batch_mix_layout_active:
            activate_batch_mix_layout()
        else:
            refresh_batch_mix_products()
            refresh_batch_mix_totals()
    else:
        if batch_mix_layout_active:
            deactivate_batch_mix_layout()

def show_batchmix_error(error_msg):
    """Display a BatchMix error message on screen"""
    canvas.delete("batchmix_error")

    width = canvas.winfo_width()
    height = canvas.winfo_height()

    # Red background box
    canvas.create_rectangle(width * 0.1, height * 0.3, width * 0.9, height * 0.5,
                           fill="darkred", outline="red", width=3, tags="batchmix_error")

    # Error title
    canvas.create_text(width // 2, height * 0.35, text="BATCHMIX ERROR",
                      font=("Helvetica", 28, "bold"), fill="white", tags="batchmix_error")

    # Error message
    canvas.create_text(width // 2, height * 0.43, text=error_msg,
                      font=("Helvetica", 20), fill="yellow", tags="batchmix_error")

    # Auto-clear after 5 seconds
    canvas.after(5000, lambda: canvas.delete("batchmix_error"))

def activate_batch_mix_layout():
    """Switch to batch mix screen layout"""
    global batch_mix_layout_active

    # Clear existing labels and redraw in new positions
    canvas.delete("labels")
    canvas.delete("batchmix")

    canvas.update()
    width = canvas.winfo_width()
    height = canvas.winfo_height()

    # Left 1/3 section - center point
    left_center_x = width // 6

    # Draw "Requested:" label on left side
    canvas.create_text(left_center_x, int(height * 0.08), text="Requested:",
                      font=("Helvetica", 28, "bold"), fill="white", tags="labels")

    # Draw "Actual:" label on left side
    canvas.create_text(left_center_x, int(height * 0.38), text="Actual:",
                      font=("Helvetica", 28, "bold"), fill="white", tags="labels")

    # Separator line above totals (bottom 1/4)
    canvas.create_line(0, int(height * 0.75), width, int(height * 0.75),
                      fill="cyan", width=2, tags="batchmix")

    # Vertical separator between left and right sections
    canvas.create_line(width // 3, 0, width // 3, int(height * 0.75),
                      fill="cyan", width=2, tags="batchmix")

    # Products section title (right 2/3, top area)
    products_x = width * 2 // 3
    canvas.create_text(products_x, int(height * 0.05), text="PRODUCTS",
                      font=("Helvetica", 32, "bold"), fill="lime", tags="batchmix")

    # Draw products and totals
    refresh_batch_mix_products()
    refresh_batch_mix_totals()

    # Redraw the numbers in new positions
    redraw_numbers_for_batch_mix()

    batch_mix_layout_active = True

def refresh_batch_mix_totals():
    """Draw/update totals section at bottom of screen"""
    global batch_mix_data

    canvas.delete("totals")

    if batch_mix_data is None:
        return

    canvas.update()
    width = canvas.winfo_width()
    height = canvas.winfo_height()

    # Bottom 1/4 section - totals info
    bottom_y = int(height * 0.85)
    label_y = bottom_y + 35

    totals_data = [
        (width * 0.12, f"{batch_mix_data.get('total_acres', 0):.1f}", "ACRES"),
        (width * 0.37, f"{batch_mix_data.get('gallons_per_acre', 0):.1f}", "GAL/AC"),
        (width * 0.62, f"{batch_mix_data.get('total_liquid', 0):.1f}", "TOTAL GAL"),
        (width * 0.87, f"{batch_mix_data.get('water_needed', 0):.1f}", "WATER"),
    ]

    for x, value, label in totals_data:
        # Value - large cyan text
        canvas.create_text(x, bottom_y, text=value, font=("Helvetica", 44, "bold"),
                          fill="cyan", tags="totals")
        # Label below - smaller gray text
        canvas.create_text(x, label_y, text=label, font=("Helvetica", 16),
                          fill="gray", tags="totals")

def refresh_batch_mix_products():
    """Draw/update products list on canvas"""
    global batch_mix_data

    canvas.delete("products")

    if batch_mix_data is None:
        return

    canvas.update()
    width = canvas.winfo_width()
    height = canvas.winfo_height()

    products = batch_mix_data.get("products", [])
    products_x_start = width // 3 + 20
    products_x_end = width - 20

    start_y = int(height * 0.14)
    row_height = int(height * 0.09)  # Height per product row

    for i, prod in enumerate(products[:6]):  # Max 6 products
        y = start_y + (i * row_height)

        # Product name (left side of products area) - auto-scale to fit
        name = prod.get("name", "Unknown")
        max_name_width = (products_x_end - products_x_start) // 2 - 20  # Half the products area

        # Start with larger font, scale down if needed
        font_size = 28
        while font_size >= 14:
            test_id = canvas.create_text(0, 0, text=name,
                                        font=("Helvetica", font_size, "bold"),
                                        anchor="w", tags="temp_measure")
            bbox = canvas.bbox(test_id)
            text_width = bbox[2] - bbox[0] if bbox else 0
            canvas.delete(test_id)

            if text_width <= max_name_width:
                break
            font_size -= 2

        canvas.create_text(products_x_start + 10, y, text=name,
                          font=("Helvetica", font_size, "bold"), fill="white",
                          anchor="w", tags="products")

        # Amount (right side)
        gallons = prod.get("gallons", 0)
        jugs = prod.get("jugs", 0)
        jug_size = prod.get("jug_size", "")

        if jugs > 0 and jug_size:
            # Parse jug size to get gallons (e.g., "2.5 gal jug" -> 2.5)
            try:
                jug_gallons = float(jug_size.split()[0])
            except Exception:
                jug_gallons = 2.5  # default
            oz_per_jug = jug_gallons * 128

            # Short jug size for display (e.g., "(2.5g Jug)")
            short_size = f"({jug_gallons:.1f}g Jug)"

            whole_jugs = int(jugs)
            fraction = jugs - whole_jugs

            # Check if close to half (within 0.02)
            if abs(fraction - 0.5) < 0.02:
                if whole_jugs == 0:
                    amount_text = "1/2 jug"
                else:
                    amount_text = f"{whole_jugs} 1/2 jugs"
            elif fraction < 0.02:
                # Close to whole number
                if whole_jugs == 1:
                    amount_text = "1 jug"
                else:
                    amount_text = f"{whole_jugs} jugs"
            else:
                # Has extra oz
                extra_oz = fraction * oz_per_jug
                if whole_jugs == 0:
                    amount_text = f"{extra_oz:.0f} oz"
                    short_size = ""  # No jug size for oz-only
                elif whole_jugs == 1:
                    amount_text = f"1 jug + {extra_oz:.0f} oz"
                else:
                    amount_text = f"{whole_jugs} jugs + {extra_oz:.0f} oz"

            # Draw amount in yellow, jug size in cyan (two separate texts)
            if short_size:
                # Draw jug size first at far right
                jug_text_id = canvas.create_text(products_x_end - 10, y, text=short_size,
                                  font=("Helvetica", 24, "bold"), fill="cyan",
                                  anchor="e", tags="products")
                # Get width of jug size text
                bbox = canvas.bbox(jug_text_id)
                jug_width = bbox[2] - bbox[0] if bbox else 150
                # Draw amount to the left with some padding
                canvas.create_text(products_x_end - 20 - jug_width, y, text=amount_text,
                                  font=("Helvetica", 28, "bold"), fill="yellow",
                                  anchor="e", tags="products")
            else:
                canvas.create_text(products_x_end - 10, y, text=amount_text,
                                  font=("Helvetica", 28, "bold"), fill="yellow",
                                  anchor="e", tags="products")
        else:
            amount_text = f"{gallons:.1f} gal"
            canvas.create_text(products_x_end - 10, y, text=amount_text,
                              font=("Helvetica", 28, "bold"), fill="yellow",
                              anchor="e", tags="products")

        # Draw subtle separator line under each product (except last)
        if i < min(len(products), 6) - 1:
            line_y = y + row_height // 2
            canvas.create_line(products_x_start + 5, line_y,
                              products_x_end - 5, line_y,
                              fill="#333355", width=1, tags="products")

    # Easy Mix indicator at bottom of products
    if batch_mix_data.get("easy_mix", False):
        easy_y = start_y + (min(len(products), 6) * row_height) + 10
        canvas.create_text(width * 2 // 3, easy_y, text="EASY MIX",
                          font=("Helvetica", 22, "bold"), fill="lime", tags="products")

    # Warning to double check jug sizes
    warning_y = start_y + (min(len(products), 6) * row_height) + 40
    canvas.create_text(width * 2 // 3, warning_y, text="Double check jug size!",
                      font=("Helvetica", 16, "italic"), fill="red", tags="products")

def deactivate_batch_mix_layout():
    """Switch back to normal screen layout"""
    global batch_mix_layout_active

    # Clear batch mix elements
    canvas.delete("batchmix")
    canvas.delete("products")
    canvas.delete("totals")

    # Restore normal labels
    canvas.delete("labels")
    canvas.update()
    center_x = canvas.winfo_width() // 2
    height = canvas.winfo_height()

    canvas.create_text(center_x, int(height * 0.08), text="Requested Gallons:",
                      font=("Helvetica", 36, "bold"), fill="white", tags="labels")
    canvas.create_text(center_x, int(height * 0.45), text="Actual Gallons:",
                      font=("Helvetica", 36, "bold"), fill="white", tags="labels")

    # Redraw numbers in normal positions
    redraw_numbers_normal()

    batch_mix_layout_active = False

# Cache for preventing flicker - only redraw when values change
_last_requested_text = None
_last_requested_color = None
_last_actual_text = None
_last_actual_color = None

def redraw_numbers_for_batch_mix():
    """Redraw requested/actual numbers in batch mix positions (left 1/3)"""
    global requested_gallons, last_totalizer_liters

    # Get current color based on state
    color = "green" if colors_are_green else "red"

    # Redraw in left 1/3 position
    canvas.delete("requested")
    canvas.delete("actual")

    canvas.update()
    width = canvas.winfo_width()
    height = canvas.winfo_height()

    left_center_x = width // 6

    # Requested number - smaller font for left panel
    req_y = int(height * 0.22)
    req_font = ("Helvetica", 90, "bold")
    req_text = f"{requested_gallons:.0f}"

    for dx, dy in [(-3,-3), (-3,0), (-3,3), (0,-3), (0,3), (3,-3), (3,0), (3,3)]:
        canvas.create_text(left_center_x+dx, req_y+dy, text=req_text,
                          font=req_font, fill="white", tags="requested")
    canvas.create_text(left_center_x, req_y, text=req_text,
                      font=req_font, fill=color, tags="requested")

    # Actual number - smaller font for left panel
    actual_gallons = last_totalizer_liters * config.LITERS_TO_GALLONS
    act_y = int(height * 0.55)
    act_font = ("Helvetica", 110, "bold")
    act_text = f"{actual_gallons:.1f}"

    for dx, dy in [(-4,-4), (-4,0), (-4,4), (0,-4), (0,4), (4,-4), (4,0), (4,4)]:
        canvas.create_text(left_center_x+dx, act_y+dy, text=act_text,
                          font=act_font, fill="white", tags="actual")
    canvas.create_text(left_center_x, act_y, text=act_text,
                      font=act_font, fill=color, tags="actual")

def redraw_numbers_normal():
    """Redraw requested/actual numbers in normal centered positions"""
    global requested_gallons, last_totalizer_liters

    color = "green" if colors_are_green else "red"
    draw_requested_number(f"{requested_gallons:.0f}", color)
    actual_gallons = last_totalizer_liters * config.LITERS_TO_GALLONS
    draw_actual_number(f"{actual_gallons:.1f}", color)

def add_to_totals(gallons):
    """Add gallons to both daily and season totals"""
    global daily_total, season_total
    daily_total += gallons
    season_total += gallons
    save_totals()

def reset_daily_total():
    """Reset daily total and log the previous day's total"""
    global daily_total, last_reset_date
    import datetime

    # Log yesterday's total if it was non-zero
    if daily_total > 0:
        try:
            with open('/home/pi/daily_totals_log.log', 'a') as f:
                yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
                f.write(f"{yesterday.strftime('%Y-%m-%d')}: {daily_total:.2f} gallons\n")
        except Exception as e:
            print(f"Error logging daily total: {e}")

    # Reset daily total
    daily_total = 0.0
    last_reset_date = datetime.datetime.now().strftime('%Y-%m-%d')
    save_totals()

def reset_season_total():
    """Reset season total (manual reset from menu)"""
    global season_total
    season_total = 0.0
    save_totals()

def update_totals_display():
    """Update the totals display in the menu (if menu is open)"""
    global menu_daily_label, menu_season_label
    if menu_daily_label and menu_season_label:
        menu_daily_label.config(text=f"Daily: {daily_total:.1f} gal")
        menu_season_label.config(text=f"Season: {season_total:.1f} gal")

def record_pending_fill():
    """Record the pending fill to history log and totals when thumbs up is pressed"""
    global pending_fill_gallons, pending_fill_requested, pending_fill_shutoff_type, thumbs_up_label

    # Only record if there's pending fill data
    if pending_fill_gallons > 0:
        fill_log = "/home/pi/fill_history.log"

        # Write to fill history log
        with open(fill_log, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | Requested: {pending_fill_requested:.3f} gal | Actual: {pending_fill_gallons:.3f} gal | Diff: {pending_fill_gallons - pending_fill_requested:+.3f} gal | {pending_fill_shutoff_type}\n")

        # Add to daily and season totals
        add_to_totals(pending_fill_gallons)
        print(f"Fill recorded - Actual: {pending_fill_gallons:.3f} gal")
        print(f"Updated totals - Daily: {daily_total:.2f}, Season: {season_total:.2f}")

        # Clear pending fill data
        pending_fill_gallons = 0.0
        pending_fill_requested = 0.0
        pending_fill_shutoff_type = ""

    else:
        print("No pending fill to record")

def pump_stop_relay(duration=config.PUMP_STOP_DURATION):
    """Activate pump stop relay for specified duration"""
    relay_log = config.RELAY_TEST_LOG

    # Log function entry
    with open(relay_log, 'a') as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - pump_stop_relay() CALLED\n")
        f.write(f"Duration: {duration} seconds\n")
        f.write(f"GPIO_AVAILABLE: {GPIO_AVAILABLE}\n")
        f.write(f"PUMP_STOP_RELAY_PIN: {config.PUMP_STOP_RELAY_PIN}\n")

    if not GPIO_AVAILABLE:
        msg = "GPIO not available, cannot control relay"
        print(msg)
        with open(relay_log, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - ERROR: {msg}\n")
        return

    try:
        # GPIO already initialized during startup - just control the output
        with open(relay_log, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - About to set GPIO {config.PUMP_STOP_RELAY_PIN} HIGH\n")

        GPIO.output(config.PUMP_STOP_RELAY_PIN, GPIO.HIGH)

        msg = f"Alert relay (GPIO {config.PUMP_STOP_RELAY_PIN}) activated for {duration} seconds"
        print(msg)
        with open(relay_log, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - SUCCESS: GPIO set to HIGH\n")
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Sleeping for {duration} seconds...\n")

        time.sleep(duration)

        with open(relay_log, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Sleep complete, about to set GPIO {config.PUMP_STOP_RELAY_PIN} LOW\n")

        GPIO.output(config.PUMP_STOP_RELAY_PIN, GPIO.LOW)

        msg = f"Alert relay (GPIO {config.PUMP_STOP_RELAY_PIN}) deactivated"
        print(msg)
        with open(relay_log, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - SUCCESS: GPIO set to LOW\n")
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
            f.write(f"{'='*60}\n")
    except Exception as e:
        msg = f"Error controlling relay: {e}"
        print(msg)
        with open(relay_log, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - EXCEPTION: {msg}\n")
            import traceback
            f.write(traceback.format_exc())
            f.write(f"{'='*60}\n")


def get_ip_address():
    """Get the current IP address of the system"""
    try:
        result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=2)
        ip = result.stdout.strip().split()[0] if result.stdout.strip() else "No IP"
        return ip
    except Exception:
        return "No IP"

def get_username():
    """Get the current username"""
    try:
        return os.getenv('USER', 'unknown')
    except Exception:
        return 'unknown'

def change_colors_to_green(from_button=False):
    """Change display colors from red to green if within 2 gallons of target, or show thumbs up only if button pressed"""
    global serial_command_received, last_totalizer_liters, requested_gallons, colors_are_green

    button_log = "/home/pi/button_debug.log"
    with open(button_log, 'a') as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [DEBUG] change_colors_to_green() called with from_button={from_button}\n")

    # Calculate current actual gallons
    actual_gallons = last_totalizer_liters * config.LITERS_TO_GALLONS
    with open(button_log, 'a') as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [DEBUG] Actual: {actual_gallons:.1f}, Requested: {requested_gallons:.0f}, Diff: {abs(actual_gallons - requested_gallons):.1f}\n")

    # Check if within 2 gallons of target
    within_threshold = abs(actual_gallons - requested_gallons) <= 2.0

    if within_threshold:
        with open(button_log, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [DEBUG] Within 2 gallon threshold! serial_command_received={serial_command_received}\n")
        # Button press always works, auto-alert only works once
        if from_button or not serial_command_received:
            with open(button_log, 'a') as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [DEBUG] Proceeding with color change to GREEN...\n")
            if not from_button:
                serial_command_received = True

            # Set the flag so update_dashboard keeps colors green
            colors_are_green = True

            # Change the number colors to green by redrawing
            draw_requested_number(f"{requested_gallons:.0f}", "green")
            current_actual = last_totalizer_liters * config.LITERS_TO_GALLONS
            draw_actual_number(f"{current_actual:.1f}", "green")
            # Show big thumbs up on the right side and start animation!
            if thumbs_up_label:
                thumbs_up_label.place(relx=0.85, rely=0.35, anchor="n")
                schedule_flow_reset()
                if thumbs_up_frames:  # Only animate if we have GIF frames
                    animate_thumbs_up()
            source = "button press" if from_button else "auto-alert"
            with open(button_log, 'a') as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Display colors changed to green ({source}, within 2 gallons: {actual_gallons:.1f}/{requested_gallons:.0f})\n")
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [DEBUG] colors_are_green flag set to True\n")
        else:
            with open(button_log, 'a') as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Color change already triggered by auto-alert ({actual_gallons:.1f}/{requested_gallons:.0f})\n")
    else:
        # NOT within threshold
        with open(button_log, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [DEBUG] NOT within 2 gallon threshold ({actual_gallons:.1f}/{requested_gallons:.0f})\n")

        # If button was pressed, still show thumbs up but keep screen RED
        if from_button:
            with open(button_log, 'a') as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [DEBUG] Button pressed but not within threshold - showing thumbs up, keeping RED\n")
            # Show thumbs up but DO NOT change colors to green
            if thumbs_up_label:
                thumbs_up_label.place(relx=0.85, rely=0.35, anchor="n")
                schedule_flow_reset()
                if thumbs_up_frames:  # Only animate if we have GIF frames
                    animate_thumbs_up()
            with open(button_log, 'a') as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Thumbs up shown but screen stays RED (not within 2 gallons: {actual_gallons:.1f}/{requested_gallons:.0f})\n")

def green_button_monitor():
    """Monitor GPIO pin for green button press (active low with pull-up)"""
    button_log = "/home/pi/button_debug.log"

    if not GPIO_AVAILABLE:
        print("GPIO not available, green button monitoring disabled")
        return

    try:
        # Initialize GPIO if not already done
        try:
            GPIO.setmode(GPIO.BCM)
        except Exception:
            pass  # Already initialized

        # Set up green button pin as input with pull-up resistor
        GPIO.setup(config.GREEN_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        msg = f"Green button monitor started on GPIO {config.GREEN_BUTTON_PIN}"
        print(msg)
        with open(button_log, 'a') as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

        last_button_state = GPIO.HIGH

        while True:
            # Read current button state
            current_state = GPIO.input(config.GREEN_BUTTON_PIN)

            # Detect button press (transition from HIGH to LOW)
            if last_button_state == GPIO.HIGH and current_state == GPIO.LOW:
                with open(button_log, 'a') as f:
                    f.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S')} *** GREEN BUTTON PRESSED! ***\n")
                print("Green button pressed!")
                # If in reminders mode, dismiss reminders
                if reminders_mode:
                    root.after(0, dismiss_reminders)
                # If in full test mode, mark button test as passed
                elif full_test_mode:
                    if full_test_window and hasattr(full_test_window, 'mark_tested'):
                        root.after(0, lambda: full_test_window.mark_tested('button'))
                        print("Full test: Button test marked as passed")
                else:
                    # Call color change function in main thread with from_button=True
                    root.after(0, lambda: change_colors_to_green(from_button=True))
                    # Record the fill if there's pending data
                    root.after(0, record_pending_fill)
                # Debounce delay
                time.sleep(0.3)

            last_button_state = current_state
            time.sleep(0.05)  # Check every 50ms

    except Exception as e:
        print(f"Green button monitor error: {e}")

def show_log_viewer():
    """Display log viewer window with button controls"""
    global log_viewer_mode, log_viewer_window, log_viewer_text

    log_viewer_mode = True
    log_viewer_window = tk.Toplevel()
    log_viewer_window.title("System Logs")
    log_viewer_window.attributes('-fullscreen', True)
    log_viewer_window.configure(bg='black')

    # Title
    title = tk.Label(log_viewer_window, text="SYSTEM LOGS", font=("Helvetica", 32, "bold"),
                     fg="cyan", bg="black")
    title.pack(pady=10)

    # Controls instruction
    controls = tk.Label(log_viewer_window, text="-1=SCROLL UP  +1=SCROLL DOWN  OV=EXIT",
                       font=("Helvetica", 22, "bold"), fg="#ffff00", bg="#0a0a0a")
    controls.pack(pady=2)

    # Create scrolled text widget for logs - HUGE FONT for 7" display
    log_frame = tk.Frame(log_viewer_window, bg='black')
    log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    scrollbar = tk.Scrollbar(log_frame, width=30)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # EXTRA LARGE FONT - 24pt bold
    log_viewer_text = tk.Text(log_frame, font=("Courier", 24, "bold"), bg="black", fg="lime",
                             yscrollcommand=scrollbar.set, wrap=tk.WORD)
    log_viewer_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.config(command=log_viewer_text.yview)

    # Load logs - get last 200 lines for scrolling through history
    try:
        import subprocess

        # Get last 200 lines from system log
        log_viewer_text.insert(tk.END, "=== SYSTEM LOG (last 200 lines) ===\n\n")
        result = subprocess.run(['tail', '-200', '/home/pi/iol_dashboard.log'],
                              capture_output=True, text=True, timeout=2)
        log_viewer_text.insert(tk.END, result.stdout)

        # Get last 200 lines from serial log
        log_viewer_text.insert(tk.END, '\n\n=== SERIAL LOG (last 200 lines) ===\n\n')
        result = subprocess.run(['tail', '-200', '/home/pi/serial_debug.log'],
                              capture_output=True, text=True, timeout=2)
        log_viewer_text.insert(tk.END, result.stdout)

        # Get button debug log if it exists
        log_viewer_text.insert(tk.END, '\n\n=== BUTTON DEBUG LOG (last 100 lines) ===\n\n')
        result = subprocess.run(['tail', '-100', '/home/pi/button_debug.log'],
                              capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            log_viewer_text.insert(tk.END, result.stdout)
        else:
            log_viewer_text.insert(tk.END, "(No button log found)\n")

        # Scroll to bottom initially
        log_viewer_text.see(tk.END)
    except Exception as e:
        log_viewer_text.insert(tk.END, f"ERROR loading logs:\n{e}")

    # Make read-only but keep enabled for scrolling
    log_viewer_text.config(state=tk.NORMAL)

def log_viewer_scroll_down():
    """Scroll log viewer down"""
    global log_viewer_text
    if log_viewer_text:
        log_viewer_text.yview_scroll(5, "units")  # Scroll down 5 lines (faster scrolling)

def log_viewer_scroll_up():
    """Scroll log viewer up"""
    global log_viewer_text
    if log_viewer_text:
        log_viewer_text.yview_scroll(-5, "units")  # Scroll up 5 lines (faster scrolling)

def close_log_viewer():
    """Close log viewer and return to menu"""
    global log_viewer_mode, log_viewer_window, log_viewer_text
    log_viewer_mode = False
    if log_viewer_window:
        log_viewer_window.destroy()
        log_viewer_window = None
        log_viewer_text = None

def show_fill_history():
    """Display fill history viewer window"""
    global fill_history_mode, fill_history_window, fill_history_text

    fill_history_mode = True
    fill_history_window = tk.Toplevel()
    fill_history_window.title("Fill History")
    fill_history_window.attributes('-fullscreen', True)
    fill_history_window.configure(bg='black')

    # Title
    title = tk.Label(fill_history_window, text="FILL HISTORY", font=("Helvetica", 32, "bold"),
                     fg="cyan", bg="black")
    title.pack(pady=10)

    # Controls instruction
    controls = tk.Label(fill_history_window, text="-1=SCROLL UP  +1=SCROLL DOWN  OV=EXIT",
                       font=("Helvetica", 22, "bold"), fg="#ffff00", bg="#0a0a0a")
    controls.pack(pady=2)

    # Create scrolled text widget for fill history
    history_frame = tk.Frame(fill_history_window, bg='black')
    history_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    scrollbar = tk.Scrollbar(history_frame, width=30)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # EXTRA LARGE FONT - 28pt bold for better readability
    fill_history_text = tk.Text(history_frame, font=("Courier", 28, "bold"), bg="black", fg="lime",
                             yscrollcommand=scrollbar.set, wrap=tk.WORD)
    fill_history_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.config(command=fill_history_text.yview)

    # Load fill history
    try:
        import subprocess

        # Get last 100 fill history entries
        fill_history_text.insert(tk.END, "=== FILL HISTORY (last 100 fills) ===\n\n")
        result = subprocess.run(['tail', '-100', '/home/pi/fill_history.log'],
                              capture_output=True, text=True, timeout=2)
        if result.returncode == 0 and result.stdout:
            fill_history_text.insert(tk.END, result.stdout)
        else:
            fill_history_text.insert(tk.END, "(No fill history found)\n\n")
            fill_history_text.insert(tk.END, "Fill history will appear here after completing fills.\n")

        # Scroll to bottom initially
        fill_history_text.see(tk.END)
    except Exception as e:
        fill_history_text.insert(tk.END, f"ERROR loading fill history:\n{e}")

    # Make read-only but keep enabled for scrolling
    fill_history_text.config(state=tk.NORMAL)

def fill_history_scroll_down():
    """Scroll fill history down"""
    global fill_history_text
    if fill_history_text:
        fill_history_text.yview_scroll(5, "units")  # Scroll down 5 lines

def fill_history_scroll_up():
    """Scroll fill history up"""
    global fill_history_text
    if fill_history_text:
        fill_history_text.yview_scroll(-5, "units")  # Scroll up 5 lines

def close_fill_history():
    """Close fill history and return to menu"""
    global fill_history_mode, fill_history_window, fill_history_text
    fill_history_mode = False
    if fill_history_window:
        fill_history_window.destroy()
        fill_history_window = None
        fill_history_text = None

def run_self_test():
    """Run system self-test"""
    global self_test_mode, self_test_window

    self_test_mode = True
    self_test_window = tk.Toplevel()
    self_test_window.title("System Self-Test")
    self_test_window.attributes('-fullscreen', True)
    self_test_window.configure(bg='black')

    # Title
    title = tk.Label(self_test_window, text="SYSTEM SELF-TEST", font=("Helvetica", 40, "bold"),
                     fg="cyan", bg="black")
    title.pack(pady=20)

    # Controls instruction
    controls = tk.Label(self_test_window, text="OV=EXIT TO MENU",
                       font=("Helvetica", 22, "bold"), fg="#ffff00", bg="#0a0a0a")
    controls.pack(pady=2)

    # Results frame
    results_frame = tk.Frame(self_test_window, bg='black')
    results_frame.pack(fill=tk.BOTH, expand=True, padx=40, pady=20)

    results_text = tk.Text(results_frame, font=("Courier", 24), bg="black", fg="white",
                           height=15, width=50)
    results_text.pack()

    def run_tests():
        results_text.insert(tk.END, "Starting self-test...\n\n")
        results_text.update()

        # Test 1: GPIO
        results_text.insert(tk.END, "1. GPIO Test: ")
        results_text.update()
        if GPIO_AVAILABLE:
            try:
                # Quick relay pulse test (0.5 seconds)
                GPIO.output(config.PUMP_STOP_RELAY_PIN, GPIO.HIGH)
                time.sleep(0.5)
                GPIO.output(config.PUMP_STOP_RELAY_PIN, GPIO.LOW)
                results_text.insert(tk.END, "PASS (Relay pulsed)\n", "pass")
            except Exception as e:
                results_text.insert(tk.END, f"FAIL ({e})\n", "fail")
        else:
            results_text.insert(tk.END, "SKIP (Not available)\n", "skip")
        results_text.update()

        # Test 2: Serial Port
        results_text.insert(tk.END, "2. Serial Port Test: ")
        results_text.update()
        try:
            import serial
            ser = serial.Serial(config.SERIAL_PORT, config.SERIAL_BAUD, timeout=1)
            ser.close()
            results_text.insert(tk.END, "PASS\n", "pass")
        except Exception as e:
            results_text.insert(tk.END, f"FAIL ({e})\n", "fail")
        results_text.update()

        # Test 3: IOL-HAT
        results_text.insert(tk.END, "3. IOL-HAT Communication: ")
        results_text.update()
        try:
            raw_data = iolhat.pd(config.IOL_PORT, 0, config.DATA_LENGTH, None)
            if len(raw_data) >= 15 and raw_data != b'\x00' * len(raw_data):
                results_text.insert(tk.END, "PASS (Data received)\n", "pass")
            else:
                results_text.insert(tk.END, "FAIL (No response or all zeros)\n", "fail")
        except Exception as e:
            results_text.insert(tk.END, f"FAIL ({e})\n", "fail")
        results_text.update()

        # Test 4: Flow Meter
        results_text.insert(tk.END, "4. Flow Meter Reading: ")
        results_text.update()
        try:
            gallons = read_flow_meter()
            if not connection_error:
                results_text.insert(tk.END, f"PASS (Reading: {gallons:.2f} gal)\n", "pass")
            else:
                results_text.insert(tk.END, f"FAIL ({error_message})\n", "fail")
        except Exception as e:
            results_text.insert(tk.END, f"FAIL ({e})\n", "fail")
        results_text.update()

        # Test 5: Display
        results_text.insert(tk.END, "5. Display System: PASS (You can see this)\n", "pass")

        results_text.insert(tk.END, "\n=== Test Complete ===\n")

        # Color tags
        results_text.tag_config("pass", foreground="red")
        results_text.tag_config("fail", foreground="red")
        results_text.tag_config("skip", foreground="yellow")

    # Run tests in thread to not block GUI
    test_thread = threading.Thread(target=run_tests, daemon=True)
    test_thread.start()

def close_self_test():
    """Close self-test and return to menu"""
    global self_test_mode, self_test_window
    self_test_mode = False
    if self_test_window:
        self_test_window.destroy()
        self_test_window = None

def run_full_test():
    """Run comprehensive interactive full system test"""
    global full_test_mode, full_test_window

    full_test_mode = True
    full_test_window = tk.Toplevel()
    full_test_window.title("Full System Test")
    full_test_window.attributes('-fullscreen', True)
    full_test_window.configure(bg='black')

    # Title
    title = tk.Label(full_test_window, text="FULL SYSTEM TEST", font=("Helvetica", 36, "bold"),
                     fg="cyan", bg="black")
    title.pack(pady=15)

    # Controls instruction
    controls = tk.Label(full_test_window, text="Test each item - OV=TEST OV / EXIT MENU (press twice to exit)",
                       font=("Helvetica", 18, "bold"), fg="#ffff00", bg="#0a0a0a")
    controls.pack(pady=2)

    # Test status dict
    test_status = {
        'minus_1': False,
        'plus_1': False,
        'minus_10': False,
        'plus_10': False,
        'OV': False,
        'PS': False,
        'button': False,
        'gpio': False,
        'relay': False,
        'serial': False,
        'iolhat': False,
        'flow_meter': False,
        'display': False
    }

    # Flow meter error message
    flow_meter_error = [None]  # Use list to allow modification in nested function

    # Results frame
    results_frame = tk.Frame(full_test_window, bg='black')
    results_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)

    results_text = tk.Text(results_frame, font=("Courier", 20), bg="black", fg="white",
                           height=18, width=65)
    results_text.pack()

    # Tag configs for colors
    results_text.tag_config("pass", foreground="green")
    results_text.tag_config("pending", foreground="yellow")
    results_text.tag_config("fail", foreground="red")
    results_text.tag_config("header", foreground="cyan")

    def update_display():
        results_text.delete('1.0', tk.END)
        results_text.insert(tk.END, "SERIAL COMMANDS:\n", "header")
        results_text.insert(tk.END, f"  Send -1:     {'✓ PASS' if test_status['minus_1'] else '○ PENDING'}\n",
                           "pass" if test_status['minus_1'] else "pending")
        results_text.insert(tk.END, f"  Send +1:     {'✓ PASS' if test_status['plus_1'] else '○ PENDING'}\n",
                           "pass" if test_status['plus_1'] else "pending")
        results_text.insert(tk.END, f"  Send -10:    {'✓ PASS' if test_status['minus_10'] else '○ PENDING'}\n",
                           "pass" if test_status['minus_10'] else "pending")
        results_text.insert(tk.END, f"  Send +10:    {'✓ PASS' if test_status['plus_10'] else '○ PENDING'}\n",
                           "pass" if test_status['plus_10'] else "pending")
        results_text.insert(tk.END, f"  Send OV:     {'✓ PASS' if test_status['OV'] else '○ PENDING'}\n",
                           "pass" if test_status['OV'] else "pending")
        results_text.insert(tk.END, f"  Send PS:     {'✓ PASS' if test_status['PS'] else '○ PENDING'}\n\n",
                           "pass" if test_status['PS'] else "pending")

        results_text.insert(tk.END, "HARDWARE:\n", "header")
        results_text.insert(tk.END, f"  GPIO:        {'✓ PASS' if test_status['gpio'] else '○ TESTING...'}\n",
                           "pass" if test_status['gpio'] else "pending")
        results_text.insert(tk.END, f"  Relay:       {'✓ PASS' if test_status['relay'] else '○ TESTING...'}\n",
                           "pass" if test_status['relay'] else "pending")
        results_text.insert(tk.END, f"  Serial Port: {'✓ PASS' if test_status['serial'] else '○ TESTING...'}\n",
                           "pass" if test_status['serial'] else "pending")
        results_text.insert(tk.END, f"  IOL-HAT:     {'✓ PASS' if test_status['iolhat'] else '○ TESTING...'}\n",
                           "pass" if test_status['iolhat'] else "pending")

        # Flow meter with error display
        if flow_meter_error[0]:
            results_text.insert(tk.END, f"  Flow Meter:  ✗ ERROR\n", "fail")
            results_text.insert(tk.END, f"\n{flow_meter_error[0]}\n", "fail")
        else:
            results_text.insert(tk.END, f"  Flow Meter:  {'✓ PASS' if test_status['flow_meter'] else '○ TESTING...'}\n",
                               "pass" if test_status['flow_meter'] else "pending")

        results_text.insert(tk.END, f"  Button:      {'✓ PASS' if test_status['button'] else '○ PENDING (Press thumbs up)'}\n",
                           "pass" if test_status['button'] else "pending")
        results_text.insert(tk.END, f"  Display:     {'✓ PASS' if test_status['display'] else '○ PASS (You can see this)'}\n",
                           "pass")

    def mark_tested(test_name):
        test_status[test_name] = True
        update_display()

    # Store functions in window for serial listener access
    full_test_window.mark_tested = mark_tested
    full_test_window.test_status = test_status

    # Run hardware tests automatically
    def run_hardware_tests():
        # Test 1: GPIO
        if GPIO_AVAILABLE:
            try:
                # Quick test - just check if GPIO is initialized
                root.after(0, lambda: mark_tested('gpio'))
            except Exception:
                pass

        time.sleep(0.2)

        # Test 2: Relay (with visible pulse)
        if GPIO_AVAILABLE:
            try:
                GPIO.output(config.PUMP_STOP_RELAY_PIN, GPIO.HIGH)
                time.sleep(0.3)
                GPIO.output(config.PUMP_STOP_RELAY_PIN, GPIO.LOW)
                root.after(0, lambda: mark_tested('relay'))
            except Exception:
                pass

        time.sleep(0.2)

        # Test 3: Serial Port
        try:
            import serial
            ser = serial.Serial(config.SERIAL_PORT, config.SERIAL_BAUD, timeout=1)
            ser.close()
            root.after(0, lambda: mark_tested('serial'))
        except Exception:
            pass

        time.sleep(0.2)

        # Test 4: IOL-HAT Communication (with 5 second timeout)
        iolhat_error = [None]
        iolhat_success = [False]

        def test_iolhat():
            try:
                raw_data = iolhat.pd(config.IOL_PORT, 0, config.DATA_LENGTH, None)
                if len(raw_data) >= 15 and raw_data != b'\x00' * len(raw_data):
                    iolhat_success[0] = True
                    root.after(0, lambda: mark_tested('iolhat'))
                # If all zeros, it means IOL-HAT works but device isn't responding
                elif len(raw_data) >= 15:
                    iolhat_success[0] = True
                    root.after(0, lambda: mark_tested('iolhat'))
            except Exception as e:
                iolhat_error[0] = str(e)

        # Run IOL-HAT test with timeout
        iolhat_thread = threading.Thread(target=test_iolhat, daemon=True)
        iolhat_thread.start()
        iolhat_thread.join(timeout=5.0)  # 5 second timeout

        # Check if test completed or timed out
        if not iolhat_success[0]:
            # Test failed or timed out
            pass  # IOL-HAT test remains in TESTING state (will show as yellow/pending)

        time.sleep(0.2)

        # Test 5: Flow Meter
        try:
            raw_data = iolhat.pd(config.IOL_PORT, 0, config.DATA_LENGTH, None)
            if raw_data and len(raw_data) == config.DATA_LENGTH and raw_data != b'\x00' * len(raw_data):
                root.after(0, lambda: mark_tested('flow_meter'))
            else:
                # Flow meter returned all zeros or wrong length
                error_msg = "ERROR: Ensure flow meter is connected.\nCheck if flow meter displays green check mark in corner."
                flow_meter_error[0] = error_msg
                root.after(0, update_display)
        except Exception as e:
            error_msg = f"ERROR: Ensure flow meter is connected.\nCheck if flow meter displays green check mark in corner.\n({str(e)})"
            flow_meter_error[0] = error_msg
            root.after(0, update_display)

        # Test 6: Display (automatically pass - if you can see this)
        root.after(0, lambda: mark_tested('display'))

    update_display()

    # Start hardware tests in background
    hardware_thread = threading.Thread(target=run_hardware_tests, daemon=True)
    hardware_thread.start()

def close_full_test():
    """Close full-test and return to menu"""
    global full_test_mode, full_test_window
    full_test_mode = False
    if full_test_window:
        full_test_window.destroy()
        full_test_window = None

def check_wifi_status():
    """Check if WiFi is connected and return status string"""
    try:
        import subprocess
        # Check if wlan0 is up and has an IP
        result = subprocess.run(['ip', 'addr', 'show', 'wlan0'],
                              capture_output=True, text=True, timeout=1)

        if 'state UP' in result.stdout and 'inet ' in result.stdout:
            # Get signal strength if available
            try:
                signal_result = subprocess.run(['cat', '/proc/net/wireless'],
                                             capture_output=True, text=True, timeout=1)
                if 'wlan0' in signal_result.stdout:
                    return "WiFi: CONNECTED"
            except Exception:
                pass
            return "WiFi: CONNECTED"
        else:
            return "WiFi: DISCONNECTED"
    except Exception as e:
        return "WiFi: UNKNOWN"

def run_system_update():
    """Run system update"""
    global update_mode, update_window

    update_mode = True
    update_window = tk.Toplevel()
    update_window.title("System Update")
    update_window.attributes('-fullscreen', True)
    update_window.configure(bg='black')

    # Title
    title = tk.Label(update_window, text="SYSTEM UPDATE", font=("Helvetica", 36, "bold"),
                     fg="cyan", bg="black")
    title.pack(pady=20)

    # Controls instruction
    controls = tk.Label(update_window, text="OV=EXIT TO MENU",
                       font=("Helvetica", 22, "bold"), fg="#ffff00", bg="#0a0a0a")
    controls.pack(pady=2)

    # Status text
    status_frame = tk.Frame(update_window, bg='black')
    status_frame.pack(fill=tk.BOTH, expand=True, padx=40, pady=20)

    status_text = tk.Text(status_frame, font=("Courier", 32, "bold"), bg="black", fg="lime",
                         wrap=tk.WORD)
    status_text.pack(fill=tk.BOTH, expand=True)

    def run_update():
        import subprocess
        status_text.insert(tk.END, "Starting BBB software update from GitHub...\n\n")
        status_text.update()

        try:
            # Show current version
            status_text.insert(tk.END, f"Current Version: {VERSION}\n\n")
            status_text.update()

            # Step 1: Navigate to git repo and pull latest
            status_text.insert(tk.END, "=== Step 1: Checking for updates ===\n")
            status_text.update()

            result = subprocess.run(['git', '-C', '/home/pi/iol-dashboard', 'fetch', 'origin'],
                                  capture_output=True, text=True, timeout=60)
            if result.stderr:
                status_text.insert(tk.END, result.stderr + "\n")
            status_text.update()

            if result.returncode != 0:
                status_text.insert(tk.END, "ERROR: Git fetch failed!\n")
                status_text.insert(tk.END, "Press OV to return to menu\n")
                return

            # Get the version we're updating to
            result = subprocess.run(['git', '-C', '/home/pi/iol-dashboard', 'log', 'origin/master', '-1', '--format=%s'],
                                  capture_output=True, text=True, timeout=10)
            new_version_msg = result.stdout.strip()
            status_text.insert(tk.END, f"\nNew Version Available:\n{new_version_msg}\n\n")
            status_text.update()

            # Step 2: Reset to latest origin/master
            status_text.insert(tk.END, "=== Step 2: Installing update ===\n")
            status_text.update()

            result = subprocess.run(['git', '-C', '/home/pi/iol-dashboard', 'reset', '--hard', 'origin/master'],
                                  capture_output=True, text=True, timeout=30)
            status_text.insert(tk.END, "Updated repository\n")
            status_text.update()

            if result.returncode != 0:
                status_text.insert(tk.END, "ERROR: Git reset failed!\n")
                status_text.insert(tk.END, "Press OV to return to menu\n")
                return

            # Step 3: Copy updated files to active location
            status_text.insert(tk.END, "\n=== Step 3: Installing files ===\n")
            status_text.update()

            result = subprocess.run(['cp', '/home/pi/iol-dashboard/dashboard.py', '/home/pi/dashboard.py'],
                                  capture_output=True, text=True, timeout=10)
            status_text.insert(tk.END, "Copied dashboard.py\n")

            result = subprocess.run(['cp', '/home/pi/iol-dashboard/config.py', '/home/pi/config.py'],
                                  capture_output=True, text=True, timeout=10)
            status_text.insert(tk.END, "Copied config.py\n")

            result = subprocess.run(['cp', '-r', '/home/pi/iol-dashboard/RPi', '/home/pi/RPi'],
                                  capture_output=True, text=True, timeout=10)
            status_text.insert(tk.END, "Copied RPi module\n\n")
            status_text.update()

            status_text.insert(tk.END, "=== UPDATE COMPLETE ===\n\n")
            status_text.insert(tk.END, "Restarting dashboard to apply changes...\n\n")
            status_text.update()

            # Wait 2 seconds so user can see the message
            time.sleep(2)

            # Restart the dashboard by executing the new version
            import os
            os.execv('/usr/bin/python3', ['/usr/bin/python3', '/home/pi/dashboard.py'])

        except subprocess.TimeoutExpired:
            status_text.insert(tk.END, "\n\nERROR: Command timed out\n")
            status_text.insert(tk.END, "Press OV to return to menu\n")
        except Exception as e:
            status_text.insert(tk.END, f"\n\nERROR: {e}\n")
            status_text.insert(tk.END, "Press OV to return to menu\n")

        status_text.see(tk.END)

    # Run update in thread
    update_thread = threading.Thread(target=run_update, daemon=True)
    update_thread.start()

def close_update():
    """Close update window and return to menu"""
    global update_mode, update_window
    update_mode = False
    if update_window:
        update_window.destroy()
        update_window = None

def confirm_reset_season():
    """Confirm and reset season total with OV/PS commands"""
    # Create confirmation window
    confirm_window = tk.Toplevel()
    confirm_window.title("Reset Season Confirmation")
    confirm_window.attributes('-fullscreen', True)
    confirm_window.configure(bg='black')

    # Confirmation message
    message = tk.Label(confirm_window, text="RESET SEASON TOTAL?",
                      font=("Helvetica", 48, "bold"), fg="orange", bg="black")
    message.pack(expand=True, pady=50)

    # Show current season total
    current_total = tk.Label(confirm_window, text=f"Current Season Total: {season_total:.2f} gal",
                            font=("Helvetica", 32, "bold"), fg="lime", bg="black")
    current_total.pack(pady=20)

    # Instructions
    instructions = tk.Label(confirm_window, text="Press OV to CONFIRM or PS to CANCEL",
                          font=("Helvetica", 22, "bold"), fg="cyan", bg="black")
    instructions.pack(pady=30)

    # Countdown timer
    countdown_label = tk.Label(confirm_window, text="Auto-cancel in 10 seconds",
                             font=("Helvetica", 22), fg="white", bg="black")
    countdown_label.pack(pady=20)

    countdown = [10]  # Use list to allow modification in nested function

    def cancel_reset():
        global reset_season_confirm_window, reset_season_confirm_handler, reset_season_cancel_handler
        confirm_window.destroy()
        reset_season_confirm_window = None
        reset_season_confirm_handler = None
        reset_season_cancel_handler = None

    def confirm_reset():
        global reset_season_confirm_window, reset_season_confirm_handler, reset_season_cancel_handler
        print("Resetting season total...")
        reset_season_total()
        confirm_window.destroy()
        reset_season_confirm_window = None
        reset_season_confirm_handler = None
        reset_season_cancel_handler = None
        # Update menu display
        if menu_window:
            update_totals_display()

    def update_countdown():
        countdown[0] -= 1
        if countdown[0] <= 0:
            cancel_reset()
        else:
            countdown_label.config(text=f"Auto-cancel in {countdown[0]} seconds")
            confirm_window.after(1000, update_countdown)

    # Bind keyboard shortcuts
    confirm_window.bind('<Escape>', lambda e: cancel_reset())

    # Store the confirmation handlers globally so serial listener can access them
    global reset_season_confirm_window, reset_season_confirm_handler, reset_season_cancel_handler
    reset_season_confirm_window = confirm_window
    reset_season_confirm_handler = confirm_reset
    reset_season_cancel_handler = cancel_reset

    # Start countdown
    confirm_window.after(1000, update_countdown)

def shutdown_system():
    """Shutdown the system"""
    import subprocess
    # Show confirmation for 2 seconds then shutdown
    confirm_window = tk.Toplevel()
    confirm_window.title("Shutdown")
    confirm_window.attributes('-fullscreen', True)
    confirm_window.configure(bg='black')

    msg = tk.Label(confirm_window, text="SHUTTING DOWN...",
                  font=("Helvetica", 48, "bold"), fg="red", bg="black")
    msg.pack(expand=True)

    def do_shutdown():
        time.sleep(2)
        # Use systemctl poweroff with password
        subprocess.run(['bash', '-c', 'echo raspi | sudo -S systemctl poweroff'])

    shutdown_thread = threading.Thread(target=do_shutdown, daemon=True)
    shutdown_thread.start()

def reboot_system():
    """Reboot the system"""
    import subprocess
    # Show confirmation for 2 seconds then reboot
    confirm_window = tk.Toplevel()
    confirm_window.title("Reboot")
    confirm_window.attributes('-fullscreen', True)
    confirm_window.configure(bg='black')

    msg = tk.Label(confirm_window, text="REBOOTING...",
                  font=("Helvetica", 48, "bold"), fg="orange", bg="black")
    msg.pack(expand=True)

    def do_reboot():
        time.sleep(2)
        # Use systemctl reboot with password
        subprocess.run(['bash', '-c', 'echo raspi | sudo -S systemctl reboot'])

    reboot_thread = threading.Thread(target=do_reboot, daemon=True)
    reboot_thread.start()

def update_menu_highlight():
    """Update visual highlighting of selected menu item"""
    global menu_buttons, menu_arrows, menu_selected_index, menu_position_label

    if not menu_buttons or not menu_arrows:
        return

    # Update position indicator with current selection
    menu_items_names = ["VIEW LOGS", "FILL HISTORY", "FULL TEST", "RESET SEASON", "SELF TEST",
                       "SYSTEM UPDATE", "SHUTDOWN", "REBOOT", "EXIT TO DESKTOP", "EXIT MENU"]
    if menu_position_label:
        menu_position_label.config(text=f"Option {menu_selected_index + 1} of 10: {menu_items_names[menu_selected_index]}")

    # Default colors for each button
    colors = [
        ("blue", "white"),       # Logs
        ("purple", "white"),     # Fill History
        ("cyan", "white"),       # Full Test
        ("orange", "white"),     # Reset Season
        ("green", "white"),      # Self Test
        ("purple", "white"),     # Update
        ("red", "white"),        # Shutdown
        ("orange", "white"),     # Reboot
        ("red4", "white"),       # Exit to Desktop
        ("gray30", "white")      # Exit Menu
    ]

    for i, (btn, arrow) in enumerate(zip(menu_buttons, menu_arrows)):
        if i == menu_selected_index:
            # SELECTED - Make it very obvious with red border (keep same font size to prevent layout shift)
            btn.config(bg="white", fg="black",
                      font=("Helvetica", 22, "bold"),
                      relief=tk.RAISED, borderwidth=6,
                      highlightbackground="red", highlightthickness=4, highlightcolor="red",
                      width=18, height=1)
            arrow.config(text=">>> SELECTED >>>", fg="yellow",
                        font=("Helvetica", 20, "bold"))
        else:
            # Unselected - Dim it
            btn.config(bg="gray20", fg="gray50",
                      font=("Helvetica", 22),
                      relief=tk.FLAT, borderwidth=2,
                      width=18, height=1)
            arrow.config(text="", fg="black")

def menu_navigate_up():
    """Move selection up in menu"""
    global menu_selected_index
    old_index = menu_selected_index
    menu_selected_index = (menu_selected_index - 1) % 10  # Wrap around - 10 menu items
    with open('/home/pi/menu_debug.log', 'a') as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - menu_navigate_up: {old_index} -> {menu_selected_index}\n")
    update_menu_highlight()

def menu_navigate_down():
    """Move selection down in menu"""
    global menu_selected_index
    old_index = menu_selected_index
    menu_selected_index = (menu_selected_index + 1) % 10  # Wrap around - 10 menu items
    with open('/home/pi/menu_debug.log', 'a') as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - menu_navigate_down: {old_index} -> {menu_selected_index}\n")
    update_menu_highlight()

def menu_select():
    """Activate the currently selected menu item"""
    global menu_selected_index

    # Debug logging to track selection
    menu_items = ["VIEW LOGS", "FILL HISTORY", "FULL TEST", "RESET SEASON", "SELF TEST",
                  "SYSTEM UPDATE", "SHUTDOWN", "REBOOT", "EXIT TO DESKTOP", "EXIT MENU"]
    with open('/home/pi/menu_debug.log', 'a') as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - menu_select() called with index={menu_selected_index}, item={menu_items[menu_selected_index] if menu_selected_index < len(menu_items) else 'UNKNOWN'}\n")

    if menu_selected_index == 0:
        show_log_viewer()
    elif menu_selected_index == 1:
        show_fill_history()
    elif menu_selected_index == 2:
        run_full_test()
    elif menu_selected_index == 3:
        confirm_reset_season()
    elif menu_selected_index == 4:
        run_self_test()
    elif menu_selected_index == 5:
        run_system_update()
    elif menu_selected_index == 6:
        shutdown_system()
    elif menu_selected_index == 7:
        reboot_system()
    elif menu_selected_index == 8:
        exit_to_desktop()
    elif menu_selected_index == 9:
        close_menu()

def exit_to_desktop():
    """Exit the dashboard and return to desktop with confirmation"""
    # Create confirmation window
    confirm_window = tk.Toplevel()
    confirm_window.title("Exit Confirmation")
    confirm_window.attributes('-fullscreen', True)
    confirm_window.configure(bg='black')
    
    # Confirmation message
    message = tk.Label(confirm_window, text="EXIT TO DESKTOP?", 
                       font=("Helvetica", 48, "bold"), fg="red", bg="black")
    message.pack(expand=True, pady=50)
    
    # Instructions
    instructions = tk.Label(confirm_window, text="Press OV to CONFIRM or PS to CANCEL",
                           font=("Helvetica", 22, "bold"), fg="cyan", bg="black")
    instructions.pack(pady=30)
    
    # Countdown timer
    countdown_label = tk.Label(confirm_window, text="Auto-cancel in 10 seconds",
                              font=("Helvetica", 22), fg="white", bg="black")
    countdown_label.pack(pady=20)
    
    countdown = [10]  # Use list to allow modification in nested function
    
    def cancel_exit():
        global exit_confirm_window, exit_confirm_handler, exit_cancel_handler
        confirm_window.destroy()
        exit_confirm_window = None
        exit_confirm_handler = None
        exit_cancel_handler = None
        # Menu window is already open behind this dialog - just let it reappear
    
    def confirm_exit():
        print("Exiting dashboard to desktop...")
        confirm_window.destroy()
        root.destroy()
        sys.exit(0)
    
    def update_countdown():
        countdown[0] -= 1
        if countdown[0] <= 0:
            cancel_exit()
        else:
            countdown_label.config(text=f"Auto-cancel in {countdown[0]} seconds")
            confirm_window.after(1000, update_countdown)
    
    # Bind keyboard shortcuts for confirmation
    confirm_window.bind('<Escape>', lambda e: cancel_exit())
    
    # Store the confirmation handlers globally so serial listener can access them
    global exit_confirm_window, exit_confirm_handler, exit_cancel_handler
    exit_confirm_window = confirm_window
    exit_confirm_handler = confirm_exit
    exit_cancel_handler = cancel_exit
    
    # Start countdown
    confirm_window.after(1000, update_countdown)

def close_menu():
    """Close the menu and return to main dashboard"""
    global menu_mode, menu_window, menu_buttons, menu_arrows, menu_selected_index
    global exit_confirm_window, exit_confirm_handler, exit_cancel_handler
    menu_mode = False
    menu_selected_index = 0
    menu_buttons = []
    menu_arrows = []
    # Reset exit confirmation globals
    exit_confirm_window = None
    exit_confirm_handler = None
    exit_cancel_handler = None
    if menu_window:
        menu_window.destroy()
        menu_window = None

def dismiss_reminders():
    """Dismiss the daily reminders screen"""
    global reminders_mode, reminders_window, last_reminder_date
    reminders_mode = False
    # Mark today as shown
    last_reminder_date = time.strftime('%Y-%m-%d')
    if reminders_window:
        reminders_window.destroy()
        reminders_window = None
    print(f"Daily reminders dismissed for {last_reminder_date}")

def show_daily_reminders():
    """Display daily reminders checklist with time-based greeting"""
    global reminders_mode, reminders_window

    reminders_mode = True
    reminders_window = tk.Toplevel(root)
    reminders_window.title("Daily Reminders")
    reminders_window.attributes('-fullscreen', True)
    reminders_window.configure(bg='black')

    # Determine greeting based on time of day
    current_hour = time.localtime().tm_hour
    if 6 <= current_hour < 12:
        greeting = "☀️ GOOD MORNING! ☀️"
        color = "yellow"
    elif 12 <= current_hour < 18:
        greeting = "🌤️ GOOD AFTERNOON! 🌤️"
        color = "orange"
    else:
        greeting = "🌙 GOOD EVENING! 🌙"
        color = "lightblue"

    # Title with time-based greeting
    title = tk.Label(reminders_window, text=greeting, font=("Helvetica", 48, "bold"),
                     fg=color, bg="black")
    title.pack(pady=30)

    # Subtitle
    subtitle = tk.Label(reminders_window, text="Before you start:", font=("Helvetica", 32),
                       fg="cyan", bg="black")
    subtitle.pack(pady=10)

    # Reminders frame
    reminders_frame = tk.Frame(reminders_window, bg='black')
    reminders_frame.pack(expand=True, pady=20)

    # Reminder items
    reminders = [
        "✓ Check for water in fuel",
        "✓ Connect app",
        "✓ Stay safe!"
    ]

    for reminder in reminders:
        label = tk.Label(reminders_frame, text=reminder, font=("Helvetica", 36, "bold"),
                        fg="white", bg="black", anchor="w")
        label.pack(pady=15, padx=50)

    # Instructions at bottom
    instructions = tk.Label(reminders_window, text="Press THUMBS UP button or send OV to continue",
                           font=("Helvetica", 28, "bold"), fg="green", bg="black")
    instructions.pack(side=tk.BOTTOM, pady=30)

def show_menu():
    """Display the main menu"""
    global menu_mode, menu_window, menu_buttons, menu_arrows, menu_selected_index, menu_position_label

    menu_mode = True
    menu_selected_index = 0  # Start at first item
    menu_buttons = []
    menu_arrows = []

    menu_window = tk.Toplevel(root)
    menu_window.title("System Menu")
    menu_window.attributes('-fullscreen', True)
    menu_window.configure(bg='#0a0a0a')

    # ═══════════════════════════════════════════════════════════════════
    # TOP INFO BAR - Professional header with system info
    # ═══════════════════════════════════════════════════════════════════
    header_frame = tk.Frame(menu_window, bg='#1a1a1a', highlightbackground='#333333',
                           highlightthickness=2)
    header_frame.pack(fill=tk.X, padx=10, pady=5)

    # Left side - System Information Panel
    left_info_frame = tk.Frame(header_frame, bg='#1a1a1a')
    left_info_frame.pack(side=tk.LEFT, padx=15, pady=8)

    # WiFi status indicator
    wifi_status = check_wifi_status()
    if "CONNECTED" in wifi_status:
        wifi_color = "#00ff00"  # Bright green
        wifi_symbol = "●"
    else:
        wifi_color = "#ff0000"  # Bright red
        wifi_symbol = "●"

    wifi_label = tk.Label(left_info_frame, text=f"{wifi_symbol} {wifi_status}",
                         font=("Helvetica", 24, "bold"), fg=wifi_color, bg="#1a1a1a")
    wifi_label.pack(anchor='w')

    # IP address
    ip_address = get_ip_address()
    ip_label = tk.Label(left_info_frame, text=f"IP: {ip_address}",
                       font=("Helvetica", 22), fg="#00d4ff", bg="#1a1a1a")
    ip_label.pack(anchor='w', pady=2)

    # Username
    username = get_username()
    user_label = tk.Label(left_info_frame, text=f"User: {username}",
                         font=("Helvetica", 22), fg="#00d4ff", bg="#1a1a1a")
    user_label.pack(anchor='w', pady=2)

    # Center - Title and Version
    center_frame = tk.Frame(header_frame, bg='#1a1a1a')
    center_frame.pack(side=tk.LEFT, expand=True, padx=15, pady=8)

    title = tk.Label(center_frame, text="SYSTEM MENU", font=("Helvetica", 32, "bold"),
                     fg="#00d4ff", bg="#1a1a1a")
    title.pack()

    version_label = tk.Label(center_frame, text=f"Version {VERSION}", font=("Helvetica", 20),
                            fg="#888888", bg="#1a1a1a")
    version_label.pack()

    # Right side - Totals Panel
    global menu_daily_label, menu_season_label
    right_info_frame = tk.Frame(header_frame, bg='#1a1a1a')
    right_info_frame.pack(side=tk.RIGHT, padx=15, pady=8)

    totals_title = tk.Label(right_info_frame, text="TOTALS",
                           font=("Helvetica", 22, "bold"), fg="#888888", bg="#1a1a1a")
    totals_title.pack()

    menu_daily_label = tk.Label(right_info_frame, text=f"Daily: {daily_total:.1f} gal",
                          font=("Helvetica", 24, "bold"), fg="#00ffff", bg="#1a1a1a")
    menu_daily_label.pack(anchor='e', pady=2)

    menu_season_label = tk.Label(right_info_frame, text=f"Season: {season_total:.1f} gal",
                           font=("Helvetica", 24, "bold"), fg="#00ff00", bg="#1a1a1a")
    menu_season_label.pack(anchor='e', pady=2)

    # ═══════════════════════════════════════════════════════════════════
    # MENU OPTIONS SECTION
    # ═══════════════════════════════════════════════════════════════════

    # Position indicator - make it global so we can update it
    global menu_position_label
    menu_items_names = ["VIEW LOGS", "FILL HISTORY", "FULL TEST", "RESET SEASON", "SELF TEST",
                       "SYSTEM UPDATE", "SHUTDOWN", "REBOOT", "EXIT TO DESKTOP", "EXIT MENU"]
    menu_position_label = tk.Label(menu_window,
                                    text=f"Option 1 of 10: {menu_items_names[0]}",
                                    font=("Helvetica", 18, "bold"),
                                    fg="#ffffff", bg='#0a0a0a')
    menu_position_label.pack(pady=2)

    # Menu buttons frame with professional styling
    button_frame = tk.Frame(menu_window, bg='#0a0a0a')
    button_frame.pack(expand=True, pady=2)

    # View Logs button with arrow
    logs_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                         fg="#ffff00", bg="#0a0a0a")
    logs_arrow.pack()
    logs_btn = tk.Button(button_frame, text="VIEW LOGS", font=("Helvetica", 22, "bold"),
                         bg="blue", fg="white", command=show_log_viewer,
                         width=18, height=1, borderwidth=2)
    logs_btn.pack(pady=2)
    menu_buttons.append(logs_btn)
    menu_arrows.append(logs_arrow)

    # Fill History button with arrow
    fill_history_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                         fg="#ffff00", bg="#0a0a0a")
    fill_history_arrow.pack()
    fill_history_btn = tk.Button(button_frame, text="FILL HISTORY", font=("Helvetica", 22, "bold"),
                         bg="purple", fg="white", command=show_fill_history,
                         width=18, height=1, borderwidth=2)
    fill_history_btn.pack(pady=2)
    menu_buttons.append(fill_history_btn)
    menu_arrows.append(fill_history_arrow)

    # Full Test button with arrow
    full_test_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                         fg="#ffff00", bg="#0a0a0a")
    full_test_arrow.pack()
    full_test_btn = tk.Button(button_frame, text="FULL TEST", font=("Helvetica", 22, "bold"),
                         bg="cyan", fg="white", command=run_full_test,
                         width=18, height=1, borderwidth=2)
    full_test_btn.pack(pady=2)
    menu_buttons.append(full_test_btn)
    menu_arrows.append(full_test_arrow)

    # Reset Season button with arrow - MOVED HERE TO INDEX 3
    reset_season_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                                 fg="#ffff00", bg="#0a0a0a")
    reset_season_arrow.pack()
    reset_season_btn = tk.Button(button_frame, text="RESET SEASON", font=("Helvetica", 22, "bold"),
                                bg="orange", fg="white", command=lambda: confirm_reset_season(),
                                width=18, height=1, borderwidth=2)
    reset_season_btn.pack(pady=2)
    menu_buttons.append(reset_season_btn)
    menu_arrows.append(reset_season_arrow)

    # Self Test button with arrow - NOW AT INDEX 4
    test_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                         fg="#ffff00", bg="#0a0a0a")
    test_arrow.pack()
    test_btn = tk.Button(button_frame, text="SELF TEST", font=("Helvetica", 22, "bold"),
                         bg="green", fg="white", command=run_self_test,
                         width=18, height=1, borderwidth=2)
    test_btn.pack(pady=2)
    menu_buttons.append(test_btn)
    menu_arrows.append(test_arrow)

    # Update button with arrow - NOW AT INDEX 5
    update_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                           fg="#ffff00", bg="#0a0a0a")
    update_arrow.pack()
    update_btn = tk.Button(button_frame, text="SYSTEM UPDATE", font=("Helvetica", 22, "bold"),
                          bg="purple", fg="white", command=run_system_update,
                          width=18, height=1, borderwidth=2)
    update_btn.pack(pady=2)
    menu_buttons.append(update_btn)
    menu_arrows.append(update_arrow)

    # Shutdown button with arrow
    shutdown_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                             fg="#ffff00", bg="#0a0a0a")
    shutdown_arrow.pack()
    shutdown_btn = tk.Button(button_frame, text="SHUTDOWN", font=("Helvetica", 22, "bold"),
                            bg="red", fg="white", command=shutdown_system,
                            width=18, height=1, borderwidth=2)
    shutdown_btn.pack(pady=2)
    menu_buttons.append(shutdown_btn)
    menu_arrows.append(shutdown_arrow)

    # Reboot button with arrow
    reboot_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                           fg="#ffff00", bg="#0a0a0a")
    reboot_arrow.pack()
    reboot_btn = tk.Button(button_frame, text="REBOOT", font=("Helvetica", 22, "bold"),
                          bg="orange", fg="white", command=reboot_system,
                          width=18, height=1, borderwidth=2)
    reboot_btn.pack(pady=2)
    menu_buttons.append(reboot_btn)
    menu_arrows.append(reboot_arrow)


    # Exit to Desktop button with arrow
    exit_desktop_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                         fg="#ffff00", bg="#0a0a0a")
    exit_desktop_arrow.pack()
    exit_desktop_btn = tk.Button(button_frame, text="EXIT TO DESKTOP", font=("Helvetica", 22, "bold"),
                         bg="red4", fg="white", command=exit_to_desktop,
                         width=18, height=1, borderwidth=2)
    exit_desktop_btn.pack(pady=2)
    menu_buttons.append(exit_desktop_btn)
    menu_arrows.append(exit_desktop_arrow)
    # Exit button with arrow
    exit_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                         fg="#ffff00", bg="#0a0a0a")
    exit_arrow.pack()
    exit_btn = tk.Button(button_frame, text="EXIT MENU", font=("Helvetica", 22, "bold"),
                         bg="gray30", fg="white", command=close_menu,
                         width=18, height=1, borderwidth=2)
    exit_btn.pack(pady=2)
    menu_buttons.append(exit_btn)
    menu_arrows.append(exit_arrow)

    # Instructions - Professional footer
    footer_frame = tk.Frame(menu_window, bg='#1a1a1a', highlightbackground='#333333',
                           highlightthickness=2)
    footer_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=5)

    instructions = tk.Label(footer_frame, text="+1 = DOWN  │  -1 = UP  │  OV = SELECT",
                           font=("Helvetica", 20, "bold"), fg="#00d4ff", bg="#1a1a1a")
    instructions.pack(pady=8)

    # Apply initial highlight
    update_menu_highlight()

def iol_power_cycle():
    """Power-cycle the IOL port in a background thread to trigger re-negotiation.

    Called when the flow meter is detected as disconnected (all-zero or stale data).
    The IOL master firmware does not auto-negotiate when a device is reconnected,
    so the port must be powered off and back on to restart the IO-Link handshake.
    """
    global iol_power_cycle_in_progress, last_power_cycle_time

    try:
        print(f"IOL power-cycle: Starting port {config.IOL_PORT} power-cycle attempt", flush=True)

        # Step 1: Power off
        try:
            iolhat.power(config.IOL_PORT, 0)
            print(f"IOL power-cycle: Port {config.IOL_PORT} powered OFF", flush=True)
        except Exception as e:
            print(f"IOL power-cycle: Failed to power off port {config.IOL_PORT}: {e}", flush=True)
            return

        # Step 2: Wait for port to fully power down
        time.sleep(0.5)

        # Step 3: Power on
        try:
            iolhat.power(config.IOL_PORT, 1)
            print(f"IOL power-cycle: Port {config.IOL_PORT} powered ON", flush=True)
        except Exception as e:
            print(f"IOL power-cycle: Failed to power on port {config.IOL_PORT}: {e}", flush=True)
            return

        # Step 4: Wait for IO-Link handshake to complete
        time.sleep(1.5)

        # Step 5: Set LED back to green (optimistic - will go red again if still disconnected)
        try:
            iolhat.led(config.IOL_PORT, iolhat.LED_GREEN)
        except:
            pass

        print(f"IOL power-cycle: Complete. Waiting for IO-Link handshake.", flush=True)

    except Exception as e:
        print(f"IOL power-cycle: Unexpected error: {e}", flush=True)
    finally:
        last_power_cycle_time = time.time()
        iol_power_cycle_in_progress = False


def _try_iol_power_cycle():
    """Check rate-limiting and spawn power-cycle thread if appropriate."""
    global iol_power_cycle_in_progress

    if iol_power_cycle_in_progress:
        return

    if (time.time() - last_power_cycle_time) < config.IOL_RECONNECT_INTERVAL:
        return

    iol_power_cycle_in_progress = True
    cycle_thread = threading.Thread(target=iol_power_cycle, daemon=True)
    cycle_thread.start()

def read_flow_meter():
    """Read data from the Picomag flow meter via IO-Link"""
    global last_totalizer_liters, last_flow_rate, connection_error, error_message, last_successful_read_time
    global consecutive_identical_raw, last_raw_data

    try:
        # Read process data from IO-Link device
        raw_data = iolhat.pd(config.IOL_PORT, 0, config.DATA_LENGTH, None)

        if len(raw_data) >= 15:
            # Check if data is all zeros (indicates IO-Link timeout/no response)
            if raw_data == b'\x00' * len(raw_data):
                connection_error = True
                error_message = "Device not responding (all-zero data)"
                try:
                    iolhat.led(config.IOL_PORT, iolhat.LED_RED)
                except:
                    pass
                last_raw_data = None
                consecutive_identical_raw = 0
                _try_iol_power_cycle()
                return last_totalizer_liters * config.LITERS_TO_GALLONS

            # If we were in error state and now getting valid non-stale data, log recovery
            if connection_error and raw_data != last_raw_data:
                print(f"Flow meter reconnected - valid data received", flush=True)

            # Check for stale data (byte-for-byte identical reads = meter disconnected)
            # A connected meter always has micro-fluctuations in the raw bytes
            if raw_data == last_raw_data:
                consecutive_identical_raw += 1
                if consecutive_identical_raw >= STALE_RAW_THRESHOLD:
                    connection_error = True
                    stale_secs = consecutive_identical_raw * (config.UPDATE_INTERVAL / 1000.0)
                    error_message = f"Stale data - meter may be disconnected ({stale_secs:.0f}s)"
                    if consecutive_identical_raw == STALE_RAW_THRESHOLD:
                        print(f"Flow meter stale data detected after {stale_secs:.0f}s", flush=True)
                        try:
                            iolhat.led(config.IOL_PORT, iolhat.LED_RED)
                        except:
                            pass
                    _try_iol_power_cycle()
                    return last_totalizer_liters * config.LITERS_TO_GALLONS
            else:
                if consecutive_identical_raw >= STALE_RAW_THRESHOLD:
                    print(f"Flow meter data flowing again", flush=True)
                consecutive_identical_raw = 0
            last_raw_data = raw_data

            # Decode the data according to Picomag format
            totalizer_liters = abs(struct.unpack('>f', raw_data[4:8])[0])
            flow_rate_l_per_s = struct.unpack('>f', raw_data[8:12])[0]

            last_totalizer_liters = totalizer_liters
            last_flow_rate = flow_rate_l_per_s
            # Clear error state - set LED green on reconnect
            if connection_error:
                try:
                    iolhat.led(config.IOL_PORT, iolhat.LED_GREEN)
                except:
                    pass
            connection_error = False
            error_message = ""
            last_successful_read_time = time.time()

            return totalizer_liters * config.LITERS_TO_GALLONS
        else:
            connection_error = True
            error_message = "Invalid data length"
            return last_totalizer_liters * config.LITERS_TO_GALLONS

    except Exception as e:
        connection_error = True
        error_message = str(e)
        _try_iol_power_cycle()
        return last_totalizer_liters * config.LITERS_TO_GALLONS


def socket_command_listener():
    """Listen for commands from rotorsync BLE server via localhost socket"""
    global requested_gallons, override_mode, colors_are_green
    global fill_requested_gallons, mix_requested_gallons, current_mode, batch_mix_data

    import socket as sock_module
    import json

    DASHBOARD_PORT = 9999
    debug_log = config.SERIAL_DEBUG_LOG

    sock_server = sock_module.socket(sock_module.AF_INET, sock_module.SOCK_STREAM)
    sock_server.setsockopt(sock_module.SOL_SOCKET, sock_module.SO_REUSEADDR, 1)
    sock_server.bind(("127.0.0.1", DASHBOARD_PORT))
    sock_server.listen(1)
    sock_server.settimeout(1.0)

    print(f"Socket listener started on port {DASHBOARD_PORT}")
    with open(debug_log, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Socket listener started on port {DASHBOARD_PORT}\n")

    while True:
        try:
            try:
                client, addr = sock_server.accept()
                client.settimeout(5.0)
                try:
                    data = client.recv(4096).decode("utf-8").strip()
                    if data:
                        for line in data.split("\n"):
                            line = line.strip()
                            if not line:
                                continue

                            with open(debug_log, "a") as f:
                                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Socket received: '{line}'\n")

                            if line == "STATUS":
                                actual = last_totalizer_liters * config.LITERS_TO_GALLONS
                                response = f"REQ:{requested_gallons:.1f}|ACT:{actual:.1f}|MODE:{current_mode}\n"
                                client.send(response.encode())
                                continue

                            elif line == "MIX":
                                root.after(0, lambda: switch_mode("mix"))

                            elif line == "RESET":
                                root.after(0, pulse_flow_reset)

                            elif line == "FILL":
                                root.after(0, lambda: switch_mode("fill"))

                            elif line.startswith("BATCHMIX_ERROR:"):
                                # Handle BatchMix validation error
                                error_msg = line[15:]
                                msg = f"Socket: BatchMix ERROR - {error_msg}"
                                print(msg)
                                with open(debug_log, "a") as f:
                                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                # Display error on screen
                                root.after(0, lambda e=error_msg: show_batchmix_error(e))

                            elif line.startswith("BATCHMIX:"):
                                try:
                                    json_str = line[9:]
                                    batch_mix_data = json.loads(json_str)
                                    msg = f"Socket: BatchMix received - {len(batch_mix_data.get('products', []))} products"
                                    print(msg)
                                    with open(debug_log, "a") as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")

                                    # Set mix mode requested gallons to water_needed amount (don't change regular requested_gallons)
                                    water_needed = batch_mix_data.get('water_needed', 0)
                                    if water_needed > 0:
                                        mix_requested_gallons = water_needed
                                        # Only update display if in mix mode
                                        if current_mode == "mix":
                                            # Show decimal if present, otherwise whole number
                                            if water_needed == int(water_needed):
                                                req_str = f"{int(water_needed)}"
                                            else:
                                                req_str = f"{water_needed:.1f}"
                                            root.after(0, lambda s=req_str: draw_requested_number(s, "green" if colors_are_green else "red"))

                                    root.after(0, update_batch_mix_overlay)
                                except Exception as bme:
                                    msg = f"Socket: BatchMix parse error: {bme}"
                                    print(msg)
                                    with open(debug_log, "a") as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")


                            elif line == "MOPEKA_OFFLINE":
                                root.after(0, _mopeka_offline)

                            elif line.startswith("MOPEKA:"):
                                try:
                                    parts = line[7:].split("|")
                                    if len(parts) >= 4:
                                        _m1g = float(parts[0])
                                        _m2g = float(parts[1])
                                        _m1q = int(parts[2])
                                        _m2q = int(parts[3])
                                        root.after(0, _apply_mopeka, _m1g, _m2g, _m1q, _m2q)
                                except Exception as me:
                                    print(f"Mopeka parse error: {me}", flush=True)

                            elif line == "HISTORY":
                                try:
                                    with open("/home/pi/fill_history.log", "r") as hf:
                                        all_lines = hf.readlines()
                                        last_5 = all_lines[-5:] if len(all_lines) >= 5 else all_lines
                                        history_items = []
                                        for entry in last_5:
                                            parts = entry.strip().split("|")
                                            if len(parts) >= 3:
                                                ts = parts[0].strip()
                                                req = parts[1].replace("Requested:", "").replace("gal", "").strip()
                                                act = parts[2].replace("Actual:", "").replace("gal", "").strip()
                                                history_items.append(f"{ts},{req},{act}")
                                        history_response = ";".join(history_items)
                                        client.send(f"HIST:{history_response}\n".encode())
                                except Exception:
                                    client.send(b"HIST:\n")
                                continue

                            elif line in ['+1', '-1', '+10', '-10']:
                                try:
                                    adjustment = int(line)
                                    requested_gallons += adjustment
                                    if requested_gallons < 0:
                                        requested_gallons = 0
                                    colors_are_green = False
                                    if current_mode == 'fill':
                                        fill_requested_gallons = requested_gallons
                                    else:
                                        mix_requested_gallons = requested_gallons
                                    save_mode_presets()
                                    msg = f"Socket: Adjusted by {adjustment}, requested gallons now {requested_gallons}"
                                    print(msg)
                                    with open(debug_log, "a") as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                except ValueError:
                                    pass

                            elif line == 'PS':
                                msg = "Socket: Pump Stop command received"
                                print(msg)
                                with open(debug_log, "a") as f:
                                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                root.after(0, pump_stop_relay)

                            client.send(b"OK\n")
                except sock_module.timeout:
                    pass
                finally:
                    client.close()
            except sock_module.timeout:
                pass
        except Exception as e:
            print(f"Socket listener error: {e}")
            time.sleep(1)

def serial_listener():
    """Listen for serial messages with format: requested,actual"""
    global requested_gallons, serial_connected, override_mode, colors_are_green, last_heartbeat_time
    global fill_requested_gallons, mix_requested_gallons, current_mode

    debug_log = config.SERIAL_DEBUG_LOG
    buffer = ""

    try:
        ser = serial.Serial(config.SERIAL_PORT, config.SERIAL_BAUD, timeout=0.5)
        ser.reset_input_buffer()
        serial_connected = True
        msg = f"Serial listener started on {config.SERIAL_PORT} at {config.SERIAL_BAUD} baud"
        print(msg)
        with open(debug_log, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")

        while True:
            try:
                if ser.in_waiting > 0:
                    # Read all available bytes
                    raw_bytes = ser.read(ser.in_waiting)
                    with open(debug_log, 'a') as f:
                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Raw bytes: {raw_bytes} (hex: {raw_bytes.hex()})\n")

                    # Decode and add to buffer
                    chunk = raw_bytes.decode('utf-8', errors='ignore')
                    buffer += chunk
                    with open(debug_log, 'a') as f:
                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Decoded: '{chunk}' | Buffer now: '{buffer}'\n")

                    # Process complete lines (ending with \n or \r)
                    while '\n' in buffer or '\r' in buffer:
                        # Split on either \n or \r
                        if '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                        else:
                            line, buffer = buffer.split('\r', 1)

                        line = line.strip()
                        with open(debug_log, 'a') as f:
                            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Complete line: '{line}'\n")

                        if line:
                            # Update heartbeat if we receive OK message
                            if line == 'OK':
                                last_heartbeat_time = time.time()
                                with open(debug_log, 'a') as f:
                                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Heartbeat received (OK)\n")
                                continue  # Don't process OK as a command

                            # Handle exit confirmation dialog
                            if exit_confirm_window:
                                if line == 'OV':
                                    msg = "Serial: Exit confirmation (OV - Confirm)"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    if exit_confirm_handler:
                                        root.after(0, exit_confirm_handler)
                                elif line == 'PS':
                                    msg = "Serial: Exit confirmation (PS - Cancel)"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    if exit_cancel_handler:
                                        root.after(0, exit_cancel_handler)
                                else:
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Ignored in exit confirmation: '{line}'\n")

                            # Handle reset season confirmation dialog
                            elif reset_season_confirm_window:
                                if line == 'OV':
                                    msg = "Serial: Reset season confirmation (OV - Confirm)"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    if reset_season_confirm_handler:
                                        root.after(0, reset_season_confirm_handler)
                                elif line == 'PS':
                                    msg = "Serial: Reset season confirmation (PS - Cancel)"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    if reset_season_cancel_handler:
                                        root.after(0, reset_season_cancel_handler)
                                else:
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Ignored in reset season confirmation: '{line}'\n")

                            # Handle reminders mode - dismiss on OV
                            elif reminders_mode:
                                if line == 'OV':
                                    msg = "Serial: Dismiss reminders"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    root.after(0, dismiss_reminders)
                                else:
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Ignored in reminders mode: '{line}'\n")

                            # Handle log viewer navigation if in log viewer mode
                            elif log_viewer_mode:
                                if line == '+1':
                                    msg = "Serial: Log viewer scroll down"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    root.after(0, log_viewer_scroll_down)
                                elif line == '-1':
                                    msg = "Serial: Log viewer scroll up"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    root.after(0, log_viewer_scroll_up)
                                elif line == 'OV':
                                    msg = "Serial: Log viewer exit"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    root.after(0, close_log_viewer)
                                else:
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Ignored in log viewer mode: '{line}'\n")

                            # Handle fill history navigation if in fill history mode
                            elif fill_history_mode:
                                if line == '+1':
                                    msg = "Serial: Fill history scroll down"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    root.after(0, fill_history_scroll_down)
                                elif line == '-1':
                                    msg = "Serial: Fill history scroll up"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    root.after(0, fill_history_scroll_up)
                                elif line == 'OV':
                                    msg = "Serial: Fill history exit"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    root.after(0, close_fill_history)
                                else:
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Ignored in fill history mode: '{line}'\n")

                            # Handle self-test mode
                            elif self_test_mode:
                                if line == 'OV':
                                    msg = "Serial: Self-test exit"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    root.after(0, close_self_test)
                                else:
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Ignored in self-test mode: '{line}'\n")

                            # Handle full-test mode
                            elif full_test_mode:
                                if line == 'OV':
                                    # Check if OV test has been done already
                                    if full_test_window and hasattr(full_test_window, 'test_status'):
                                        if not full_test_window.test_status['OV']:
                                            # First press: mark OV as tested
                                            msg = "Serial: Full-test OV command detected (marked as tested)"
                                            print(msg)
                                            with open(debug_log, 'a') as f:
                                                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                            root.after(0, lambda: full_test_window.mark_tested('OV'))
                                        else:
                                            # Second press: exit full test
                                            msg = "Serial: Full-test exit (OV pressed second time)"
                                            print(msg)
                                            with open(debug_log, 'a') as f:
                                                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                            root.after(0, close_full_test)
                                    else:
                                        # Fallback: just exit
                                        msg = "Serial: Full-test exit (OV pressed)"
                                        print(msg)
                                        with open(debug_log, 'a') as f:
                                            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                        root.after(0, close_full_test)
                                elif line in ['-1', '+1', '-10', '+10', 'PS']:
                                    msg = f"Serial: Full-test {line} command detected"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    # Mark the corresponding test as passed
                                    if full_test_window and hasattr(full_test_window, 'mark_tested'):
                                        if line == '-1':
                                            root.after(0, lambda: full_test_window.mark_tested('minus_1'))
                                        elif line == '+1':
                                            root.after(0, lambda: full_test_window.mark_tested('plus_1'))
                                        elif line == '-10':
                                            root.after(0, lambda: full_test_window.mark_tested('minus_10'))
                                        elif line == '+10':
                                            root.after(0, lambda: full_test_window.mark_tested('plus_10'))
                                        elif line == 'PS':
                                            root.after(0, lambda: full_test_window.mark_tested('PS'))
                                else:
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Ignored in full-test mode: '{line}'\n")

                            # Handle update mode
                            elif update_mode:
                                if line == 'OV':
                                    msg = "Serial: Update exit"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    root.after(0, close_update)
                                else:
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Ignored in update mode: '{line}'\n")

                            # Handle menu navigation if in menu mode
                            elif menu_mode:
                                if line == '+1':
                                    msg = "Serial: Menu navigate down"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    root.after(0, menu_navigate_down)
                                elif line == '-1':
                                    msg = "Serial: Menu navigate up"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    root.after(0, menu_navigate_up)
                                elif line == 'OV':
                                    msg = "Serial: Menu select"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    root.after(0, menu_select)
                                else:
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Ignored in menu mode: '{line}'\n")

                            # Normal dashboard mode
                            else:
                                # Handle adjustment commands
                                if line in ['+1', '-1', '+10', '-10']:
                                    try:
                                        adjustment = int(line)
                                        requested_gallons += adjustment
                                        # Don't allow requested gallons to go below zero
                                        if requested_gallons < 0:
                                            requested_gallons = 0
                                        colors_are_green = False  # Reset colors when requested gallons changes
                                        # Update the current mode's preset
                                        if current_mode == 'fill':
                                            fill_requested_gallons = requested_gallons
                                        else:
                                            mix_requested_gallons = requested_gallons
                                        save_mode_presets()
                                        msg = f"Serial: Adjusted by {adjustment}, requested gallons now {requested_gallons}"
                                        print(msg)
                                        with open(debug_log, 'a') as f:
                                            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    except ValueError as ve:
                                        with open(debug_log, 'a') as f:
                                            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - ValueError parsing adjustment: {ve}\n")

                                # Handle special commands
                                elif line == 'PS':
                                    msg = "Serial: Pump Stop command received - activating relay"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    # Run relay in separate thread to not block serial listener
                                    relay_thread = threading.Thread(target=pump_stop_relay, daemon=True)
                                    relay_thread.start()

                                elif line == 'OV':
                                    global override_enabled_time
                                    # Check if requested gallons is 0 to trigger menu
                                    if requested_gallons == 0:
                                        msg = "Serial: Menu access triggered (gallons=0, OV pressed)"
                                        print(msg)
                                        with open(debug_log, 'a') as f:
                                            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                        # Show menu in main thread
                                        root.after(0, show_menu)
                                    else:
                                        override_mode = not override_mode
                                        if override_mode:
                                            override_enabled_time = time.time()  # Record when enabled
                                        msg = f"Serial: Override mode {'ENABLED' if override_mode else 'DISABLED'}"
                                        print(msg)
                                        with open(debug_log, 'a') as f:
                                            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")


                                elif line == 'TU':
                                    msg = "Serial: Thumbs Up command received"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    root.after(0, lambda: change_colors_to_green(from_button=True))
                                    root.after(0, record_pending_fill)

                                elif line == 'MIX':
                                    msg = "Serial: Mix mode command received"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    root.after(0, lambda: switch_mode('mix'))

                                elif line == 'FILL':
                                    msg = "Serial: Fill mode command received"
                                    print(msg)
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                                    root.after(0, lambda: switch_mode('fill'))

                                else:
                                    # Unknown command
                                    with open(debug_log, 'a') as f:
                                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Unknown command: '{line}'\n")
                else:
                    time.sleep(0.1)
            except Exception as e:
                msg = f"Serial read error: {e}"
                print(msg)
                with open(debug_log, 'a') as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                time.sleep(0.1)

    except Exception as e:
        serial_connected = False
        msg = f"Serial listener error: {e}"
        print(msg)
        with open(debug_log, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")

def initialize_gpio():
    """Initialize GPIO for relay control"""
    if not GPIO_AVAILABLE:
        print("GPIO not available, skipping GPIO initialization")
        return True

    try:
        # Disable warnings about channels already in use
        GPIO.setwarnings(False)
        # Configure GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(config.PUMP_STOP_RELAY_PIN, GPIO.OUT)
        GPIO.setup(config.FLOW_RESET_PIN, GPIO.OUT)
        GPIO.output(config.FLOW_RESET_PIN, GPIO.LOW)
        GPIO.output(config.PUMP_STOP_RELAY_PIN, GPIO.LOW)
        print(f"GPIO initialized: Relay on pin {config.PUMP_STOP_RELAY_PIN}")
        return True
    except Exception as e:
        print(f"Failed to initialize GPIO: {e}")
        return False

# Flow meter reset
flow_reset_scheduled = False

def pulse_flow_reset():
    global flow_reset_scheduled
    dbg = open("/home/pi/reset_debug.log", "a")
    dbg.write("pulse called\n")
    dbg.close()
    if not GPIO_AVAILABLE:
        return
    try:
        dbg = open("/home/pi/reset_debug.log", "a")
        dbg.write("pulsing gpio0\n")
        dbg.close()
        GPIO.output(config.FLOW_RESET_PIN, GPIO.HIGH)
        time.sleep(config.FLOW_RESET_DURATION)
        GPIO.output(config.FLOW_RESET_PIN, GPIO.LOW)
        dbg = open("/home/pi/reset_debug.log", "a")
        dbg.write("done\n")
        dbg.close()
    except Exception as e:
        dbg = open("/home/pi/reset_debug.log", "a")
        dbg.write(str(e) + "\n")
        dbg.close()
    flow_reset_scheduled = False

def schedule_flow_reset():
    global flow_reset_scheduled
    if flow_reset_scheduled:
        return
    flow_reset_scheduled = True
    root.after(int(config.FLOW_RESET_DELAY * 1000), pulse_flow_reset)


def initialize_iol():
    """Initialize IO-Link port"""
    try:
        # Power on the port
        iolhat.power(config.IOL_PORT, 1)
        # Set LED to green
        iolhat.led(config.IOL_PORT, iolhat.LED_GREEN)
        time.sleep(0.5)
        print(f"IO-Link Port {config.IOL_PORT+1} initialized successfully")
        return True
    except Exception as e:
        print(f"Failed to initialize IO-Link Port {config.IOL_PORT+1}: {e}")
        return False

# Tkinter GUI setup
root = tk.Tk()
root.title("Tank Dashboard")
root.configure(bg="black")
root.attributes("-fullscreen", True)

# Get screen dimensions
screen_width = root.winfo_screenwidth()
screen_height = root.winfo_screenheight()

# Create ONE full-screen canvas for everything
canvas = tk.Canvas(root, bg="black", highlightthickness=0)
canvas.pack(fill='both', expand=True)

# Draw full-screen barber pole stripes
def draw_fullscreen_stripes():
    """Draw barber pole stripes across entire screen"""
    # Get actual canvas size after it's been packed
    canvas.update()
    width = canvas.winfo_width()
    height = canvas.winfo_height()

    stripe_height = 30
    dark_yellow = "#CC9900"

    for i, stripe_y in enumerate(range(0, height, stripe_height)):
        stripe_color = "red" if i % 2 == 0 else dark_yellow
        canvas.create_rectangle(0, stripe_y, width, stripe_y + stripe_height,
                               fill=stripe_color, outline="", tags="stripes")

# Draw stripes after window is created
root.update()
# draw_fullscreen_stripes()  # Disabled - using solid black background


def _apply_mopeka(m1g, m2g, m1q, m2q):
    """Apply mopeka values and update display (called from main thread via root.after)"""
    global mopeka1_gallons, mopeka2_gallons, mopeka1_quality, mopeka2_quality, mopeka_connected
    mopeka1_gallons = m1g
    mopeka2_gallons = m2g
    mopeka1_quality = m1q
    mopeka2_quality = m2q
    mopeka_connected = True
    print(f"Mopeka applied: front={m1g:.0f} back={m2g:.0f} q={m1q}/{m2q}", flush=True)
    update_mopeka_display()


def _mopeka_offline():
    """Mark mopeka sensors as offline and update display"""
    global mopeka_connected
    mopeka_connected = False
    print("Mopeka offline", flush=True)
    update_mopeka_display()


def update_mopeka_display():
    """Draw Mopeka tank levels in top-right corner of screen"""
    canvas.delete("mopeka_display")
    
    width = canvas.winfo_width()
    x = width - 20  # 20px from right edge
    font = ("Helvetica", 54)
    
    if not mopeka_connected:
        canvas.create_text(x, 40, text="Tanks: No Signal", font=font,
                          fill="#ff0000", anchor="ne", tags="mopeka_display")
        return
    
    # Quality indicator: 0=no signal, 1=weak, 2=ok, 3=good
    def quality_color(q):
        if q >= 3: return "#00ff00"   # green
        if q >= 2: return "#ffff00"   # yellow
        if q >= 1: return "#ff8800"   # orange
        return "#ff0000"              # red (no signal)
    
    # Front tank - top right
    color1 = quality_color(mopeka1_quality)
    label1 = f"Front: {mopeka1_gallons:.0f} gal"
    canvas.create_text(x, 40, text=label1, font=font,
                      fill=color1, anchor="ne", tags="mopeka_display")
    
    # Back tank - below front
    color2 = quality_color(mopeka2_quality)
    label2 = f"Back: {mopeka2_gallons:.0f} gal"
    canvas.create_text(x, 110, text=label2, font=font,
                      fill=color2, anchor="ne", tags="mopeka_display")

def draw_requested_number(text, color="red"):
    """Draw the requested number with white outline on full-screen canvas"""
    global batch_mix_layout_active, _last_requested_text, _last_requested_color

    # If batch mix layout is active, use different positioning
    if batch_mix_layout_active:
        redraw_numbers_for_batch_mix()
        return

    # Only redraw if value or color changed (prevents flicker)
    if text == _last_requested_text and color == _last_requested_color:
        return

    _last_requested_text = text
    _last_requested_color = color

    # Delete old requested number
    canvas.delete("requested")

    # Position: centered horizontally, 20% from top
    x = canvas.winfo_width() // 2
    y = int(canvas.winfo_height() * 0.28)
    font = ("Helvetica", 180, "bold")

    # Draw white outline (8 positions around the text)
    for dx, dy in [(-5,-5), (-5,0), (-5,5), (0,-5), (0,5), (5,-5), (5,0), (5,5)]:
        canvas.create_text(x+dx, y+dy, text=text, font=font, fill="white", tags="requested")

    # Draw text with specified color on top
    canvas.create_text(x, y, text=text, font=font, fill=color, tags="requested")

# Draw text labels on canvas (centered)
canvas.update()
center_x = canvas.winfo_width() // 2
height = canvas.winfo_height()

canvas.create_text(center_x, int(height * 0.08), text="Requested Gallons:", font=("Helvetica", 36, "bold"),
                  fill="white", tags="labels")

# Draw initial requested value
draw_requested_number(f"{config.REQUESTED_GALLONS:.0f}", "red")

canvas.create_text(center_x, int(height * 0.45), text="Actual Gallons:", font=("Helvetica", 36, "bold"),
                  fill="white", tags="labels")

def draw_actual_number(text, color="red"):
    """Draw the actual number with white outline on full-screen canvas"""
    global batch_mix_layout_active, _last_actual_text, _last_actual_color

    # If batch mix layout is active, use different positioning
    if batch_mix_layout_active:
        redraw_numbers_for_batch_mix()
        return

    # Only redraw if value or color changed (prevents flicker)
    if text == _last_actual_text and color == _last_actual_color:
        return

    _last_actual_text = text
    _last_actual_color = color

    # Delete old actual number
    canvas.delete("actual")

    # Position: centered horizontally, 65% from top
    x = canvas.winfo_width() // 2
    y = int(canvas.winfo_height() * 0.65)
    font = ("Helvetica", 240, "bold")

    # Draw white outline (8 positions around the text)
    for dx, dy in [(-6,-6), (-6,0), (-6,6), (0,-6), (0,6), (6,-6), (6,0), (6,6)]:
        canvas.create_text(x+dx, y+dy, text=text, font=font, fill="white", tags="actual")

    # Draw text with specified color on top
    canvas.create_text(x, y, text=text, font=font, fill=color, tags="actual")

# Draw initial value
draw_actual_number("0.0", "red")

# Draw initial mopeka state (no signal)
root.after(1000, update_mopeka_display)

# Status Label (for connection errors)
status_label = ttk.Label(root, text="", font=("Helvetica", 24),
                        foreground="yellow", background="black")
# status_label.pack(pady=2)

# Flow Meter Disconnected Warning (flashing)
flowmeter_disconnected_label = ttk.Label(root, text="FLOW METER\nDISCONNECTED",
                                         font=("Helvetica", 60, "bold"),
                                         foreground="red", background="black")
# flowmeter_disconnected_label.pack(pady=2)
# flowmeter_disconnected_label.pack_forget()

# Switch Box Disconnected Label (shown when heartbeat times out)
switchbox_disconnected_label = ttk.Label(root, text="SWITCH BOX\nDISCONNECTED",
                                         font=("Helvetica", 60, "bold"),
                                         foreground="red", background="black")
# switchbox_disconnected_label.pack(pady=2)
# switchbox_disconnected_label.pack_forget()

# Warning Label (flashing)
warning_label = ttk.Label(root, text="OVER TARGET!", font=("Helvetica", 72, "bold"),
                          foreground="red", background="black")
# warning_label.pack(pady=2)
# warning_label.pack_forget()

# Manual Mode Label (flashing when override is active)
manual_label = ttk.Label(root, text="MANUAL", font=("Helvetica", 90, "bold"),
                         foreground="orange", background="black")
# manual_label.pack(pady=2)
# manual_label.pack_forget()

# Mix Mode Indicator Label (shown in top-left corner when in mix mode)
mode_indicator_label = ttk.Label(root, text="MIX", font=("Helvetica", 48, "bold"),
                                 foreground="cyan", background="black")
# mode_indicator_label initially hidden, shown via place() when in mix mode

# Thumbs up animated GIF support
thumbs_up_frames = []
thumbs_up_frame_index = [0]  # Use list for mutable reference
thumbs_up_label = None
thumbs_up_animation_id = None

def load_thumbs_up_gif():
    """Load thumbs up image (PNG or GIF) for display"""
    global thumbs_up_frames, thumbs_up_label
    
    png_path = "/home/pi/thumbs_up.png"
    gif_path = "/home/pi/thumbs_up.gif"
    
    try:
        from PIL import Image, ImageTk
        import os
        
        # Try PNG first
        if os.path.exists(png_path):
            img = Image.open(png_path)
            # Resize to fit nicely on screen
            img = img.resize((200, 200), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            thumbs_up_frames = [photo]
            print(f"Loaded thumbs up from PNG")
        elif os.path.exists(gif_path):
            img = Image.open(gif_path)
            # Extract all frames from GIF
            thumbs_up_frames = []
            try:
                while True:
                    frame = img.copy()
                    frame = frame.resize((200, 200), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(frame)
                    thumbs_up_frames.append(photo)
                    img.seek(img.tell() + 1)
            except EOFError:
                pass
            print(f"Loaded {len(thumbs_up_frames)} frames from thumbs up GIF")
        else:
            thumbs_up_frames = []
        
        # Create label
        if thumbs_up_frames:
            thumbs_up_label = tk.Label(root, image=thumbs_up_frames[0], bg="black")
        else:
            thumbs_up_label = tk.Label(root, text="OK", font=("Helvetica", 150, "bold"),
                                       foreground="green", background="black")
    except Exception as e:
        print(f"Could not load thumbs up image: {e}")
        thumbs_up_label = tk.Label(root, text="OK", font=("Helvetica", 150, "bold"),
                                   foreground="green", background="black")
def animate_thumbs_up():
    """Animate the thumbs up GIF"""
    global thumbs_up_animation_id
    
    if thumbs_up_frames and thumbs_up_label:
        thumbs_up_frame_index[0] = (thumbs_up_frame_index[0] + 1) % len(thumbs_up_frames)
        thumbs_up_label.config(image=thumbs_up_frames[thumbs_up_frame_index[0]])
        thumbs_up_animation_id = root.after(100, animate_thumbs_up)  # 10 FPS

# Load the GIF on startup
load_thumbs_up_gif()

def update_dashboard():
    """Update the dashboard with current flow meter readings"""
    global last_alert_triggered, override_mode, was_flowing, colors_are_green, heartbeat_disconnected, override_enabled_time

    actual = read_flow_meter()

    # Use green if flag is set, otherwise red
    color = "green" if colors_are_green else "red"

    # Debug log color being used
    button_log = "/home/pi/button_debug.log"
    if colors_are_green:
        with open(button_log, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [UPDATE_DASHBOARD] colors_are_green={colors_are_green}, using color={color}\n")

    draw_actual_number(f"{actual:.1f}", color)

    # Update requested gallons number
    draw_requested_number(f"{requested_gallons:.0f}", color)

    # Check if flow meter has timed out (no successful reads in X seconds)
    flow_meter_disconnected = (time.time() - last_successful_read_time) > config.FLOW_METER_TIMEOUT

    # Check if heartbeat has timed out (no OK message in 11 seconds)
    heartbeat_timeout = (time.time() - last_heartbeat_time) > 11
    if heartbeat_timeout and not heartbeat_disconnected:
        heartbeat_disconnected = True
        print("Heartbeat timeout - Switch box disconnected")
    elif not heartbeat_timeout and heartbeat_disconnected:
        heartbeat_disconnected = False
        print("Heartbeat restored - Switch box reconnected")

    # Detect flow state
    is_flowing = last_flow_rate >= config.FLOW_STOPPED_THRESHOLD

    # Store fill data when flow stops (don't record yet - wait for thumbs up)
    if was_flowing and not is_flowing:
        global pending_fill_gallons, pending_fill_requested, pending_fill_shutoff_type
        # Determine if shutoff was automatic or manual
        shutoff_type = "Auto" if last_alert_triggered else "Manual"

        # Store pending fill data (will be recorded when thumbs up is pressed)
        pending_fill_gallons = actual
        pending_fill_requested = requested_gallons
        pending_fill_shutoff_type = shutoff_type

        print(f"Fill complete - Requested: {requested_gallons:.3f}, Actual: {actual:.3f}, Diff: {actual - requested_gallons:+.3f}, Type: {shutoff_type}")
        print(f"Waiting for thumbs up button to record fill...")

        # NOTE: Do NOT hide thumbs up when flow stops - keep it visible so user can press it
        # Thumbs up will be hidden after button is pressed or when new fill starts

    # Reset colors when new fill cycle starts
    if not was_flowing and is_flowing:
        colors_are_green = False
        # Hide thumbs up when new cycle starts
        if thumbs_up_label:
            thumbs_up_label.place_forget()
        # Clear any pending fill data from previous cycle
        pending_fill_gallons = 0.0
        pending_fill_requested = 0.0
        pending_fill_shutoff_type = ""
        print("New fill cycle started - colors reset to red, thumbs up hidden, pending fill cleared")

    # Update flow state for next cycle
    was_flowing = is_flowing

    # Auto-disable override after 1 minute if no flow (only if flow meter is connected)
    if override_mode and not flow_meter_disconnected:
        if last_flow_rate < config.FLOW_STOPPED_THRESHOLD:
            # No flow detected - check if override has been enabled for more than 60 seconds
            time_since_override_enabled = time.time() - override_enabled_time
            if time_since_override_enabled > 60:
                override_mode = False
                print(f"Override auto-disabled: no flow for {time_since_override_enabled:.0f} seconds (> 60s limit)")
        else:
            # Flow detected - reset the timer so override can stay active
            override_enabled_time = time.time()
    # If flow meter is disconnected, override stays on indefinitely (no auto-disable)

    # Calculate dynamic trigger threshold based on current flow rate
    flow_rate_gpm = last_flow_rate * config.LITERS_PER_SEC_TO_GPM
    trigger_threshold = calculate_trigger_threshold(last_flow_rate)

    # Auto-alert: Trigger GPIO 27 based on flow-adjusted threshold (once per cycle)
    # Only if override mode is OFF and flow meter is connected
    if not override_mode and not flow_meter_disconnected and actual >= requested_gallons - trigger_threshold and not last_alert_triggered:
        last_alert_triggered = True
        print(f"Auto-alert: Flow={flow_rate_gpm:.1f} GPM, threshold={trigger_threshold:.2f}gal, triggering relay for {config.AUTO_ALERT_DURATION}s")
        relay_thread = threading.Thread(target=pump_stop_relay, args=(config.AUTO_ALERT_DURATION,), daemon=True)
        relay_thread.start()
    elif actual < requested_gallons - trigger_threshold:
        last_alert_triggered = False  # Reset for next cycle

    # Update status label
    status_parts = []
    if connection_error:
        status_parts.append(f"IOL: {error_message[:30]}")
    else:
        status_parts.append("IOL: Connected")

    if serial_connected:
        # Show Connected or Disconnected based on heartbeat status
        if heartbeat_disconnected:
            status_parts.append("Serial: Disconnected")
        else:
            status_parts.append("Serial: Connected")
    else:
        status_parts.append("Serial: Disconnected")

    if override_mode:
        status_parts.append("OVERRIDE: ON")

    # Draw status text on canvas
    canvas.delete("status")
    if status_parts:
        canvas.create_text(canvas.winfo_width() // 2, canvas.winfo_height() - 20, text=" | ".join(status_parts),
                          font=("Helvetica", 20), fill="yellow", tags="status")

    # Draw daily total in bottom left corner (only in fill mode)
    canvas.delete("daily_total")
    if current_mode != "mix":
        canvas.create_text(10, canvas.winfo_height() - 10, text=f"Today:\n{daily_total:.1f} gal",
                          font=("Helvetica", 72, "bold"), fill="cyan", anchor="sw", tags="daily_total")

    # Draw skull icons on sides when flow meter is disconnected (3 inches ~= 288pt at 96 DPI)
    # Pulse animation: size varies between 240pt and 288pt with 1-second cycle
    canvas.delete("skull_icons")
    if flow_meter_disconnected:
        import math
        pulse = math.sin(time.time() * 2 * math.pi)  # -1 to 1, completes cycle every 1 second
        skull_size = int(264 + 24 * pulse)  # Varies from 240pt to 288pt

        # Left skull
        canvas.create_text(150, canvas.winfo_height() // 2, text="☠",
                         font=("Helvetica", skull_size, "bold"), fill="red", tags="skull_icons")
        # Right skull
        canvas.create_text(canvas.winfo_width() - 150, canvas.winfo_height() // 2, text="☠",
                         font=("Helvetica", skull_size, "bold"), fill="red", tags="skull_icons")

    # Draw warnings on canvas - collect all active warnings and cycle through them
    canvas.delete("warning")
    canvas.delete("caution_blocks")  # Delete caution blocks from previous frame

    # Special handling for override/caution mode - draw flashing red blocks with caution symbols
    if override_mode:
        # Railroad crossing alternating flash pattern (1 Hz - left side / right side alternate every 0.5s)
        phase = int(time.time() * 2) % 2  # 0 or 1

        # Block dimensions - larger to fit caution symbol
        block_width = 320
        block_height = 380

        # Calculate vertical positions for upper and lower blocks
        upper_y = int(canvas.winfo_height() * 0.35)  # Upper blocks at 35% down screen
        lower_y = int(canvas.winfo_height() * 0.65)  # Lower blocks at 65% down screen

        # Block positions (left and right sides)
        left_x = 50
        right_x = canvas.winfo_width() - 50 - block_width

        # Draw LEFT side blocks when phase=0 (both upper and lower left)
        if phase == 0:
            # Upper left block
            canvas.create_rectangle(left_x, upper_y - block_height//2,
                                   left_x + block_width, upper_y + block_height//2,
                                   fill="red", outline="", tags="caution_blocks")
            canvas.create_text(left_x + block_width//2, upper_y,
                             text="⚠", font=("Helvetica", 180, "bold"),
                             fill="white", tags="caution_blocks")

            # Lower left block
            canvas.create_rectangle(left_x, lower_y - block_height//2,
                                   left_x + block_width, lower_y + block_height//2,
                                   fill="red", outline="", tags="caution_blocks")
            canvas.create_text(left_x + block_width//2, lower_y,
                             text="⚠", font=("Helvetica", 180, "bold"),
                             fill="white", tags="caution_blocks")

        # Draw RIGHT side blocks when phase=1 (both upper and lower right)
        else:
            # Upper right block
            canvas.create_rectangle(right_x, upper_y - block_height//2,
                                   right_x + block_width, upper_y + block_height//2,
                                   fill="red", outline="", tags="caution_blocks")
            canvas.create_text(right_x + block_width//2, upper_y,
                             text="⚠", font=("Helvetica", 180, "bold"),
                             fill="white", tags="caution_blocks")

            # Lower right block
            canvas.create_rectangle(right_x, lower_y - block_height//2,
                                   right_x + block_width, lower_y + block_height//2,
                                   fill="red", outline="", tags="caution_blocks")
            canvas.create_text(right_x + block_width//2, lower_y,
                             text="⚠", font=("Helvetica", 180, "bold"),
                             fill="white", tags="caution_blocks")

        # Draw "MANUAL" text at center bottom
        canvas.create_text(canvas.winfo_width() // 2, int(canvas.winfo_height() * 0.88),
                         text="MANUAL", font=("Helvetica", 90, "bold"),
                         fill="orange", tags="warning")

    else:
        # Build list of active warnings (in priority order) - only when NOT in override mode
        active_warnings = []

        if heartbeat_disconnected:
            active_warnings.append(("SWITCH BOX\nDISCONNECTED", "Helvetica", 60, "red"))

        if flow_meter_disconnected:
            active_warnings.append(("☠ FLOW METER ☠\nDISCONNECTED", "Helvetica", 60, "red"))

        if actual > requested_gallons + config.WARNING_THRESHOLD:
            active_warnings.append(("OVER TARGET!", "Helvetica", 72, "red"))

        # Display warnings - cycle through them if multiple exist
        if active_warnings:
            # Flash on/off at 2Hz (on for 0.5s, off for 0.5s)
            if int(time.time() * 2) % 2 == 0:
                # If multiple warnings, cycle through them every 3 seconds
                if len(active_warnings) > 1:
                    warning_index = int(time.time() / 3) % len(active_warnings)
                else:
                    warning_index = 0

                text, font_family, font_size, color = active_warnings[warning_index]
                canvas.create_text(canvas.winfo_width() // 2, int(canvas.winfo_height() * 0.88),
                                 text=text, font=(font_family, font_size, "bold"), fill=color, tags="warning")

    root.after(config.UPDATE_INTERVAL, update_dashboard)

# Initialize GPIO and IO-Link and start serial listener
gpio_ok = initialize_gpio()
iol_ok = initialize_iol()

if gpio_ok:
    print(f"Starting dashboard (GPIO: OK, IOL: {'OK' if iol_ok else 'FAILED - display frozen'})")
else:
    print(f"Starting dashboard (GPIO: FAILED - no relay control, IOL: {'OK' if iol_ok else 'FAILED - display frozen'})")

# Load totals from files
load_totals()
print(f"Loaded totals - Daily: {daily_total:.2f}, Season: {season_total:.2f}")

# Load mode presets and set initial requested gallons
load_mode_presets()
if current_mode == 'fill':
    requested_gallons = fill_requested_gallons
else:
    requested_gallons = mix_requested_gallons
    # Show mode indicator if starting in mix mode
    if mode_indicator_label:
        mode_indicator_label.place(relx=0.02, rely=0.02, anchor="nw")
print(f"Loaded mode - Mode: {current_mode}, Requested: {requested_gallons}, Fill preset: {fill_requested_gallons}, Mix preset: {mix_requested_gallons}")

# Redraw requested gallons with the loaded value
draw_requested_number(f"{requested_gallons:.0f}", "red")

# Start serial listener in background thread (works without IOL)
serial_thread = threading.Thread(target=serial_listener, daemon=True)
serial_thread.start()

# Start socket command listener in background thread (for BLE server communication)
socket_thread = threading.Thread(target=socket_command_listener, daemon=True)
socket_thread.start()

# Start green button monitor in background thread
green_button_thread = threading.Thread(target=green_button_monitor, daemon=True)
green_button_thread.start()

def daily_total_checker():
    """Background thread to check time and reset daily total at 1:00 AM"""
    global last_reset_date
    import datetime

    while True:
        try:
            current_time = datetime.datetime.now()
            current_hour = current_time.hour
            current_minute = current_time.minute
            current_date = current_time.strftime('%Y-%m-%d')

            # Check if it's 1:00 AM and we haven't reset today
            if current_hour == 1 and current_minute == 0:
                if current_date != last_reset_date:
                    print(f"It's 1:00 AM - resetting daily total for {current_date}")
                    reset_daily_total()
                # Sleep for 61 seconds to avoid re-triggering during the same minute
                time.sleep(61)
            else:
                # Check every 30 seconds
                time.sleep(30)
        except Exception as e:
            print(f"Error in daily_total_checker: {e}")
            time.sleep(60)

def daily_reminder_checker():
    """Background thread to check time and show reminders at 2 AM daily"""
    global last_reminder_date, reminders_mode

    while True:
        try:
            current_time = time.localtime()
            current_hour = current_time.tm_hour
            current_minute = current_time.tm_minute
            current_date = time.strftime('%Y-%m-%d')

            # Check if it's 2 AM and we haven't shown reminders today
            if current_hour == 2 and current_minute == 0:
                if current_date != last_reminder_date and not reminders_mode:
                    print(f"It's 2 AM - showing daily reminders for {current_date}")
                    root.after(0, show_daily_reminders)
                # Sleep for 61 seconds to avoid re-triggering during the same minute
                time.sleep(61)
            else:
                # Check every 30 seconds
                time.sleep(30)
        except Exception as e:
            print(f"Error in daily_reminder_checker: {e}")
            time.sleep(60)

# Start daily total checker thread
total_checker_thread = threading.Thread(target=daily_total_checker, daemon=True)
total_checker_thread.start()

# Start daily reminder checker thread
reminder_thread = threading.Thread(target=daily_reminder_checker, daemon=True)
reminder_thread.start()

update_dashboard()

try:
    root.mainloop()
finally:
    # Cleanup GPIO on exit
    if GPIO_AVAILABLE:
        GPIO.cleanup()
