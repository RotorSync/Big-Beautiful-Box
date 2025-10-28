#!/bin/bash
# Startup script for IOL Dashboard and Master
# Modified to keep dashboard running even if IOL-HAT fails

# Find Xwayland auth file dynamically
if [ -d "/run/user/1000" ]; then
    export XAUTHORITY=$(ls /run/user/1000/.mutter-Xwayland* 2>/dev/null | head -1)
fi

# Disable screen blanking and power management
export DISPLAY=:0
export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"

# X11 settings
xset s off 2>/dev/null          # Disable screen saver
xset s noblank 2>/dev/null      # Disable screen blanking
xset -dpms 2>/dev/null          # Disable DPMS (Energy Star) features

# GNOME settings (these are critical for Ubuntu)
gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null
gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null
gsettings set org.gnome.desktop.screensaver idle-activation-enabled false 2>/dev/null
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing' 2>/dev/null
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type 'nothing' 2>/dev/null
gsettings set org.gnome.settings-daemon.plugins.power idle-dim false 2>/dev/null

# Disable system notifications and warnings
gsettings set org.gnome.desktop.notifications show-banners false 2>/dev/null
gsettings set org.gnome.desktop.notifications show-in-lock-screen false 2>/dev/null

# Log file location
LOG_FILE="/home/user/iol_dashboard.log"

# Try to start the IOL master application (optional - may fail if IOL-HAT disconnected)
cd /home/user/iol-hat/src-master-application
echo "$(date): Attempting to start IOL Master Application..." >> "$LOG_FILE"
./bin/debug/iol_master_app >> "$LOG_FILE" 2>&1 &
IOL_MASTER_PID=$!

# Wait a moment to see if it crashes immediately
sleep 2

# Check if IOL master is still running
if kill -0 $IOL_MASTER_PID 2>/dev/null; then
    echo "$(date): IOL Master started successfully (PID: $IOL_MASTER_PID)" >> "$LOG_FILE"
else
    echo "$(date): IOL Master failed to start - continuing without it" >> "$LOG_FILE"
    IOL_MASTER_PID=""
fi

# Always start the dashboard GUI (works without IOL-HAT for relay control)
echo "$(date): Starting Dashboard GUI..." >> "$LOG_FILE"
cd /home/user
python3 dashboard.py >> "$LOG_FILE" 2>&1 &
DASHBOARD_PID=$!

echo "$(date): Dashboard PID: $DASHBOARD_PID" >> "$LOG_FILE"

# Keep disabling screen blanking every 2 minutes (in background)
while kill -0 $DASHBOARD_PID 2>/dev/null; do
    sleep 120
    # Re-apply X11 settings
    xset s off 2>/dev/null
    xset s noblank 2>/dev/null
    xset -dpms 2>/dev/null
    # Re-apply GNOME settings
    gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null
    gsettings set org.gnome.settings-daemon.plugins.power idle-dim false 2>/dev/null
    gsettings set org.gnome.desktop.notifications show-banners false 2>/dev/null
done &

# Wait for dashboard to exit (ignore IOL master crashes)
wait $DASHBOARD_PID

# If dashboard exits, kill IOL master if it's running
if [ -n "$IOL_MASTER_PID" ]; then
    echo "$(date): Dashboard exited, stopping IOL Master..." >> "$LOG_FILE"
    kill $IOL_MASTER_PID 2>/dev/null
fi

exit 0
