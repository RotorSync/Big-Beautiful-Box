#!/usr/bin/env bash
# Configure local cursor injection for TrailerSync dashboard control.

set -euo pipefail

RESTART_DASHBOARD=0
if [ "${1:-}" = "--restart-dashboard" ]; then
    RESTART_DASHBOARD=1
fi

if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        exec sudo "$0" "$@"
    fi
    echo "setup-cursor-control: root privileges required" >&2
    exit 1
fi

changed=0
rule_path="/etc/udev/rules.d/99-uinput-input.rules"
rule_text='KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"'

if ! getent group input >/dev/null; then
    groupadd --system input
    changed=1
fi

if id pi >/dev/null 2>&1 && ! id -nG pi | tr ' ' '\n' | grep -qx input; then
    usermod -aG input pi
    changed=1
fi

if [ ! -f "$rule_path" ] || [ "$(cat "$rule_path")" != "$rule_text" ]; then
    printf '%s\n' "$rule_text" > "$rule_path"
    changed=1
fi

if command -v modprobe >/dev/null 2>&1; then
    modprobe uinput 2>/dev/null || true
fi

if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=misc --attr-match=name=uinput 2>/dev/null || true
fi

if [ -e /dev/uinput ]; then
    current_group="$(stat -c '%G' /dev/uinput 2>/dev/null || true)"
    current_mode="$(stat -c '%a' /dev/uinput 2>/dev/null || true)"
    if [ "$current_group" != "input" ]; then
        chgrp input /dev/uinput
        changed=1
    fi
    if [ "$current_mode" != "660" ]; then
        chmod 660 /dev/uinput
        changed=1
    fi
fi

if [ "$RESTART_DASHBOARD" -eq 1 ] && [ "$changed" -eq 1 ]; then
    if systemctl list-unit-files iol_dashboard.service >/dev/null 2>&1; then
        if command -v systemd-run >/dev/null 2>&1; then
            systemd-run \
                --unit=bbb-cursor-control-dashboard-restart \
                --description="Restart dashboard after cursor-control setup" \
                --on-active=1s \
                /bin/systemctl try-restart iol_dashboard.service >/dev/null 2>&1 || true
        else
            systemctl try-restart iol_dashboard.service >/dev/null 2>&1 || true
        fi
    fi
fi

echo "setup-cursor-control: changed=$changed"
