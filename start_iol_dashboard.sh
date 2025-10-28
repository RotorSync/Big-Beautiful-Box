#!/bin/bash
# Startup script for IOL Dashboard and Master
# Modified to keep dashboard running even if IOL-HAT fails

# Find Xwayland auth file dynamically
if [ -d "/run/user/1000" ]; then
    export XAUTHORITY=$(ls /run/user/1000/.mutter-Xwayland* 2>/dev/null | head -1)
fi

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

# Wait for dashboard to exit (ignore IOL master crashes)
wait $DASHBOARD_PID

# If dashboard exits, kill IOL master if it's running
if [ -n "$IOL_MASTER_PID" ]; then
    echo "$(date): Dashboard exited, stopping IOL Master..." >> "$LOG_FILE"
    kill $IOL_MASTER_PID 2>/dev/null
fi

exit 0
