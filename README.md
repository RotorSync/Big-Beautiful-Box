# IOL Dashboard - Flow Meter Monitoring and Pump Control System

A Raspberry Pi-based flow meter monitoring and pump control system with real-time display, serial command interface, and intelligent shutoff control.

## Overview

This system provides:
- Real-time flow meter data display via IO-Link (Picomag flow meter)
- Serial command interface (RS485) for remote control
- Relay control for pump stop/alert (GPIO 27)
- Fullscreen Tkinter GUI dashboard
- Flow-rate-based dynamic shutoff timing
- Fault-tolerant operation (runs even without flow meter connected)

## Hardware Requirements

### Raspberry Pi
- **Recommended**: Raspberry Pi 5 with Ubuntu OS
- **Also tested**: Raspberry Pi 4 with Raspberry Pi OS
- 7-inch HDMI display (1024x600 resolution)

### Hardware Connections
- **GPIO 27**: Pump stop/alert relay (BCM mode)
- **GPIO 14/15 (Pins 8/10)**: RS485 serial interface (/dev/ttyAMA0, 115200 baud)
- **SPI**: IO-Link HAT communication
- **IOL-HAT Port 2**: Picomag flow meter (Vendor ID 310, Device ID 262149)

## Features

### Flow-Rate-Based Shutoff Curve
Dynamically adjusts relay trigger point based on current flow rate to compensate for flow-dependent overshoot.

**Formula**: `threshold = 0.025 × GPM - 0.06`

**Calibration Data**:
- 22 GPM → 0.5 gallon coast
- 45 GPM → 1.04 gallon coast
- 70 GPM → 1.7 gallon coast

### Flow Meter Disconnection Detection
- Detects all-zero data from IOL-HAT (indicates timeout/no response)
- Shows "IOL: Device not responding" in status bar
- After 5 seconds: Large flashing "FLOW METER DISCONNECTED" warning
- Display freezes at last known good value

### Serial Command Interface

Supported commands (115200 baud, RS485):
- `PS` - Pump Stop (activates relay for 15 seconds)
- `OV` - Override mode toggle (enables/disables auto-alert)
- `+1` / `-1` - Adjust requested gallons by 1
- `+10` / `-10` - Adjust requested gallons by 10

## Installation

### Quick Install (Recommended)

The easiest way to install the IOL Dashboard is using the automated installation script:

```bash
# Clone repository
git clone https://github.com/austins05/Big-Beautiful-Box.git
cd Big-Beautiful-Box

# Run installation script
chmod +x install.sh
./install.sh
```

The installation script will automatically:
- Update system packages
- Install all Python dependencies (tkinter, serial, lgpio)
- Configure HDMI for 7-inch display (1024x600 resolution)
- Configure UART on GPIO pins 14/15 for RS485 serial
- Enable auto-login for the current user
- Disable screen blanking and idle timeout (systemd logind)
- Configure GNOME power management to prevent display timeout
- Install and enable the systemd service
- Prompt for reboot when complete

**Note**: The script will prompt for sudo password when needed and ask for confirmation before modifying system files.

### Manual Installation

If you prefer to install manually or need to customize the installation:

#### Prerequisites

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python dependencies
sudo apt install -y python3-tk python3-serial python3-lgpio git build-essential cmake

# Install IOL-HAT software (if using IO-Link master)
# Clone and build from your IOL-HAT repository
```

### Raspberry Pi 5 Specific Configuration

#### Enable UART on GPIO Pins
Edit `/boot/firmware/config.txt` and add:
```ini
# Enable UART on GPIO 14/15 (pins 8/10)
dtparam=uart0=on
enable_uart=1
```

#### Configure HDMI for 7" Display
Edit `/boot/firmware/config.txt` and add:
```ini
# Force HDMI output for 7-inch display
hdmi_force_hotplug=1
hdmi_drive=2
hdmi_group=2
hdmi_mode=87
hdmi_cvt=1024 600 60 6 0 0 0
```

#### Enable Auto-login (Optional)
Edit `/etc/gdm3/custom.conf`:
```ini
[daemon]
WaylandEnable=false
AutomaticLoginEnable = true
AutomaticLogin = user
```

### Install Dashboard

```bash
# Clone repository
git clone <your-repo-url> /home/user/iol-dashboard
cd /home/user/iol-dashboard

