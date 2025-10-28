#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk
import time
import sys
import struct
import socket
import serial
import threading

# Add /home/user to path for RPi.GPIO wrapper on Pi 5
sys.path.insert(0, "/home/user")

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("WARNING: RPi.GPIO not available, relay control disabled")

# Add IOL-HAT examples to path
sys.path.insert(0, "/home/user/iol-hat/examples/python")
import iolhat

# Configuration
REQUESTED_GALLONS = 50  # Default, will be updated via serial
WARNING_THRESHOLD = 2
UPDATE_INTERVAL = 200  # milliseconds
IOL_PORT = 1  # Port 2 in the system (Port 1 in code - 0-indexed)
DATA_LENGTH = 15
SERIAL_PORT = "/dev/ttyAMA0"  # Primary UART on GPIO 14/15 (pins 8/10)
SERIAL_BAUD = 115200
PUMP_STOP_RELAY_PIN = 27  # GPIO pin 27 for pump stop/alert relay
GREEN_BUTTON_PIN = 17  # GPIO pin 17 for green button (active low with pull-up)
PUMP_STOP_DURATION = 15  # seconds for PS command
AUTO_ALERT_DURATION = 10  # seconds for auto-alert

# Flow-based shutoff curve coefficients (based on calibration data)
# Coast distance (gallons) = FLOW_CURVE_SLOPE * flow_rate_gpm + FLOW_CURVE_INTERCEPT
# Adjusted: 22 GPM→0.45gal coast, 70 GPM→1.75gal coast
# (Shut off 0.05 gal later at low flow, 0.05 gal sooner at high flow)
FLOW_CURVE_SLOPE = 0.0270833333
FLOW_CURVE_INTERCEPT = -0.14583333

# Conversion factors
LITERS_TO_GALLONS = 0.264172
LITERS_PER_SEC_TO_GPM = 15.850323  # L/s to GPM conversion (60 * 0.264172)

# Global variables
last_totalizer_liters = 0.0
last_flow_rate = 0.0
connection_error = False
error_message = ""
requested_gallons = REQUESTED_GALLONS
serial_connected = False
override_mode = False
last_alert_triggered = False
last_successful_read_time = time.time()
FLOW_STOPPED_THRESHOLD = 0.001  # L/s - flow is considered stopped below this
FLOW_METER_TIMEOUT = 5  # seconds - flow meter considered disconnected after this
menu_mode = False  # Track if we're in menu mode
menu_window = None  # Reference to menu window
menu_selected_index = 0  # Currently selected menu item (0=logs, 1=self-test, 2=update, 3=shutdown, 4=reboot, 5=exit)
menu_buttons = []  # List of menu button widgets
menu_arrows = []  # List of arrow label widgets
log_viewer_mode = False  # Track if we're in log viewer
log_viewer_window = None  # Reference to log viewer window
log_viewer_text = None  # Reference to log text widget
self_test_mode = False  # Track if we're in self-test
self_test_window = None  # Reference to self-test window
update_mode = False  # Track if we're in update screen
update_window = None  # Reference to update window
serial_command_received = False  # Track if any serial command has been received (for color change)

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
    flow_rate_gpm = flow_rate_l_per_s * LITERS_PER_SEC_TO_GPM

    # Calculate predicted coast distance using calibration curve
    # coast_distance = 0.0270833333 * flow_rate_gpm - 0.14583333
    predicted_coast = FLOW_CURVE_SLOPE * flow_rate_gpm + FLOW_CURVE_INTERCEPT

    # Ensure we don't have negative threshold (minimum 0.1 gallon before target)
    threshold = max(predicted_coast, 0.1)

    return threshold

def pump_stop_relay(duration=PUMP_STOP_DURATION):
    """Activate pump stop relay for specified duration"""
    if not GPIO_AVAILABLE:
        print("GPIO not available, cannot control relay")
        return

    try:
        GPIO.output(PUMP_STOP_RELAY_PIN, GPIO.HIGH)
        print(f"Alert relay (GPIO {PUMP_STOP_RELAY_PIN}) activated for {duration} seconds")
        time.sleep(duration)
        GPIO.output(PUMP_STOP_RELAY_PIN, GPIO.LOW)
        print(f"Alert relay (GPIO {PUMP_STOP_RELAY_PIN}) deactivated")
    except Exception as e:
        print(f"Error controlling relay: {e}")

