#!/bin/bash
# Install the systemd/journald files that install.sh installs, for boxes that
# update themselves (dashboard git update, BLE maintenance update, WiFi update).
#
# Mirrors install.sh:
#   cp rotorsync_watchdog.service /etc/systemd/system/; daemon-reload; enable
#   cp deploy/journald-bbb.conf /etc/systemd/journald.conf.d/bbb.conf; restart journald
#
# Rules:
#   * Idempotent: a file is copied only if missing or different; daemon-reload
#     and the journald restart happen only when something actually changed.
#     "enable" is always re-asserted (it is a no-op when already enabled).
#   * Never restarts rotorsync_watchdog.service itself; every caller restarts
#     the services after the update, watchdog last.
#   * Fail-soft: every step is best-effort and the script ALWAYS exits 0, so it
#     can never abort or roll back an update. Problems are printed as WARN.
#   * Copies are atomic (temp file + mv) so a power cut can't leave a half unit.
#
# Test hooks (env): BBB_REPO (repo dir), BBB_ROOT (prefix for /etc),
# BBB_SYSTEMCTL (systemctl command).

set -u
REPO="${BBB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)}"
ROOT="${BBB_ROOT:-}"
SYSTEMCTL="${BBB_SYSTEMCTL:-systemctl}"

warn() { echo "[units] WARN: $*" >&2; }
info() { echo "[units] $*"; }

# install_if_changed SRC DST MODE -> exit 0 = changed, 1 = unchanged, 2 = failed
install_if_changed() {
    local src="$1" dst="$2" mode="$3" tmp
    if [ ! -f "$src" ]; then
        warn "source missing: $src (skipped)"
        return 2
    fi
    if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
        return 1
    fi
    mkdir -p "$(dirname "$dst")" || { warn "cannot create $(dirname "$dst")"; return 2; }
    tmp="$(mktemp "$(dirname "$dst")/.bbb-unit.XXXXXX")" || { warn "mktemp failed for $dst"; return 2; }
    if cp "$src" "$tmp" && chmod "$mode" "$tmp" && mv -f "$tmp" "$dst"; then
        info "installed $dst"
        return 0
    fi
    rm -f "$tmp"
    warn "could not install $dst"
    return 2
}

unit_changed=0
install_if_changed "$REPO/rotorsync_watchdog.service" \
    "$ROOT/etc/systemd/system/rotorsync_watchdog.service" 644
rc=$?
[ $rc -eq 0 ] && unit_changed=1

if [ $unit_changed -eq 1 ]; then
    $SYSTEMCTL daemon-reload || warn "daemon-reload failed"
fi
if [ -f "$ROOT/etc/systemd/system/rotorsync_watchdog.service" ]; then
    $SYSTEMCTL enable rotorsync_watchdog.service >/dev/null 2>&1 \
        || warn "could not enable rotorsync_watchdog.service"
fi

install_if_changed "$REPO/deploy/journald-bbb.conf" \
    "$ROOT/etc/systemd/journald.conf.d/bbb.conf" 644
rc=$?
if [ $rc -eq 0 ]; then
    $SYSTEMCTL restart systemd-journald || warn "could not restart systemd-journald"
fi

exit 0