# Make scripts executable
chmod +x start_iol_dashboard.sh

# Install systemd service
sudo cp iol_dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable iol_dashboard.service

# Start service
sudo systemctl start iol_dashboard.service

# Check status
systemctl status iol_dashboard.service
```

## File Structure

```
Big-Beautiful-Box/
├── install.sh                      # Automated installation script
├── dashboard.py                    # Main Python application
├── start_iol_dashboard.sh          # Startup script for systemd
├── iol_dashboard.service           # Systemd service configuration
├── RPi/
│   ├── __init__.py
│   └── GPIO.py                     # GPIO compatibility wrapper for Pi 5 (lgpio)
├── .gitignore                      # Git ignore file
└── README.md                       # This file
```

## Configuration

Edit constants in `dashboard.py`:

```python
REQUESTED_GALLONS = 10              # Target fill amount
PUMP_STOP_DURATION = 15             # PS command relay duration (seconds)
AUTO_ALERT_DURATION = 10            # Auto-alert relay duration (seconds)
SERIAL_BAUD = 115200                # RS485 serial baud rate
FLOW_METER_TIMEOUT = 5              # Seconds before "disconnected" warning
UPDATE_INTERVAL = 100               # GUI refresh rate (milliseconds)
FLOW_CURVE_SLOPE = 0.025           # Flow curve calibration
FLOW_CURVE_INTERCEPT = -0.06       # Flow curve calibration
```

## Troubleshooting

### Dashboard not displaying
```bash
# Check service status
systemctl status iol_dashboard.service

# Check logs
tail -50 /home/user/iol_dashboard.log

# Restart service
sudo systemctl restart iol_dashboard.service

# If still not working, reboot (auto-start works better on clean boot)
sudo reboot
```

### Serial commands not working
```bash
# Check serial debug log
tail -f /home/user/serial_debug.log

# Verify UART device exists
ls -l /dev/ttyAMA0

# Check UART configuration in /boot/firmware/config.txt
cat /boot/firmware/config.txt | grep uart

# Make sure no other process is using serial port
fuser /dev/ttyAMA0
```

### IOL-HAT not detected
```bash
# Check logs for chip detection
tail -100 /home/user/iol_dashboard.log | grep -i "chip\|iol"

# Verify SPI enabled
ls -l /dev/spi*

# Power cycle and reseat IOL-HAT
sudo poweroff
# Remove power, reseat IOL-HAT firmly on GPIO pins, restore power
```

### GPIO relay not working
```bash
# Test GPIO manually
python3 << EOF
import sys
sys.path.insert(0, "/home/user")
import RPi.GPIO as GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(27, GPIO.OUT)
GPIO.output(27, GPIO.HIGH)
print("Relay should be ON")
input("Press Enter to turn OFF...")
GPIO.output(27, GPIO.LOW)
GPIO.cleanup()
EOF
```

## Logs

- **Main log**: `/home/user/iol_dashboard.log` - Dashboard and IOL master output
- **Serial debug**: `/home/user/serial_debug.log` - All serial commands with timestamps

## System Status

Currently working:
- Dashboard GUI running stable
- Serial listener active and receiving commands
- PS button working (relay activates for 15 seconds)
- GPIO relay control functional
- Override mode toggle (OV command)
- Gallon adjustment commands
- Zero GPIO conflicts on startup
- Fault-tolerant operation without IOL-HAT

Known issues:
- System requires full reboot after service restart to restore display properly

## Development Notes

### Raspberry Pi 5 GPIO Compatibility
The included `RPi/GPIO.py` is a compatibility wrapper that allows the dashboard to use the familiar RPi.GPIO API while actually using the lgpio library required by Raspberry Pi 5.

### Serial Port Selection
- Pi 5 uses `/dev/ttyAMA0` for GPIO UART (pins 8/10)
- Requires `dtparam=uart0=on` in boot config
- Pi 4 and earlier may use different UART assignments

### IOL-HAT Integration
The dashboard works with or without the IOL-HAT connected:
- With IOL-HAT: Full flow meter monitoring and auto-shutoff
- Without IOL-HAT: Relay control via serial commands still works
- Startup script continues even if IOL master fails to start

## License

[Add your license here]

## Author

Developed for industrial flow meter control and monitoring applications.
