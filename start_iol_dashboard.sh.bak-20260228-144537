#!/bin/bash
# Startup script for IOL Dashboard and Master
# Part of Big-Beautiful-Box project

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$HOME/iol_dashboard.log"

# Find Xwayland auth file dynamically
if [ -d "/run/user/1000" ]; then
    export XAUTHORITY=$(ls /run/user/1000/.mutter-Xwayland* 2>/dev/null | head -1)
fi

export DISPLAY=:0
export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"

# Disable screen blanking
disable_screensaver() {
    xset s off 2>/dev/null || true
    xset s noblank 2>/dev/null || true
    xset -dpms 2>/dev/null || true
    gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true
    gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null || true
    gsettings set org.gnome.desktop.screensaver idle-activation-enabled false 2>/dev/null || true
    gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing' 2>/dev/null || true
    gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type 'nothing' 2>/dev/null || true
    gsettings set org.gnome.settings-daemon.plugins.power idle-dim false 2>/dev/null || true
    gsettings set org.gnome.desktop.notifications show-banners false 2>/dev/null || true
}

disable_screensaver

echo "$(date): Starting IOL Dashboard..." >> "$LOG_FILE"

# Start IOL master application if available
IOL_MASTER_PID=""
if [ -d "$HOME/iol-hat/src-master-application" ]; then
    echo "$(date): Starting IOL Master Application..." >> "$LOG_FILE"
    cd "$HOME/iol-hat/src-master-application"

    if [ -f "./bin/debug/iol-master-appl" ]; then
        ./bin/debug/iol-master-appl -m0 0 -m1 0 -i 34 >> "$LOG_FILE" 2>&1 &
        IOL_MASTER_PID=$!
        echo "$(date): IOL Master PID: $IOL_MASTER_PID" >> "$LOG_FILE"

        # Wait for IOL master to be ready
        for i in {1..10}; do
            if nc -z localhost 12011 2>/dev/null; then
                echo "$(date): IOL Master ready (TCP 12011 listening)" >> "$LOG_FILE"
                break
            fi
            sleep 1
        done
    else
        echo "$(date): IOL Master binary not found" >> "$LOG_FILE"
    fi
fi

# Start the dashboard
echo "$(date): Starting Dashboard GUI..." >> "$LOG_FILE"
cd "$SCRIPT_DIR"
python3 dashboard.py >> "$LOG_FILE" 2>&1 &
DASHBOARD_PID=$!
echo "$(date): Dashboard PID: $DASHBOARD_PID" >> "$LOG_FILE"

# Keep disabling screen blanking periodically
while kill -0 $DASHBOARD_PID 2>/dev/null; do
    sleep 120
    disable_screensaver
done &

# Wait for dashboard to exit
wait $DASHBOARD_PID
EXIT_CODE=$?

echo "$(date): Dashboard exited with code $EXIT_CODE" >> "$LOG_FILE"

# Clean up IOL master if we started it
if [ -n "$IOL_MASTER_PID" ]; then
    echo "$(date): Stopping IOL Master..." >> "$LOG_FILE"
    kill $IOL_MASTER_PID 2>/dev/null || true
fi

exit $EXIT_CODE
