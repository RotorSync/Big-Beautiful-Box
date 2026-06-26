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

setup_rotorlink() {
    # Install/refresh the RotorLink WiFi-link service. Idempotent + best-effort:
    # this script is re-pulled and run by the in-app updater, so it bootstraps the
    # WiFi service onto every box on its next update. Must never fail the update.
    local repo="/home/pi/Big-Beautiful-Box"
    [ -f "$repo/systemd/rotorlink.service" ] || return 0
    mkdir -p /etc/rotorlink
    [ -f /etc/rotorlink/ap.psk ] || printf 'rotorsync' > /etc/rotorlink/ap.psk
    cp "$repo/systemd/rotorlink.service" /etc/systemd/system/rotorlink.service || true
    systemctl daemon-reload || true
    systemctl enable rotorlink.service >/dev/null 2>&1 || true
    systemctl restart rotorlink.service >/dev/null 2>&1 || true
    # Deps (websockets/avahi) can need a slow apt; run them DETACHED so they never
    # block the updater's deploy window, then restart rotorlink once they land.
    local deps='python3 -c "import websockets" >/dev/null 2>&1 || apt-get -o Dpkg::Lock::Timeout=60 install -y python3-websockets >/dev/null 2>&1 || python3 -m pip install --break-system-packages websockets >/dev/null 2>&1 || true; command -v avahi-publish-service >/dev/null 2>&1 || apt-get -o Dpkg::Lock::Timeout=60 install -y avahi-utils >/dev/null 2>&1 || true; systemctl restart rotorlink.service >/dev/null 2>&1 || true'
    if command -v systemd-run >/dev/null 2>&1; then
        systemd-run --unit=bbb-rotorlink-deps --description="RotorLink deps" /bin/bash -c "$deps" >/dev/null 2>&1 || true
    else
        nohup /bin/bash -c "$deps" >/dev/null 2>&1 &
    fi
}

install_missing_cursor_tools
setup_rotorlink || true

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