def change_colors_to_green():
    """Change display colors from red to green if within 2 gallons of target"""
    global serial_command_received, last_totalizer_liters, requested_gallons

    # Calculate current actual gallons
    actual_gallons = last_totalizer_liters * LITERS_TO_GALLONS

    # Check if within 2 gallons of target
    if abs(actual_gallons - requested_gallons) <= 2.0:
        if not serial_command_received:
            serial_command_received = True
            # Change the number labels to green
            requested_number_label.config(foreground="green")
            actual_label.config(foreground="green")
            print(f"Display colors changed to green (within 2 gallons: {actual_gallons:.1f}/{requested_gallons:.0f})")
    else:
        print(f"Cannot change to green: not within 2 gallons ({actual_gallons:.1f}/{requested_gallons:.0f})")

def green_button_monitor():
    """Monitor GPIO pin for green button press (active low with pull-up)"""
    if not GPIO_AVAILABLE:
        print("GPIO not available, green button monitoring disabled")
        return

    try:
        # Set up green button pin as input with pull-up resistor
        GPIO.setup(GREEN_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        print(f"Green button monitor started on GPIO {GREEN_BUTTON_PIN}")

        last_button_state = GPIO.HIGH

        while True:
            # Read current button state
            current_state = GPIO.input(GREEN_BUTTON_PIN)

            # Detect button press (transition from HIGH to LOW)
            if last_button_state == GPIO.HIGH and current_state == GPIO.LOW:
                print("Green button pressed!")
                # Call color change function in main thread
                root.after(0, change_colors_to_green)
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
                       font=("Helvetica", 22, "bold"), fg="yellow", bg="black")
    controls.pack(pady=5)

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

    # Load logs IMMEDIATELY using fast tail command
    try:
        import subprocess

        # Get last 10 lines from system log (faster with fewer lines)
        log_viewer_text.insert(tk.END, "=== SYSTEM LOG ===\n\n")
        result = subprocess.run(['tail', '-10', '/home/pi/iol_dashboard.log'],
                              capture_output=True, text=True, timeout=1)
        log_viewer_text.insert(tk.END, result.stdout)

        # Get last 10 lines from serial log
        log_viewer_text.insert(tk.END, '\n\n=== SERIAL LOG ===\n\n')
        result = subprocess.run(['tail', '-10', '/home/user/serial_debug.log'],
                              capture_output=True, text=True, timeout=1)
        log_viewer_text.insert(tk.END, result.stdout)

        # Scroll to bottom
        log_viewer_text.see(tk.END)
    except Exception as e:
        log_viewer_text.insert(tk.END, f"ERROR loading logs:\n{e}")

    # Make read-only but keep enabled for scrolling
    log_viewer_text.config(state=tk.NORMAL)

def log_viewer_scroll_down():
    """Scroll log viewer down"""
    global log_viewer_text
    if log_viewer_text:
        log_viewer_text.yview_scroll(3, "units")  # Scroll down 3 lines

def log_viewer_scroll_up():
    """Scroll log viewer up"""
    global log_viewer_text
    if log_viewer_text:
        log_viewer_text.yview_scroll(-3, "units")  # Scroll up 3 lines

def close_log_viewer():
    """Close log viewer and return to menu"""
    global log_viewer_mode, log_viewer_window, log_viewer_text
    log_viewer_mode = False
    if log_viewer_window:
        log_viewer_window.destroy()
        log_viewer_window = None
        log_viewer_text = None

