#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${BBB_TK_PYTHON:-$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3}"
APP_DIR="${TMPDIR:-/tmp}/BBBTk.app"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python runtime not found: $PYTHON_BIN" >&2
  exit 1
fi

mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>BBBTk</string>
  <key>CFBundleIdentifier</key><string>local.bbb.tk</string>
  <key>CFBundleName</key><string>BBBTk</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSBackgroundOnly</key><false/>
</dict>
</plist>
PLIST

cat > "$APP_DIR/Contents/MacOS/BBBTk" <<SH
#!/usr/bin/env bash
cd "$ROOT_DIR"
exec "$PYTHON_BIN" "$ROOT_DIR/sim_dashboard.py"
SH
chmod +x "$APP_DIR/Contents/MacOS/BBBTk"

open -n "$APP_DIR"
