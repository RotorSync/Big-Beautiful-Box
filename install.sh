#!/bin/bash
# IOL Dashboard Installation Script for Raspberry Pi 5 (Ubuntu OS)
# This script automates the complete installation and configuration

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    log_error "Please do not run this script as root. Run as the user account."
    exit 1
fi

# Detect username and home directory
INSTALL_USER=$(whoami)
INSTALL_HOME=$HOME
log_info "Installing for user: $INSTALL_USER"
log_info "Home directory: $INSTALL_HOME"

# Check if running on Ubuntu
if ! grep -q "Ubuntu" /etc/os-release 2>/dev/null; then
    log_warn "This script is designed for Ubuntu OS. Continue anyway? (y/n)"
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo ""
log_info "=========================================="
log_info "IOL Dashboard Installation"
log_info "=========================================="
echo ""
log_info "This script will:"
log_info "  1. Update system packages"
log_info "  2. Install Python dependencies"
log_info "  3. Clone and build IOL-HAT software"
log_info "  4. Configure HDMI for 7-inch display"
log_info "  5. Configure UART for RS485 serial"
log_info "  6. Configure auto-login"
log_info "  7. Disable screen blanking"
log_info "  8. Install systemd service"
echo ""
log_warn "This will modify system configuration files."
log_warn "Continue? (y/n)"
read -r response
if [[ ! "$response" =~ ^[Yy]$ ]]; then
    log_info "Installation cancelled."
    exit 0
fi

# Step 1: Update system
echo ""
log_info "Step 1/8: Updating system packages..."
sudo apt update
sudo apt upgrade -y

# Step 2: Install Python dependencies
echo ""
log_info "Step 2/8: Installing Python dependencies..."
sudo apt install -y \
    python3 \
    python3-tk \
    python3-serial \
    python3-lgpio \
    git \
    build-essential \
    cmake

log_info "Python dependencies installed successfully."

# Step 3: Clone and build IOL-HAT software
echo ""
log_info "Step 3/8: Setting up IOL-HAT software..."

# Clone IOL-HAT repository if it doesn't exist
if [ ! -d "$INSTALL_HOME/iol-hat" ]; then
    log_info "Cloning IOL-HAT repository..."
    cd "$INSTALL_HOME"
    # Note: Replace this URL with the actual IOL-HAT repository URL
    log_warn "IOL-HAT repository URL needed. Skipping clone for now."
    log_warn "You may need to manually clone and build the IOL-HAT software."
    mkdir -p "$INSTALL_HOME/iol-hat/src-master-application/bin/debug"
else
    log_info "IOL-HAT directory already exists."
fi

# Build IOL-HAT master application if source exists
if [ -d "$INSTALL_HOME/iol-hat/src-master-application" ]; then
    log_info "Building IOL-HAT master application..."
    cd "$INSTALL_HOME/iol-hat/src-master-application"
    if [ -f "Makefile" ]; then
        make clean 2>/dev/null || true
        make debug || log_warn "IOL-HAT build failed (may not have hardware connected)"
    else
        log_warn "No Makefile found in IOL-HAT source directory."
    fi
else
    log_warn "IOL-HAT source not found. Dashboard will run without IOL master."
fi

# Step 4: Configure HDMI for 7-inch display
echo ""
log_info "Step 4/8: Configuring HDMI for 7-inch display (1024x600)..."

HDMI_CONFIG="
# Force HDMI output for 7-inch display
hdmi_force_hotplug=1
hdmi_drive=2
hdmi_group=2
hdmi_mode=87
hdmi_cvt=1024 600 60 6 0 0 0"

if ! grep -q "hdmi_force_hotplug=1" /boot/firmware/config.txt 2>/dev/null; then
    log_info "Adding HDMI configuration to /boot/firmware/config.txt..."
    echo "$HDMI_CONFIG" | sudo tee -a /boot/firmware/config.txt > /dev/null
    log_info "HDMI configuration added."
else
    log_info "HDMI configuration already present."
fi

# Step 5: Configure UART for RS485 serial communication
echo ""
log_info "Step 5/8: Configuring UART on GPIO 14/15 (pins 8/10)..."

UART_CONFIG="
# Enable UART on GPIO 14/15 (pins 8/10)
dtparam=uart0=on
enable_uart=1"

if ! grep -q "dtparam=uart0=on" /boot/firmware/config.txt 2>/dev/null; then
    log_info "Adding UART configuration to /boot/firmware/config.txt..."
    echo "$UART_CONFIG" | sudo tee -a /boot/firmware/config.txt > /dev/null
    log_info "UART configuration added."
else
    log_info "UART configuration already present."
fi

# Step 6: Configure auto-login
echo ""
log_info "Step 6/8: Configuring auto-login for user '$INSTALL_USER'..."

