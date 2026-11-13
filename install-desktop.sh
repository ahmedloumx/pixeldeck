#!/usr/bin/env bash
# Linux: add PixelDeck to the app grid, the desktop and the dock (GNOME favourites).
#   ./install-desktop.sh            install and pin to the dock
#   ./install-desktop.sh --no-pin   install without pinning
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ID="io.github.ahmedloumx.PixelDeck"   # config.APP_ID: GNOME ties the window to <ID>.desktop
ICON="pixeldeck"
# Ids and icons an earlier version installed under: replaced, and a dock pin keeps its place.
OLD_IDS=("local.prdesk.Dashboard")
OLD_ICONS=("my-personal-dashboard")
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
mkdir -p "$APPS" "$ICONS"

for old in "${OLD_IDS[@]}"; do rm -f "$APPS/$old.desktop" "$DESKTOP_DIR/$old.desktop"; done
for old in "${OLD_ICONS[@]}"; do rm -f "$ICONS/$old.svg"; done

cp "$DIR/static/app-icon.svg" "$ICONS/$ICON.svg"
# The icon is named by its full path in the launcher: GNOME Shell scans icon names once at login,
# so a name installed later would show a generic icon until the next login. The folder is also
# touched, which makes running shells that do watch it look again.
touch "${ICONS%/scalable/apps}"
cat >"$APPS/$ID.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=PixelDeck
Comment=Bitbucket pull requests, review threads and Claude Code fixes
Exec="$DIR/run.sh"
Icon=$ICONS/$ICON.svg
Terminal=false
Categories=Development;
Keywords=PixelDeck;PR;pull request;Bitbucket;review;dashboard;
StartupWMClass=$ID
StartupNotify=true
DESKTOP
update-desktop-database "$APPS" >/dev/null 2>&1 || true
gtk-update-icon-cache -q "${ICONS%/scalable/apps}" >/dev/null 2>&1 || true

if [[ -d "$DESKTOP_DIR" ]]; then
  install -m 755 "$APPS/$ID.desktop" "$DESKTOP_DIR/$ID.desktop"
  gio set "$DESKTOP_DIR/$ID.desktop" metadata::trusted true 2>/dev/null || true
fi

if [[ "${1:-}" != "--no-pin" ]] && command -v gsettings >/dev/null && gsettings list-schemas | grep -qx org.gnome.shell; then
  favs="$(gsettings get org.gnome.shell favorite-apps)"
  for old in "${OLD_IDS[@]}"; do favs="${favs//\'$old.desktop\'/\'$ID.desktop\'}"; done
  if [[ "$favs" != *"'$ID.desktop'"* ]]; then
    if [[ "$favs" == "@as []" || "$favs" == "[]" ]]; then favs="['$ID.desktop']"; else favs="${favs%]*}, '$ID.desktop']"; fi
  fi
  gsettings set org.gnome.shell favorite-apps "$favs"
fi
echo "Installed PixelDeck: app grid, desktop and dock"