def run_self_test():
    """Run system self-test"""
    global self_test_mode, self_test_window

    self_test_mode = True
    self_test_window = tk.Toplevel()
    self_test_window.title("System Self-Test")
    self_test_window.attributes('-fullscreen', True)
    self_test_window.configure(bg='black')

    # Title
    title = tk.Label(self_test_window, text="SYSTEM SELF-TEST", font=("Helvetica", 36, "bold"),
                     fg="cyan", bg="black")
    title.pack(pady=20)

    # Controls instruction
    controls = tk.Label(self_test_window, text="OV=EXIT TO MENU",
                       font=("Helvetica", 22, "bold"), fg="yellow", bg="black")
    controls.pack(pady=5)

    # Results frame
    results_frame = tk.Frame(self_test_window, bg='black')
    results_frame.pack(fill=tk.BOTH, expand=True, padx=40, pady=20)

    results_text = tk.Text(results_frame, font=("Courier", 18), bg="black", fg="white",
                           height=20, width=60)
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
                GPIO.output(PUMP_STOP_RELAY_PIN, GPIO.HIGH)
                time.sleep(0.5)
                GPIO.output(PUMP_STOP_RELAY_PIN, GPIO.LOW)
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
            ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
            ser.close()
            results_text.insert(tk.END, "PASS\n", "pass")
        except Exception as e:
            results_text.insert(tk.END, f"FAIL ({e})\n", "fail")
        results_text.update()

        # Test 3: IOL-HAT
        results_text.insert(tk.END, "3. IOL-HAT Communication: ")
        results_text.update()
        try:
            raw_data = iolhat.pd(IOL_PORT, 0, DATA_LENGTH, None)
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
        results_text.tag_config("pass", foreground="green")
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
            except:
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
                       font=("Helvetica", 22, "bold"), fg="yellow", bg="black")
    controls.pack(pady=5)

    # Status text
    status_frame = tk.Frame(update_window, bg='black')
    status_frame.pack(fill=tk.BOTH, expand=True, padx=40, pady=20)

    status_text = tk.Text(status_frame, font=("Courier", 16, "bold"), bg="black", fg="lime",
                         wrap=tk.WORD)
    status_text.pack(fill=tk.BOTH, expand=True)

    def run_update():
        import subprocess
        status_text.insert(tk.END, "Starting system update...\n\n")
        status_text.update()

        try:
            # Step 1: apt update
            status_text.insert(tk.END, "=== Step 1: Updating package list ===\n")
            status_text.update()
            result = subprocess.run(['sudo', 'apt', 'update'],
                                  capture_output=True, text=True, timeout=300)
            status_text.insert(tk.END, result.stdout)
            if result.stderr:
                status_text.insert(tk.END, "STDERR: " + result.stderr + "\n")
            status_text.insert(tk.END, f"Return code: {result.returncode}\n\n")
            status_text.update()

            if result.returncode != 0:
                status_text.insert(tk.END, "ERROR: apt update failed!\n")
                status_text.insert(tk.END, "Press OV to return to menu\n")
                return

            # Step 2: apt upgrade
            status_text.insert(tk.END, "=== Step 2: Upgrading packages ===\n")
            status_text.insert(tk.END, "This may take several minutes...\n\n")
            status_text.update()

            # Use DEBIAN_FRONTEND=noninteractive to avoid prompts
            env = {'DEBIAN_FRONTEND': 'noninteractive'}
            result = subprocess.run(['sudo', '-E', 'apt', 'upgrade', '-y'],
                                  capture_output=True, text=True, timeout=900, env=env)
            status_text.insert(tk.END, result.stdout)
            if result.stderr:
                status_text.insert(tk.END, "STDERR: " + result.stderr + "\n")
            status_text.insert(tk.END, f"Return code: {result.returncode}\n\n")
            status_text.update()

            if result.returncode == 0:
                status_text.insert(tk.END, "\n=== UPDATE COMPLETE ===\n")
            else:
                status_text.insert(tk.END, "\n=== UPDATE FAILED ===\n")
            status_text.insert(tk.END, "Press OV to return to menu\n")
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
        subprocess.run(['sudo', 'shutdown', '-h', 'now'])

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
        subprocess.run(['sudo', 'reboot'])

    reboot_thread = threading.Thread(target=do_reboot, daemon=True)
    reboot_thread.start()

