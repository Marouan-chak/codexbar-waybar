#!/usr/bin/env bash
# Install the codexbar-waybar module + GTK popup into your Waybar config.
#
# Idempotent: re-running just refreshes the installed scripts.
#
# What it does:
#   - Copies codexbar.sh, provider helpers, and codexbar-popup.py to $XDG_CONFIG_HOME/waybar/scripts/
#   - Drops codexbar.jsonc (as custom-codexbar.json) into $XDG_CONFIG_HOME/waybar/modules/
#   - Appends codexbar.css to $XDG_CONFIG_HOME/waybar/user-style.css (or creates it)
#   - Installs provider SVG icons to $XDG_DATA_HOME/codexbar-waybar/icons/
#   - Prints the snippet to add "custom/codexbar" to your config.jsonc
#
# What it does NOT do:
#   - Install the codexbar CLI itself (see README for that).
#   - Edit your config.jsonc — you wire "custom/codexbar" in yourself.

set -euo pipefail

WAYBAR_CONF="${XDG_CONFIG_HOME:-$HOME/.config}/waybar"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/codexbar-waybar"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }

# 1. codexbar binary check
if ! command -v codexbar >/dev/null 2>&1; then
    red "'codexbar' is not on PATH."
    cat <<'EOF' >&2

Install the Linux CLI first (release assets are versioned, so resolve the
latest tag from the GitHub API; swap x86_64 → aarch64 on ARM):

  ARCH=x86_64   # or aarch64
  TAG=$(curl -fsSL https://api.github.com/repos/steipete/CodexBar/releases/latest | grep -oP '"tag_name":\s*"\K[^"]+')
  curl -fLO "https://github.com/steipete/CodexBar/releases/download/${TAG}/CodexBarCLI-${TAG}-linux-${ARCH}.tar.gz"
  tar -xzf "CodexBarCLI-${TAG}-linux-${ARCH}.tar.gz"
  install -m 0755 CodexBarCLI ~/.local/bin/codexbar

Arch users also need:  sudo pacman -S libxml2-legacy

Then re-run this installer.
EOF
    exit 1
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
install -m 0755 "$SCRIPT_DIR/codexbar-commandcode-api.py" "$WAYBAR_CONF/scripts/codexbar-commandcode-api.py"
install -m 0755 "$SCRIPT_DIR/codexbar-opencodego-local.py" "$WAYBAR_CONF/scripts/codexbar-opencodego-local.py"
install -m 0644 "$SCRIPT_DIR/codexbar.jsonc"     "$WAYBAR_CONF/modules/custom-codexbar.json"
green "Installed scripts → $WAYBAR_CONF/scripts/"
green "Installed module  → $WAYBAR_CONF/modules/custom-codexbar.json"

# Compile SSL redirect shim for Antigravity
COMPILER=""
for cmd in gcc clang cc; do
    if command -v "$cmd" >/dev/null 2>&1; then
        COMPILER="$cmd"
        break
    fi
done

if [[ -n "$COMPILER" ]]; then
    if "$COMPILER" -shared -fPIC -o "$WAYBAR_CONF/scripts/cert_redirect.so" "$SCRIPT_DIR/cert_redirect.c" -ldl 2>/dev/null; then
        green "Compiled SSL redirect shim → $WAYBAR_CONF/scripts/cert_redirect.so"
    else
        red "Failed to compile SSL redirect shim."
    fi
else
    yellow "No C compiler (gcc/clang/cc) found on PATH. Skipping compilation of SSL redirect shim."
fi

# 4. Provider SVG icons (for the popup tabs/settings rows).
mkdir -p "$DATA_DIR/icons"
if [[ -d "$SCRIPT_DIR/assets/providers" ]]; then
    install -m 0644 "$SCRIPT_DIR/assets/providers"/ProviderIcon-*.svg "$DATA_DIR/icons/"
    [[ -f "$SCRIPT_DIR/assets/providers/NOTICE" ]] && install -m 0644 "$SCRIPT_DIR/assets/providers/NOTICE" "$DATA_DIR/icons/NOTICE"
    green "Installed icons   → $DATA_DIR/icons/"
fi

# 5. CSS — append if not already present.
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
