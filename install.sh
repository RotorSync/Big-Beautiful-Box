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

append_if_missing() {
    local file="$1"
    local line="$2"
    grep -Fqx "$line" "$file" || echo "$line" | sudo tee -a "$file" > /dev/null
}

install_boot_logo() {
    local image_path="$1"
    local theme_name="trailersync"
    local theme_dir="/usr/share/plymouth/themes/${theme_name}"
    local plymouth_file="${theme_dir}/${theme_name}.plymouth"
    local script_file="${theme_dir}/${theme_name}.script"
    local image_name="Trailersync.png"
    local cmdline_file=""
    local candidate

    if [ ! -f "$image_path" ]; then
        log_warn "Boot logo image missing; skipping Plymouth theme install."
        return
    fi

    if ! command -v update-initramfs >/dev/null 2>&1 || ! command -v update-alternatives >/dev/null 2>&1; then
        log_warn "Plymouth tools missing; skipping boot logo install."
        return
    fi

    sudo mkdir -p "$theme_dir"
    sudo cp "$image_path" "${theme_dir}/${image_name}"

    sudo tee "$plymouth_file" > /dev/null <<EOF
[Plymouth Theme]
Name=TrailerSync
Description=TrailerSync boot splash with a centered fading logo
ModuleName=script

[script]
ImageDir=${theme_dir}
ScriptFile=${script_file}
EOF

    sudo tee "$script_file" > /dev/null <<'EOF'
Window.SetBackgroundTopColor(0.0, 0.0, 0.0);
Window.SetBackgroundBottomColor(0.0, 0.0, 0.0);

logo_image = Image("Trailersync.png");
logo_sprite = Sprite(logo_image);

screen_width = Window.GetWidth();
screen_height = Window.GetHeight();
logo_width = logo_image.GetWidth();
logo_height = logo_image.GetHeight();

logo_sprite.SetX((screen_width - logo_width) / 2);
logo_sprite.SetY((screen_height - logo_height) / 2);
logo_sprite.SetZ(100);
logo_sprite.SetOpacity(0.0);

opacity = 0.0;
fade_step = 0.02;

fun refresh_callback() {
    if (opacity < 1.0) {
        opacity += fade_step;

        if (opacity > 1.0) {
            opacity = 1.0;
        }

        logo_sprite.SetOpacity(opacity);
    }
}

Plymouth.SetRefreshFunction(refresh_callback);
EOF

    sudo chmod 644 "${theme_dir}/${image_name}" "$plymouth_file" "$script_file"

    sudo update-alternatives --install \
        /usr/share/plymouth/themes/default.plymouth \
        default.plymouth \
        "$plymouth_file" \
        220 >/dev/null

    sudo update-alternatives --set \
        default.plymouth \
        "$plymouth_file" >/dev/null

    for candidate in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
        if [ -f "$candidate" ]; then
            cmdline_file="$candidate"
            break
        fi
    done

    if [ -n "$cmdline_file" ]; then
        if ! grep -qw quiet "$cmdline_file" || ! grep -qw splash "$cmdline_file"; then
            sudo cp "$cmdline_file" "${cmdline_file}.bak.$(date +%Y%m%d%H%M%S)"
            sudo python3 - "$cmdline_file" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
parts = path.read_text().strip().split()
for flag in ("quiet", "splash"):
    if flag not in parts:
        parts.append(flag)
path.write_text(" ".join(parts) + "\n")
PY
        fi
    else
        log_warn "No cmdline.txt found; skipped quiet/splash enforcement."
    fi

    sudo update-initramfs -u
    log_info "Installed TrailerSync Plymouth boot logo."
}

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    log_error "Do not run as root. Run as the pi user."
    exit 1
fi

INSTALL_USER=$(whoami)
INSTALL_HOME=$HOME
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$INSTALL_HOME/Big-Beautiful-Box"
OPT_DIR="/opt"
SOFTWARE_VERSION="$(cat "$SCRIPT_DIR/VERSION" 2>/dev/null || echo "unknown")"
REPO_URL="$(git -C "$SCRIPT_DIR" remote get-url origin 2>/dev/null || true)"
REPO_BRANCH="master"

if [ "$INSTALL_USER" != "pi" ]; then
    log_error "This installer currently expects to run as the pi user."
    exit 1
fi

echo ""
log_info "=========================================="
log_info "IOL Dashboard Installation"
log_info "=========================================="
echo ""
log_info "User: $INSTALL_USER"
log_info "Install directory: $INSTALL_DIR"
log_info "Software version: $SOFTWARE_VERSION"
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
    netcat-openbsd \
    python3-pip \
    python3-yaml \
    bluez \
    bluez-tools \
    plymouth \
    plymouth-themes

sudo usermod -a -G dialout $INSTALL_USER

# Install Python packages used by the BLE/Rotorsync stack
python3 -m pip install --break-system-packages --upgrade pip
python3 -m pip install --break-system-packages bleak bumble