def update_menu_highlight():
    """Update visual highlighting of selected menu item"""
    global menu_buttons, menu_arrows, menu_selected_index

    if not menu_buttons or not menu_arrows:
        return

    # Default colors for each button
    colors = [
        ("blue", "white"),       # Logs
        ("green", "white"),      # Self Test
        ("purple", "white"),     # Update
        ("red", "white"),        # Shutdown
        ("orange", "white"),     # Reboot
        ("gray30", "white")      # Exit
    ]

    for i, (btn, arrow) in enumerate(zip(menu_buttons, menu_arrows)):
        if i == menu_selected_index:
            # SELECTED - Make it very obvious
            btn.config(bg="white", fg="black",
                      font=("Helvetica", 32, "bold"),
                      relief=tk.RAISED, borderwidth=6,
                      width=18, height=1)
            arrow.config(text=">>> SELECTED >>>", fg="yellow",
                        font=("Helvetica", 20, "bold"))
        else:
            # Unselected - Dim it
            btn.config(bg="gray20", fg="gray50",
                      font=("Helvetica", 24),
                      relief=tk.FLAT, borderwidth=2,
                      width=18, height=1)
            arrow.config(text="", fg="black")

def menu_navigate_up():
    """Move selection up in menu"""
    global menu_selected_index
    menu_selected_index = (menu_selected_index - 1) % 6  # Wrap around - 6 menu items
    update_menu_highlight()

def menu_navigate_down():
    """Move selection down in menu"""
    global menu_selected_index
    menu_selected_index = (menu_selected_index + 1) % 6  # Wrap around - 6 menu items
    update_menu_highlight()

def menu_select():
    """Activate the currently selected menu item"""
    global menu_selected_index

    if menu_selected_index == 0:
        show_log_viewer()
    elif menu_selected_index == 1:
        run_self_test()
    elif menu_selected_index == 2:
        run_system_update()
    elif menu_selected_index == 3:
        shutdown_system()
    elif menu_selected_index == 4:
        reboot_system()
    elif menu_selected_index == 5:
        close_menu()

def close_menu():
    """Close the menu and return to main dashboard"""
    global menu_mode, menu_window, menu_buttons, menu_arrows, menu_selected_index
    menu_mode = False
    menu_selected_index = 0
    menu_buttons = []
    menu_arrows = []
    if menu_window:
        menu_window.destroy()
        menu_window = None

