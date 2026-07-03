#!/usr/bin/env bash
# Quiet the Ubuntu "N updates can be applied" / "new release available" nags on
# the boxes. We deliberately hold the whole fleet at one OS level and upgrade it
# out of season, all at once, on the bench (see AGENTS.md) — so the login-time
# update banner is pure noise on a field box.
#
# This does NOT change the security posture (we weren't applying these anyway)
# and does NOT disable anything that would auto-upgrade a box: it only silences
# the display. It is idempotent and fail-soft — every step guards on presence
# and never aborts the caller. Run as root (installer/updater already are).
set -u

# 1) Login MOTD scripts that print the update/release nag. chmod -x is fully
#    reversible (chmod +x restores them) and leaves the scripts in place.
for f in \
    /etc/update-motd.d/90-updates-available \
    /etc/update-motd.d/91-release-upgrade \
    /etc/update-motd.d/50-motd-news; do
    [ -f "$f" ] && chmod -x "$f" 2>/dev/null || true
done

# 2) Don't offer an Ubuntu RELEASE upgrade (do-release-upgrade) — release moves
#    are a deliberate out-of-season bench operation, never an in-season prompt.
if [ -f /etc/update-manager/release-upgrades ]; then
    if grep -qiE '^[[:space:]]*Prompt[[:space:]]*=' /etc/update-manager/release-upgrades; then
        sed -i 's/^[[:space:]]*Prompt[[:space:]]*=.*/Prompt=never/I' \
            /etc/update-manager/release-upgrades 2>/dev/null || true
    fi
fi

# 3) Blank the cached banner so the count disappears immediately (it won't
#    refresh — apt periodic update-package-lists is already 0 on the fleet).
if [ -f /var/lib/update-notifier/updates-available ]; then
    : > /var/lib/update-notifier/updates-available 2>/dev/null || true
fi

exit 0