# Step 3: Install vendored IOL-HAT
log_step "3/7: Setting up IOL-HAT..."
if [ ! -d "$SCRIPT_DIR/iol-hat" ]; then
    log_error "Vendored iol-hat directory is missing from this repo."
    exit 1
fi

rm -rf "$INSTALL_HOME/iol-hat"
mkdir -p "$INSTALL_HOME/iol-hat"
cp -r "$SCRIPT_DIR/iol-hat/"* "$INSTALL_HOME/iol-hat/"
log_info "Installed vendored iol-hat snapshot from BBB repo"

# Build IOL-HAT
if [ -d "$INSTALL_HOME/iol-hat/src-master-application" ]; then
    cd "$INSTALL_HOME/iol-hat/src-master-application"
    make clean 2>/dev/null || true
    make debug || log_warn "IOL-HAT build failed (may need hardware)"
fi

# Step 4: Configure hardware
log_step "4/7: Configuring hardware..."

# Apply tracked BBB boot/display settings
BBB_BOOT_CFG="/boot/firmware/config.txt"
BBB_BOOT_SNIPPET="$SCRIPT_DIR/deploy/boot-firmware-bbb.conf"
UART_CHANGED=0
if [ -f "$BBB_BOOT_SNIPPET" ]; then
    sudo cp "$BBB_BOOT_CFG" "${BBB_BOOT_CFG}.bbb.bak" 2>/dev/null || true
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        if ! grep -Fqx "$line" "$BBB_BOOT_CFG"; then
            echo "$line" | sudo tee -a "$BBB_BOOT_CFG" > /dev/null
            case "$line" in
                enable_uart=*|dtparam=uart0=*|dtoverlay=disable-bt)
                    UART_CHANGED=1
                    ;;
            esac
        fi
    done < "$BBB_BOOT_SNIPPET"
fi

# Explicitly enforce BBB UART settings for the switch box serial link.
for uart_line in "enable_uart=0" "dtparam=uart0=on" "dtoverlay=disable-bt"; do
    if ! grep -Fqx "$uart_line" "$BBB_BOOT_CFG"; then
        append_if_missing "$BBB_BOOT_CFG" "$uart_line"
        UART_CHANGED=1
    fi
done

if [ "$UART_CHANGED" -eq 1 ]; then
    log_warn "Applied UART boot settings for /dev/ttyAMA0. A reboot is required before the switch box serial link will work."
fi

# Step 5: Install dashboard and Rotorsync files
log_step "5/7: Installing dashboard files..."

if [ -n "$REPO_URL" ]; then
    if git -C "$INSTALL_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        log_info "Refreshing BBB git checkout in $INSTALL_DIR"
        git -C "$INSTALL_DIR" fetch origin
        git -C "$INSTALL_DIR" checkout -f "$REPO_BRANCH"
        git -C "$INSTALL_DIR" reset --hard "origin/$REPO_BRANCH"
    else
        [ -d "$INSTALL_DIR" ] && mv "$INSTALL_DIR" "${INSTALL_DIR}.pre-git-backup.$(date +%Y%m%d-%H%M%S)"
        log_info "Cloning BBB repo into $INSTALL_DIR"
        git clone --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
    fi
else
    log_warn "Installer repo has no origin remote; installing plain files and GitHub updater will not work on this box."
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$INSTALL_DIR/src"
    mkdir -p "$INSTALL_DIR/RPi"
    mkdir -p "$INSTALL_DIR/mopeka"
    mkdir -p "$INSTALL_DIR/deploy"
fi

mkdir -p "$INSTALL_DIR/src"
mkdir -p "$INSTALL_DIR/RPi"
mkdir -p "$INSTALL_DIR/mopeka"
mkdir -p "$INSTALL_DIR/deploy"
sudo mkdir -p "$OPT_DIR/src"
sudo mkdir -p "$OPT_DIR/mopeka"

# Copy dashboard/runtime files
cp "$SCRIPT_DIR/dashboard.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/config.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/VERSION" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/iolhat.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/start_iol_dashboard.sh" "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/src/"* "$INSTALL_DIR/src/"
cp -r "$SCRIPT_DIR/RPi/"* "$INSTALL_DIR/RPi/"
cp -r "$SCRIPT_DIR/mopeka/"* "$INSTALL_DIR/mopeka/"
cp -r "$SCRIPT_DIR/deploy/"* "$INSTALL_DIR/deploy/"
cp "$SCRIPT_DIR/install.sh" "$INSTALL_DIR/"
[ -f "$SCRIPT_DIR/Trailersync.png" ] && cp "$SCRIPT_DIR/Trailersync.png" "$INSTALL_DIR/"