def show_menu():
    """Display the main menu"""
    global menu_mode, menu_window, menu_buttons, menu_arrows, menu_selected_index

    menu_mode = True
    menu_selected_index = 0  # Start at first item
    menu_buttons = []
    menu_arrows = []

    menu_window = tk.Toplevel(root)
    menu_window.title("System Menu")
    menu_window.attributes('-fullscreen', True)
    menu_window.configure(bg='black')

    # Title
    title = tk.Label(menu_window, text="SYSTEM MENU", font=("Helvetica", 40, "bold"),
                     fg="cyan", bg="black")
    title.pack(pady=20)

    # WiFi status indicator (top right corner)
    wifi_status = check_wifi_status()
    if "CONNECTED" in wifi_status:
        wifi_color = "green"
        wifi_symbol = "✓"
    else:
        wifi_color = "red"
        wifi_symbol = "✗"

    wifi_frame = tk.Frame(menu_window, bg='black')
    wifi_frame.place(x=10, y=10)  # Top left corner

    wifi_label = tk.Label(wifi_frame, text=f"{wifi_symbol} {wifi_status}",
                         font=("Helvetica", 18, "bold"), fg=wifi_color, bg="black")
    wifi_label.pack()

    # Position indicator
    position = tk.Label(menu_window, text="Option 1 of 6", font=("Helvetica", 20),
                       fg="white", bg="black")
    position.pack(pady=5)

    # Menu buttons frame
    button_frame = tk.Frame(menu_window, bg='black')
    button_frame.pack(expand=True, pady=10)

    # View Logs button with arrow
    logs_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                         fg="yellow", bg="black")
    logs_arrow.pack()
    logs_btn = tk.Button(button_frame, text="VIEW LOGS", font=("Helvetica", 26, "bold"),
                         bg="blue", fg="white", command=show_log_viewer,
                         width=18, height=1, borderwidth=2)
    logs_btn.pack(pady=5)
    menu_buttons.append(logs_btn)
    menu_arrows.append(logs_arrow)

    # Self Test button with arrow
    test_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                         fg="yellow", bg="black")
    test_arrow.pack()
    test_btn = tk.Button(button_frame, text="SELF TEST", font=("Helvetica", 26, "bold"),
                         bg="green", fg="white", command=run_self_test,
                         width=18, height=1, borderwidth=2)
    test_btn.pack(pady=5)
    menu_buttons.append(test_btn)
    menu_arrows.append(test_arrow)

    # Update button with arrow
    update_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                           fg="yellow", bg="black")
    update_arrow.pack()
    update_btn = tk.Button(button_frame, text="SYSTEM UPDATE", font=("Helvetica", 26, "bold"),
                          bg="purple", fg="white", command=run_system_update,
                          width=18, height=1, borderwidth=2)
    update_btn.pack(pady=5)
    menu_buttons.append(update_btn)
    menu_arrows.append(update_arrow)

    # Shutdown button with arrow
    shutdown_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                             fg="yellow", bg="black")
    shutdown_arrow.pack()
    shutdown_btn = tk.Button(button_frame, text="SHUTDOWN", font=("Helvetica", 26, "bold"),
                            bg="red", fg="white", command=shutdown_system,
                            width=18, height=1, borderwidth=2)
    shutdown_btn.pack(pady=5)
    menu_buttons.append(shutdown_btn)
    menu_arrows.append(shutdown_arrow)

    # Reboot button with arrow
    reboot_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                           fg="yellow", bg="black")
    reboot_arrow.pack()
    reboot_btn = tk.Button(button_frame, text="REBOOT", font=("Helvetica", 26, "bold"),
                          bg="orange", fg="white", command=reboot_system,
                          width=18, height=1, borderwidth=2)
    reboot_btn.pack(pady=5)
    menu_buttons.append(reboot_btn)
    menu_arrows.append(reboot_arrow)

    # Exit button with arrow
    exit_arrow = tk.Label(button_frame, text="", font=("Helvetica", 20, "bold"),
                         fg="yellow", bg="black")
    exit_arrow.pack()
    exit_btn = tk.Button(button_frame, text="EXIT MENU", font=("Helvetica", 26, "bold"),
                         bg="gray30", fg="white", command=close_menu,
                         width=18, height=1, borderwidth=2)
    exit_btn.pack(pady=5)
    menu_buttons.append(exit_btn)
    menu_arrows.append(exit_arrow)

    # Instructions
    instructions = tk.Label(menu_window, text="+1=DOWN  -1=UP  OV=SELECT",
                           font=("Helvetica", 22, "bold"), fg="cyan", bg="black")
    instructions.pack(side=tk.BOTTOM, pady=15)

    # Apply initial highlight
    update_menu_highlight()

def read_flow_meter():
    """Read data from the Picomag flow meter via IO-Link"""
    global last_totalizer_liters, last_flow_rate, connection_error, error_message, last_successful_read_time

    try:
        # Read process data from IO-Link device
        raw_data = iolhat.pd(IOL_PORT, 0, DATA_LENGTH, None)

        if len(raw_data) >= 15:
            # Check if data is all zeros (indicates IO-Link timeout/no response)
            if raw_data == b'\x00' * len(raw_data):
                connection_error = True
                error_message = "Device not responding (all-zero data)"
                return last_totalizer_liters * LITERS_TO_GALLONS

            # Decode the data according to Picomag format
            totalizer_liters = struct.unpack('>f', raw_data[4:8])[0]
            flow_rate_l_per_s = struct.unpack('>f', raw_data[8:12])[0]

            last_totalizer_liters = totalizer_liters
            last_flow_rate = flow_rate_l_per_s
            connection_error = False
            error_message = ""
            last_successful_read_time = time.time()

            return totalizer_liters * LITERS_TO_GALLONS
        else:
            connection_error = True
            error_message = "Invalid data length"
            return last_totalizer_liters * LITERS_TO_GALLONS

    except Exception as e:
        connection_error = True
        error_message = str(e)
        return last_totalizer_liters * LITERS_TO_GALLONS

