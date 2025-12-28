#!/bin/bash
# IOL Dashboard Installation Script for Raspberry Pi 5
# Big-Beautiful-Box Project

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${CYAN}[STEP]${NC} $1"; }

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    log_error "Do not run as root. Run as the pi user."
    exit 1
fi

INSTALL_USER=$(whoami)
INSTALL_HOME=$HOME
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$INSTALL_HOME/Big-Beautiful-Box"

echo ""
log_info "=========================================="
log_info "IOL Dashboard Installation"
log_info "=========================================="
echo ""
log_info "User: $INSTALL_USER"
log_info "Install directory: $INSTALL_DIR"
echo ""

# Confirm installation
read -p "Continue with installation? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    log_info "Installation cancelled."
    exit 0
fi

# Step 1: Update system
log_step "1/7: Updating system packages..."
sudo apt update
sudo apt upgrade -y

# Step 2: Install dependencies
log_step "2/7: Installing dependencies..."
sudo apt install -y \
    python3 \
    python3-tk \
    python3-serial \
    python3-lgpio \
    python3-spidev \
    python3-pil \
    python3-pil.imagetk \
    git \
    build-essential \
    cmake \
    libgpiod-dev \
    gpiod \
    netcat-openbsd

sudo usermod -a -G dialout $INSTALL_USER

# Step 3: Clone/update IOL-HAT
log_step "3/7: Setting up IOL-HAT..."
if [ ! -d "$INSTALL_HOME/iol-hat" ]; then
    cd "$INSTALL_HOME"
    git clone https://github.com/Pinetek-Networks/iol-hat.git
fi

# Apply Pi 5 fix
if [ -f "$INSTALL_HOME/iol-hat/src-master-application/iol_osal/osal_irq.c" ]; then
    sed -i 's|gpiod_chip_open("/dev/gpiochip4")|gpiod_chip_open("/dev/gpiochip0")|g' \
        "$INSTALL_HOME/iol-hat/src-master-application/iol_osal/osal_irq.c"
    log_info "Applied Pi 5 GPIO fix"
fi

# Build IOL-HAT
if [ -d "$INSTALL_HOME/iol-hat/src-master-application" ]; then
    cd "$INSTALL_HOME/iol-hat/src-master-application"
    make clean 2>/dev/null || true
    make debug || log_warn "IOL-HAT build failed (may need hardware)"
fi

# Step 4: Configure hardware
log_step "4/7: Configuring hardware..."

# SPI
if ! grep -q "dtoverlay=spi0-1cs" /boot/firmware/config.txt 2>/dev/null; then
    sudo sed -i 's/^dtparam=spi=on$/dtoverlay=spi0-1cs/' /boot/firmware/config.txt 2>/dev/null || true
    echo "dtoverlay=spi0-1cs" | sudo tee -a /boot/firmware/config.txt > /dev/null
fi

# UART
if ! grep -q "dtparam=uart0=on" /boot/firmware/config.txt 2>/dev/null; then
    echo -e "\ndtparam=uart0=on\nenable_uart=1" | sudo tee -a /boot/firmware/config.txt > /dev/null
fi

# Step 5: Install dashboard files
log_step "5/7: Installing dashboard files..."

mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/src"
mkdir -p "$INSTALL_DIR/RPi"

# Copy files
cp "$SCRIPT_DIR/dashboard.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/config.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/start_iol_dashboard.sh" "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/src/"* "$INSTALL_DIR/src/"
cp -r "$SCRIPT_DIR/RPi/"* "$INSTALL_DIR/RPi/"

# Copy optional files
[ -f "$SCRIPT_DIR/bbb_diagram_rotated.jpeg" ] && cp "$SCRIPT_DIR/bbb_diagram_rotated.jpeg" "$INSTALL_DIR/"
[ -f "$SCRIPT_DIR/thumbs_up.png" ] && cp "$SCRIPT_DIR/thumbs_up.png" "$INSTALL_DIR/"
[ -f "$SCRIPT_DIR/README.md" ] && cp "$SCRIPT_DIR/README.md" "$INSTALL_DIR/"

chmod +x "$INSTALL_DIR/start_iol_dashboard.sh"
chmod +x "$INSTALL_DIR/dashboard.py"

# Step 6: Install systemd service
log_step "6/7: Installing systemd service..."

sudo cp "$SCRIPT_DIR/iol_dashboard.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable iol_dashboard.service

# Step 7: Configure auto-login and screen
log_step "7/7: Configuring display settings..."

# GDM3 auto-login
if [ -f /etc/gdm3/custom.conf ]; then
    sudo sed -i 's/^#AutomaticLoginEnable =.*/AutomaticLoginEnable = true/' /etc/gdm3/custom.conf
    sudo sed -i 's/^#AutomaticLogin =.*/AutomaticLogin = pi/' /etc/gdm3/custom.conf
fi

# Disable idle timeout
if ! grep -q "IdleAction=ignore" /etc/systemd/logind.conf 2>/dev/null; then
    echo -e "\nIdleAction=ignore\nIdleActionSec=0" | sudo tee -a /etc/systemd/logind.conf > /dev/null
fi

# Done!
echo ""
log_info "=========================================="
log_info "Installation Complete!"
log_info "=========================================="
echo ""
log_info "Dashboard installed to: $INSTALL_DIR"
log_info "Service enabled: iol_dashboard.service"
echo ""
log_info "Next steps:"
log_info "  1. Reboot: sudo reboot"
log_info "  2. Check status: systemctl status iol_dashboard.service"
log_info "  3. View logs: tail -f ~/iol_dashboard.log"
echo ""

read -p "Reboot now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo reboot
fi
