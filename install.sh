#!/usr/bin/env bash
# Install the codexbar-waybar module + GTK popup into your Waybar config.
#
# Idempotent: re-running just refreshes the installed scripts.
#
# What it does:
#   - Copies codexbar.sh and codexbar-popup.py to $XDG_CONFIG_HOME/waybar/scripts/
#   - Drops codexbar.jsonc (as custom-codexbar.json) into $XDG_CONFIG_HOME/waybar/modules/
#   - Appends codexbar.css to $XDG_CONFIG_HOME/waybar/user-style.css (or creates it)
#   - Prints the snippet to add "custom/codexbar" to your config.jsonc
#
# What it does NOT do:
#   - Install the codexbar CLI itself (see README for that).
#   - Edit your config.jsonc — you wire "custom/codexbar" in yourself.

set -euo pipefail

WAYBAR_CONF="${XDG_CONFIG_HOME:-$HOME/.config}/waybar"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }

# 1. codexbar binary check
if ! command -v codexbar >/dev/null 2>&1; then
    yellow "Warning: 'codexbar' is not on PATH."
    yellow "Install the Linux CLI before continuing — see README.md for the tarball + libxml2-legacy notes."
fi

# 2. Required deps
for dep in jq python3; do
    if ! command -v "$dep" >/dev/null 2>&1; then
        red "Missing dependency: $dep"
        exit 1
    fi
done

# 3. Copy scripts.
mkdir -p "$WAYBAR_CONF/scripts" "$WAYBAR_CONF/modules"
install -m 0755 "$SCRIPT_DIR/codexbar.sh"        "$WAYBAR_CONF/scripts/codexbar.sh"
install -m 0755 "$SCRIPT_DIR/codexbar-popup.py"  "$WAYBAR_CONF/scripts/codexbar-popup.py"
install -m 0644 "$SCRIPT_DIR/codexbar.jsonc"     "$WAYBAR_CONF/modules/custom-codexbar.json"
green "Installed scripts → $WAYBAR_CONF/scripts/"
green "Installed module  → $WAYBAR_CONF/modules/custom-codexbar.json"

# 4. CSS — append if not already present.
USER_STYLE="$WAYBAR_CONF/user-style.css"
touch "$USER_STYLE"
if ! grep -q "#custom-codexbar" "$USER_STYLE"; then
    {
        echo
        echo "/* codexbar-waybar — appended by install.sh */"
        cat "$SCRIPT_DIR/codexbar.css"
    } >> "$USER_STYLE"
    green "Appended styling → $USER_STYLE"
else
    yellow "Skipped CSS (already contains #custom-codexbar rules in $USER_STYLE)"
fi

cat <<'EOF'

────────────────────────────────────────────────────────────
Last step: add "custom/codexbar" to a modules-right group in
your ~/.config/waybar/config.jsonc, e.g.:

  "modules-right": [..., "custom/codexbar", "clock", ...]

Then reload Waybar (Ctrl+Alt+W on HyDE, or `pkill waybar; waybar &`).
Click the 🤖 icon to open the popup.
────────────────────────────────────────────────────────────
EOF
