#!/usr/bin/env bash
# Per-user installer for the RACoN Linux build. No root privileges required.
#
# Usage:
#   ./install.sh                 install the desktop app
#   ./install.sh --autostart     also start RACoN in the tray at login
#   ./install.sh --service       also install and start a systemd user worker (headless)
#   ./install.sh --uninstall     remove RACoN (user data is kept)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_SOURCE="$SCRIPT_DIR/racon"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/racon-app"
BIN_DIR="$HOME/.local/bin"
BIN="$BIN_DIR/racon"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

AUTOSTART=0
SERVICE=0
UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --autostart) AUTOSTART=1 ;;
        --service) SERVICE=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help)
            sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

remove_entry() {
    rm -f "$APPS_DIR/racon.desktop" "$AUTOSTART_DIR/racon.desktop" "$BIN"
}

if [ "$UNINSTALL" -eq 1 ]; then
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user disable --now racon-worker.service >/dev/null 2>&1 || true
    fi
    rm -f "$SYSTEMD_DIR/racon-worker.service"
    remove_entry
    rm -rf "$DEST"
    echo "RACoN removed. User data under ${XDG_DATA_HOME:-$HOME/.local/share}/RACoN was kept."
    exit 0
fi

if [ ! -x "$APP_SOURCE/racon" ]; then
    echo "racon binary not found at $APP_SOURCE/racon. Run this script from the extracted release folder." >&2
    exit 1
fi

install -d "$DEST" "$BIN_DIR" "$APPS_DIR"
rm -rf "$DEST"
cp -a "$APP_SOURCE" "$DEST"
ln -sfn "$DEST/racon" "$BIN"

ICON="$DEST/apps/desktop/icon.png"
if [ ! -f "$ICON" ]; then
    ICON="$DEST/_internal/apps/desktop/icon.png"
fi

render_entry() {
    local target="$1"
    local exec_line="$2"
    sed -e "s|@EXEC@|$exec_line|g" -e "s|@ICON@|$ICON|g" \
        "$SCRIPT_DIR/racon.desktop" > "$target"
    chmod 644 "$target"
}

render_entry "$APPS_DIR/racon.desktop" "$BIN"

if [ "$AUTOSTART" -eq 1 ]; then
    install -d "$AUTOSTART_DIR"
    render_entry "$AUTOSTART_DIR/racon.desktop" "$BIN --autostart"
    echo "Autostart enabled: $AUTOSTART_DIR/racon.desktop"
fi

if [ "$SERVICE" -eq 1 ]; then
    if ! command -v systemctl >/dev/null 2>&1; then
        echo "systemctl not found; skipping the systemd user service." >&2
    else
        install -d "$SYSTEMD_DIR"
        sed -e "s|@EXEC@|$BIN|g" "$SCRIPT_DIR/racon-worker.service" > "$SYSTEMD_DIR/racon-worker.service"
        systemctl --user daemon-reload
        systemctl --user enable --now racon-worker.service
        echo "Headless worker installed: systemctl --user status racon-worker.service"
    fi
fi

echo "RACoN installed."
echo "  App:      $BIN"
echo "  Desktop:  $APPS_DIR/racon.desktop"
echo "  Data:     ${XDG_DATA_HOME:-$HOME/.local/share}/RACoN"
echo "Ensure $BIN_DIR is on your PATH, then run: racon"