def serial_listener():
    """Listen for serial messages with format: requested,actual"""
    global requested_gallons, serial_connected, override_mode

    debug_log = "/home/user/serial_debug.log"
    buffer = ""

    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0.5)
        ser.reset_input_buffer()
        serial_connected = True
        msg = f"Serial listener started on {SERIAL_PORT} at {SERIAL_BAUD} baud"
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
                            # Handle log viewer navigation if in log viewer mode
                            if log_viewer_mode:
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
                                        msg = f"Serial: Override mode {'ENABLED' if override_mode else 'DISABLED'}"
                                        print(msg)
                                        with open(debug_log, 'a') as f:
                                            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")

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
        # Clean up any previous GPIO state
        GPIO.cleanup()
        # Configure GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(PUMP_STOP_RELAY_PIN, GPIO.OUT)
        GPIO.output(PUMP_STOP_RELAY_PIN, GPIO.LOW)
        print(f"GPIO initialized: Relay on pin {PUMP_STOP_RELAY_PIN}")
        return True
    except Exception as e:
        print(f"Failed to initialize GPIO: {e}")
        return False

def initialize_iol():
    """Initialize IO-Link port"""
    try:
        # Power on the port
        iolhat.power(IOL_PORT, 1)
        # Set LED to green
        iolhat.led(IOL_PORT, iolhat.LED_GREEN)
        time.sleep(0.5)
        print(f"IO-Link Port {IOL_PORT+1} initialized successfully")
        return True
    except Exception as e:
        print(f"Failed to initialize IO-Link Port {IOL_PORT+1}: {e}")
        return False

# Tkinter GUI setup
root = tk.Tk()
root.title("Tank Dashboard")
root.configure(bg="black")
root.attributes("-fullscreen", True)

# Requested Gallons - split into label and number
requested_text_label = ttk.Label(root, text="Requested Gallons:",
                                font=("Helvetica", 36, "bold"), foreground="white", background="black")
requested_text_label.pack(pady=5)

requested_number_label = ttk.Label(root, text=f"{REQUESTED_GALLONS:.0f}",
                                  font=("Helvetica", 120, "bold"), foreground="red", background="black")
requested_number_label.pack(pady=5)

# Actual Gallons Label (very large, center)
actual_text_label = ttk.Label(root, text="Actual Gallons:", font=("Helvetica", 36, "bold"),
                              foreground="white", background="black")
actual_text_label.pack(pady=5)

actual_label = ttk.Label(root, text="0.0", font=("Helvetica", 240, "bold"),
                         foreground="red", background="black")
actual_label.pack(pady=10)

# Status Label (for connection errors)
status_label = ttk.Label(root, text="", font=("Helvetica", 24),
                        foreground="yellow", background="black")
status_label.pack(pady=5)

# Flow Meter Disconnected Warning (flashing)
flowmeter_disconnected_label = ttk.Label(root, text="FLOW METER\nDISCONNECTED",
                                         font=("Helvetica", 60, "bold"),
                                         foreground="red", background="black")
flowmeter_disconnected_label.pack(pady=5)
flowmeter_disconnected_label.pack_forget()

# Warning Label (flashing)
warning_label = ttk.Label(root, text="OVER TARGET!", font=("Helvetica", 72, "bold"),
                          foreground="red", background="black")
warning_label.pack(pady=5)
warning_label.pack_forget()

# Manual Mode Label (flashing when override is active)
manual_label = ttk.Label(root, text="MANUAL", font=("Helvetica", 90, "bold"),
                         foreground="orange", background="black")