if [ -f /etc/gdm3/custom.conf ]; then
    # Backup original config
    if [ ! -f /etc/gdm3/custom.conf.bak ]; then
        sudo cp /etc/gdm3/custom.conf /etc/gdm3/custom.conf.bak
        log_info "Backed up /etc/gdm3/custom.conf"
    fi

    # Configure auto-login
    sudo sed -i 's/^#WaylandEnable=.*/WaylandEnable=false/' /etc/gdm3/custom.conf
    sudo sed -i 's/^#AutomaticLoginEnable =.*/AutomaticLoginEnable = true/' /etc/gdm3/custom.conf
    sudo sed -i 's/^#AutomaticLogin =.*/AutomaticLogin = '"$INSTALL_USER"'/' /etc/gdm3/custom.conf

    # Add settings if they don't exist
    if ! grep -q "WaylandEnable=false" /etc/gdm3/custom.conf; then
        sudo sed -i '/\[daemon\]/a WaylandEnable=false' /etc/gdm3/custom.conf
    fi
    if ! grep -q "AutomaticLoginEnable" /etc/gdm3/custom.conf; then
        sudo sed -i '/\[daemon\]/a AutomaticLoginEnable = true' /etc/gdm3/custom.conf
        sudo sed -i '/AutomaticLoginEnable/a AutomaticLogin = '"$INSTALL_USER" /etc/gdm3/custom.conf
    fi

    log_info "Auto-login configured for $INSTALL_USER."
else
    log_warn "/etc/gdm3/custom.conf not found. Auto-login not configured."
fi

# Step 7: Disable screen blanking and idle timeout
echo ""
log_info "Step 7/8: Disabling screen blanking and idle timeout..."

LOGIND_CONFIG="
# Disable idle timeout and screen blanking
IdleAction=ignore
IdleActionSec=0"

if ! grep -q "IdleAction=ignore" /etc/systemd/logind.conf 2>/dev/null; then
    log_info "Adding idle timeout configuration to /etc/systemd/logind.conf..."
    echo "$LOGIND_CONFIG" | sudo tee -a /etc/systemd/logind.conf > /dev/null
    log_info "Idle timeout disabled."
else
    log_info "Idle timeout configuration already present."
fi

# Step 8: Install systemd service
echo ""
log_info "Step 8/8: Installing systemd service..."

# Create dashboard directory if it doesn't exist
mkdir -p "$INSTALL_HOME/iol-dashboard"

# Copy dashboard files to installation location
if [ -f "dashboard.py" ]; then
    cp dashboard.py "$INSTALL_HOME/iol-dashboard/"
    log_info "Copied dashboard.py"
fi

if [ -f "start_iol_dashboard.sh" ]; then
    cp start_iol_dashboard.sh "$INSTALL_HOME/iol-dashboard/"
    chmod +x "$INSTALL_HOME/iol-dashboard/start_iol_dashboard.sh"
    log_info "Copied start_iol_dashboard.sh"
fi

# Copy RPi GPIO wrapper
mkdir -p "$INSTALL_HOME/iol-dashboard/RPi"
if [ -d "RPi" ]; then
    cp RPi/*.py "$INSTALL_HOME/iol-dashboard/RPi/" 2>/dev/null || true
    log_info "Copied RPi GPIO wrapper"
fi

# Update paths in files to use actual install location
if [ -f "$INSTALL_HOME/iol-dashboard/dashboard.py" ]; then
    sed -i "s|/home/user|$INSTALL_HOME|g" "$INSTALL_HOME/iol-dashboard/dashboard.py"
fi

if [ -f "$INSTALL_HOME/iol-dashboard/start_iol_dashboard.sh" ]; then
    sed -i "s|/home/user|$INSTALL_HOME|g" "$INSTALL_HOME/iol-dashboard/start_iol_dashboard.sh"
fi

# Install systemd service
if [ -f "iol_dashboard.service" ]; then
    # Create service file with correct username and paths
    cat > /tmp/iol_dashboard.service << EOF
[Unit]
Description=IOL Dashboard and Master
After=network.target

[Service]
Type=simple
ExecStart=$INSTALL_HOME/iol-dashboard/start_iol_dashboard.sh
Restart=always
RestartSec=5
User=$INSTALL_USER
Group=$INSTALL_USER
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u)

[Install]
WantedBy=graphical.target
EOF

    sudo cp /tmp/iol_dashboard.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable iol_dashboard.service
    log_info "Systemd service installed and enabled."
else
    log_warn "iol_dashboard.service not found in current directory."
fi

# Installation complete
echo ""
log_info "=========================================="
log_info "Installation Complete!"
log_info "=========================================="
echo ""
log_info "Next steps:"
log_info "  1. Reboot the system: sudo reboot"
log_info "  2. After reboot, the dashboard will start automatically"
log_info "  3. Check service status: systemctl status iol_dashboard.service"
log_info "  4. View logs: tail -f $INSTALL_HOME/iol_dashboard.log"
echo ""
log_warn "IMPORTANT: A reboot is required for all changes to take effect."
log_warn "Reboot now? (y/n)"
read -r response
if [[ "$response" =~ ^[Yy]$ ]]; then
    log_info "Rebooting..."
    sudo reboot
else
    log_info "Please reboot manually when ready: sudo reboot"
fi