# Copy Rotorsync runtime files to /opt to match service paths
sudo cp "$SCRIPT_DIR/rotorsync_bumble.py" "$OPT_DIR/rotorsync_bumble.py"
sudo cp "$SCRIPT_DIR/rotorsync_watchdog.py" "$OPT_DIR/rotorsync_watchdog.py"
sudo cp "$SCRIPT_DIR/src/__init__.py" "$OPT_DIR/src/"
sudo cp "$SCRIPT_DIR/src/mopeka_converter.py" "$OPT_DIR/src/mopeka_converter.py"
sudo cp -r "$SCRIPT_DIR/mopeka/"* "$OPT_DIR/mopeka/"
sudo chmod 755 "$OPT_DIR/rotorsync_bumble.py" "$OPT_DIR/rotorsync_watchdog.py"

# Copy optional files
[ -f "$SCRIPT_DIR/bbb_diagram_rotated.jpeg" ] && cp "$SCRIPT_DIR/bbb_diagram_rotated.jpeg" "$INSTALL_DIR/"
[ -f "$SCRIPT_DIR/thumbs_up.png" ] && cp "$SCRIPT_DIR/thumbs_up.png" "$INSTALL_DIR/"
[ -f "$SCRIPT_DIR/thumbs_up.png" ] && cp "$SCRIPT_DIR/thumbs_up.png" "$INSTALL_HOME/"
[ -f "$SCRIPT_DIR/README.md" ] && cp "$SCRIPT_DIR/README.md" "$INSTALL_DIR/"

chmod +x "$INSTALL_DIR/start_iol_dashboard.sh"
chmod +x "$INSTALL_DIR/dashboard.py"

# Step 6: Install systemd services
log_step "6/7: Installing systemd service..."

sudo cp "$SCRIPT_DIR/iol_dashboard.service" /etc/systemd/system/
sudo cp "$SCRIPT_DIR/rotorsync.service" /etc/systemd/system/
sudo cp "$SCRIPT_DIR/rotorsync_watchdog.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable iol_dashboard.service
sudo systemctl enable rotorsync.service
sudo systemctl enable rotorsync_watchdog.service

# Step 7: Configure auto-login, screen, and log retention
log_step "7/7: Configuring display settings and log retention..."

# GDM3 auto-login / X11 display manager settings
if [ -f /etc/gdm3/custom.conf ] && [ -f "$SCRIPT_DIR/deploy/gdm3-custom.conf" ]; then
    sudo cp /etc/gdm3/custom.conf /etc/gdm3/custom.conf.bbb.bak 2>/dev/null || true
    sudo cp "$SCRIPT_DIR/deploy/gdm3-custom.conf" /etc/gdm3/custom.conf
fi

# Disable idle timeout
if ! grep -q "IdleAction=ignore" /etc/systemd/logind.conf 2>/dev/null; then
    echo -e "\nIdleAction=ignore\nIdleActionSec=0" | sudo tee -a /etc/systemd/logind.conf > /dev/null
fi

# Cap noisy BBB logs while preserving seasonal fill history logs.
sudo mkdir -p /home/pi/bug_reports
if [ -f "$SCRIPT_DIR/deploy/bbb-logrotate.conf" ]; then
    sudo cp "$SCRIPT_DIR/deploy/bbb-logrotate.conf" /etc/logrotate.d/bbb
fi

# Cap journald disk usage for unattended deployments.
if [ -f "$SCRIPT_DIR/deploy/journald-bbb.conf" ]; then
    sudo mkdir -p /etc/systemd/journald.conf.d
    sudo cp "$SCRIPT_DIR/deploy/journald-bbb.conf" /etc/systemd/journald.conf.d/bbb.conf
    sudo systemctl restart systemd-journald
fi

# Disable unattended package updates for in-season stability.
if [ -f "$SCRIPT_DIR/deploy/20auto-upgrades-bbb" ]; then
    sudo cp "$SCRIPT_DIR/deploy/20auto-upgrades-bbb" /etc/apt/apt.conf.d/20auto-upgrades
fi
if [ -f "$SCRIPT_DIR/deploy/10periodic-bbb" ]; then
    sudo cp "$SCRIPT_DIR/deploy/10periodic-bbb" /etc/apt/apt.conf.d/10periodic
fi
sudo systemctl disable --now unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true

# Install TrailerSync Plymouth boot logo if the bundled asset is present.
if [ -f "$INSTALL_DIR/Trailersync.png" ]; then
    install_boot_logo "$INSTALL_DIR/Trailersync.png" || \
        log_warn "Boot logo install failed; continuing"
fi

# Done!
echo ""
log_info "=========================================="
log_info "Installation Complete!"
log_info "=========================================="
echo ""
log_info "Dashboard installed to: $INSTALL_DIR"
log_info "Rotorsync installed to: $OPT_DIR"
log_info "Services enabled: iol_dashboard.service, rotorsync.service, rotorsync_watchdog.service"
echo ""
log_info "Next steps:"
log_info "  1. Reboot: sudo reboot"
log_info "  2. Check status: systemctl status iol_dashboard.service"
log_info "  3. Check BLE status: systemctl status rotorsync.service"
log_info "  4. After reboot, confirm serial: ls -l /dev/ttyAMA0"
log_info "  5. View logs: tail -f ~/iol_dashboard.log"
echo ""

read -p "Reboot now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo reboot
fi