manual_label.pack(pady=5)
manual_label.pack_forget()

def update_dashboard():
    """Update the dashboard with current flow meter readings"""
    global last_alert_triggered, override_mode

    actual = read_flow_meter()
    actual_label.config(text=f"{actual:.1f}")

    # Update requested gallons number
    requested_number_label.config(text=f"{requested_gallons:.0f}")

    # Check if flow meter has timed out (no successful reads in X seconds)
    flow_meter_disconnected = (time.time() - last_successful_read_time) > FLOW_METER_TIMEOUT

    # Auto-disable override when flow stops
    if override_mode and last_flow_rate < FLOW_STOPPED_THRESHOLD:
        override_mode = False
        print(f"Flow stopped ({last_flow_rate:.6f} L/s < {FLOW_STOPPED_THRESHOLD} L/s), auto-disabling override mode")

    # Calculate dynamic trigger threshold based on current flow rate
    flow_rate_gpm = last_flow_rate * LITERS_PER_SEC_TO_GPM
    trigger_threshold = calculate_trigger_threshold(last_flow_rate)

    # Auto-alert: Trigger GPIO 27 based on flow-adjusted threshold (once per cycle)
    # Only if override mode is OFF and flow meter is connected
    if not override_mode and not flow_meter_disconnected and actual >= requested_gallons - trigger_threshold and not last_alert_triggered:
        last_alert_triggered = True
        print(f"Auto-alert: Flow={flow_rate_gpm:.1f} GPM, threshold={trigger_threshold:.2f}gal, triggering relay for {AUTO_ALERT_DURATION}s")
        relay_thread = threading.Thread(target=pump_stop_relay, args=(AUTO_ALERT_DURATION,), daemon=True)
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
        status_parts.append("Serial: Listening")
    else:
        status_parts.append("Serial: Disconnected")

    if override_mode:
        status_parts.append("OVERRIDE: ON")

    status_label.config(text=" | ".join(status_parts))

    # Priority 1: Flow Meter Disconnected (highest priority - flashing)
    if flow_meter_disconnected:
        if int(time.time() * 2) % 2 == 0:
            flowmeter_disconnected_label.pack(pady=20)
        else:
            flowmeter_disconnected_label.pack_forget()
        manual_label.pack_forget()
        warning_label.pack_forget()

    # Priority 2: Manual Mode (when override is ON)
    elif override_mode:
        flowmeter_disconnected_label.pack_forget()
        if int(time.time() * 2) % 2 == 0:
            manual_label.pack(pady=20)
        else:
            manual_label.pack_forget()
        warning_label.pack_forget()  # Hide over target warning in manual mode

    # Priority 3: Over Target Warning (normal operation)
    else:
        flowmeter_disconnected_label.pack_forget()
        manual_label.pack_forget()  # Hide manual label when not in override

        # Flash warning if over target (only if override mode is OFF)
        if actual > requested_gallons + WARNING_THRESHOLD:
            if int(time.time() * 2) % 2 == 0:
                warning_label.pack(pady=20)
            else:
                warning_label.pack_forget()
        else:
            warning_label.pack_forget()

    root.after(UPDATE_INTERVAL, update_dashboard)

# Initialize GPIO and IO-Link and start serial listener
gpio_ok = initialize_gpio()
iol_ok = initialize_iol()

if gpio_ok:
    print(f"Starting dashboard (GPIO: OK, IOL: {'OK' if iol_ok else 'FAILED - display frozen'})")
else:
    print(f"Starting dashboard (GPIO: FAILED - no relay control, IOL: {'OK' if iol_ok else 'FAILED - display frozen'})")

# Start serial listener in background thread (works without IOL)
serial_thread = threading.Thread(target=serial_listener, daemon=True)
serial_thread.start()

# Start green button monitor in background thread
green_button_thread = threading.Thread(target=green_button_monitor, daemon=True)
green_button_thread.start()

update_dashboard()

try:
    root.mainloop()
finally:
    # Cleanup GPIO on exit
    if GPIO_AVAILABLE:
        GPIO.cleanup()
