#!/usr/bin/env bash

set -euo pipefail

THEME_NAME="trailersync"
THEME_DIR="/usr/share/plymouth/themes/${THEME_NAME}"
PLYMOUTH_FILE="${THEME_DIR}/${THEME_NAME}.plymouth"
SCRIPT_FILE="${THEME_DIR}/${THEME_NAME}.script"
IMAGE_NAME="Trailersync.png"
BOOT_CMDLINE_CANDIDATES=("/boot/firmware/cmdline.txt" "/boot/cmdline.txt")

usage() {
  cat <<'EOF'
Usage:
  sudo ./install-trailersync-boot-logo.sh /path/to/logo.png [--reboot]

What it does:
  - Installs a custom Plymouth theme for Ubuntu on Raspberry Pi
  - Uses a black background with a centered fade-in logo
  - Sets the theme as the default
  - Rebuilds initramfs

Notes:
  - Run this as root.
  - This script is intended for Ubuntu systems using Plymouth.
EOF
}

log() {
  printf '[%s] %s\n' "$1" "$2"
}

require_root() {
  if [[ ${EUID} -ne 0 ]]; then
    log ERROR "Run this script with sudo or as root."
    exit 1
  fi
}

require_cmd() {
  local cmd
  for cmd in "$@"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      log ERROR "Required command not found: $cmd"
      exit 1
    fi
  done
}

ensure_quiet_splash() {
  local cmdline_file=""
  local candidate

  for candidate in "${BOOT_CMDLINE_CANDIDATES[@]}"; do
    if [[ -f "$candidate" ]]; then
      cmdline_file="$candidate"
      break
    fi
  done

  if [[ -z "$cmdline_file" ]]; then
    log WARN "No cmdline.txt found; skipping quiet/splash check."
    return
  fi

  if grep -qw splash "$cmdline_file" && grep -qw quiet "$cmdline_file"; then
    log INFO "Kernel cmdline already contains quiet splash."
    return
  fi

  cp "$cmdline_file" "${cmdline_file}.bak.$(date +%Y%m%d%H%M%S)"

  python3 - "$cmdline_file" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
content = path.read_text().strip()
parts = content.split()
for flag in ("quiet", "splash"):
    if flag not in parts:
        parts.append(flag)
path.write_text(" ".join(parts) + "\n")
PY

  log INFO "Added quiet splash to ${cmdline_file}."
}

install_theme() {
  local image_path="$1"

  mkdir -p "$THEME_DIR"
  cp "$image_path" "${THEME_DIR}/${IMAGE_NAME}"

  cat >"$PLYMOUTH_FILE" <<EOF
[Plymouth Theme]
Name=TrailerSync
Description=TrailerSync boot splash with a centered fading logo
ModuleName=script

[script]
ImageDir=${THEME_DIR}
ScriptFile=${SCRIPT_FILE}
EOF

  cat >"$SCRIPT_FILE" <<'EOF'
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

  chmod 644 "${THEME_DIR}/${IMAGE_NAME}" "$PLYMOUTH_FILE" "$SCRIPT_FILE"
  log INFO "Installed theme files in ${THEME_DIR}."
}

set_default_theme() {
  update-alternatives --install \
    /usr/share/plymouth/themes/default.plymouth \
    default.plymouth \
    "$PLYMOUTH_FILE" \
    220 >/dev/null

  update-alternatives --set \
    default.plymouth \
    "$PLYMOUTH_FILE" >/dev/null

  log INFO "Set ${THEME_NAME} as the default Plymouth theme."
}

rebuild_initramfs() {
  update-initramfs -u
  log INFO "Rebuilt initramfs."
}

main() {
  local image_path=""
  local do_reboot="false"

  if [[ $# -lt 1 || $# -gt 2 ]]; then
    usage
    exit 1
  fi

  image_path="$1"
  if [[ $# -eq 2 ]]; then
    if [[ "$2" != "--reboot" ]]; then
      usage
      exit 1
    fi
    do_reboot="true"
  fi

  require_root
  require_cmd update-initramfs update-alternatives python3

  if [[ ! -f "$image_path" ]]; then
    log ERROR "Image not found: $image_path"
    exit 1
  fi

  if ! command -v plymouth-set-default-theme >/dev/null 2>&1 && [[ ! -d /usr/share/plymouth/themes ]]; then
    log ERROR "Plymouth does not appear to be installed on this system."
    exit 1
  fi

  log INFO "Installing TrailerSync boot theme from ${image_path}"
  install_theme "$image_path"
  set_default_theme
  ensure_quiet_splash
  rebuild_initramfs

  if [[ "$do_reboot" == "true" ]]; then
    log INFO "Rebooting now."
    reboot
  else
    log INFO "Done. Reboot to see the new splash."
  fi
}

main "$@"
