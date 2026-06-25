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

install_missing_cursor_tools() {
    local missing=()
    local tool

    for tool in xdotool ydotool; do
        if ! command -v "$tool" >/dev/null 2>&1 && [ ! -x "/usr/bin/$tool" ]; then
            missing+=("$tool")
        fi
    done

    if [ "${#missing[@]}" -eq 0 ]; then
        return 0
    fi

    if ! command -v apt-get >/dev/null 2>&1; then
        echo "setup-cursor-control: missing ${missing[*]}, apt-get unavailable" >&2
        return 0
    fi

    local apt_opts=(
        -o Acquire::Retries=2
        -o Acquire::http::Timeout=20
        -o Acquire::https::Timeout=20
        -o Dpkg::Lock::Timeout=20
    )
    local update_cmd=(apt-get "${apt_opts[@]}" update)
    local install_cmd=(apt-get "${apt_opts[@]}" install -y --no-install-recommends "${missing[@]}")

    echo "setup-cursor-control: installing missing tools: ${missing[*]}"
    if command -v timeout >/dev/null 2>&1; then
        timeout 240 "${update_cmd[@]}" || {
            echo "setup-cursor-control: apt-get update failed; cursor tools still missing" >&2
            return 0
        }
        timeout 240 "${install_cmd[@]}" || {
            echo "setup-cursor-control: apt-get install failed; cursor tools still missing" >&2
            return 0
        }
    else
        "${update_cmd[@]}" || {
            echo "setup-cursor-control: apt-get update failed; cursor tools still missing" >&2
            return 0
        }
        "${install_cmd[@]}" || {
            echo "setup-cursor-control: apt-get install failed; cursor tools still missing" >&2
            return 0
        }
    fi

    changed=1
}

install_missing_cursor_tools

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
