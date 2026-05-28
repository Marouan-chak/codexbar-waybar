#!/usr/bin/env python3
"""GTK4 popover for CodexBar Linux CLI.

Mirrors the macOS CodexBar menu popover: a provider tab strip at the top,
the active provider's usage windows shown as flat sections separated by
hairline dividers, no card boxes, thin progress bars, light translucent
background, dark text.

Anchored top-right via gtk4-layer-shell. Reads the cached last.json for
instant paint, then refetches in the background.
"""

from __future__ import annotations

import datetime
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from threading import Thread

# gtk4-layer-shell must load before libwayland-client; re-exec with LD_PRELOAD.
# Override the lib location with CODEXBAR_LAYER_SHELL_LIB if needed.
_LAYER_SHELL_LIB_CANDIDATES = [
    os.environ.get("CODEXBAR_LAYER_SHELL_LIB", ""),
    "/usr/lib/libgtk4-layer-shell.so",                   # Arch
    "/usr/lib/x86_64-linux-gnu/libgtk4-layer-shell.so",  # Debian / Ubuntu
    "/usr/lib64/libgtk4-layer-shell.so",                 # Fedora
    "/usr/lib/aarch64-linux-gnu/libgtk4-layer-shell.so",
]
_LAYER_SHELL_LIB = next((p for p in _LAYER_SHELL_LIB_CANDIDATES if p and os.path.exists(p)), "")
if os.environ.get("CODEXBAR_POPUP_PRELOADED") != "1" and _LAYER_SHELL_LIB:
    env = dict(os.environ)
    existing = env.get("LD_PRELOAD", "")
    env["LD_PRELOAD"] = f"{_LAYER_SHELL_LIB}:{existing}" if existing else _LAYER_SHELL_LIB
    env["CODEXBAR_POPUP_PRELOADED"] = "1"
    os.execve(sys.executable, [sys.executable, *sys.argv], env)

import re  # noqa: E402

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")

from gi.repository import GLib, Gtk, Gtk4LayerShell  # noqa: E402

CODEXBAR = os.environ.get("CODEXBAR_BIN", str(Path.home() / ".local/bin/codexbar"))
CACHE = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "codexbar-waybar"
LAST_GOOD = CACHE / "last.json"
SCRIPT_DIR = Path(__file__).resolve().parent
WRAPPER = SCRIPT_DIR / "codexbar.sh"

PROVIDER_NAMES = {
    "codex": "Codex",
    "claude": "Claude",
    "gemini": "Gemini",
    "copilot": "Copilot",
    "cursor": "Cursor",
    "vertexai": "Vertex AI",
    "openrouter": "OpenRouter",
    "openai": "OpenAI",
    "kimik2": "Kimi K2",
    "antigravity": "Antigravity",
}

WINDOW_LABELS = {
    "primary": "Session",
    "secondary": "Weekly",
    "tertiary": "Monthly",
}

# Provider id → icon filename (without the "ProviderIcon-" prefix and ".svg").
# Most providers map to their own id; a few share an icon upstream.
PROVIDER_ICON_ALIAS = {
    "openai": "codex",
    "moonshot": "kimi",
    "kimik2": "kimi",
}

# Providers that have at least one non-web Linux path (OAuth, API key, CLI,
# local probe). Everything else either requires browser cookies or is gated to
# macOS in the upstream CLI.
LINUX_SUPPORTED = {
    "codex", "claude", "gemini", "copilot", "kilo", "openrouter", "deepseek",
    "moonshot", "codebuff", "zai", "warp", "venice", "crof", "minimax",
    "kimik2", "vertexai", "antigravity",
}

CONFIG_PATH = Path.home() / ".codexbar" / "config.json"
STATE_PATH = Path(
    os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
) / "codexbar-waybar" / "state.json"
ICONS_DIR = Path(
    os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
) / "codexbar-waybar" / "icons"

# CSS mirrors the macOS menu popover: light translucent panel, dark text,
# thin hairline dividers, no card boxes, restrained accent only on the
# active provider tab.
CSS = b"""
/* The window itself stays transparent so the root box can paint rounded corners. */
window.codexbar-popup {
    background-color: transparent;
    background-image: none;
}

.codexbar-root {
    background-color: #ffffff;
    background-image: none;
    color: #111111;
    border-radius: 14px;
    border: 1px solid #d0d0d0;
    padding: 0;
    min-width: 360px;
}

/* Force every child of the root to inherit the white panel (Adwaita ships a lot
   of toolbar/headerbar styling that paints over our background). */
.codexbar-root > * {
    background-color: #ffffff;
    background-image: none;
}

/* --- Tab strip --- */
.codexbar-tabbar {
    background-color: #ffffff;
    padding: 8px 10px 6px 10px;
    border-bottom: 1px solid #e5e5e5;
    border-top-left-radius: 14px;
    border-top-right-radius: 14px;
}
/* Tabs are clickable Boxes (not Gtk.Button) so the GTK theme can't impose
   its own button background. Labels inside inherit the box's colour. */
.codexbar-tab {
    padding: 5px 12px;
    border-radius: 8px;
    color: #6b6b6b;
    font-size: 12px;
    font-weight: 600;
    background-color: transparent;
}
.codexbar-tab:hover {
    background-color: #ececec;
    color: #111111;
}
.codexbar-tab.active,
.codexbar-tab.active:hover {
    background-color: #0a84ff;
    color: #ffffff;
}
.codexbar-tab label { color: inherit; font-size: 12px; font-weight: 600; }

.codexbar-iconbtn {
    padding: 5px 9px;
    border-radius: 8px;
    color: #6b6b6b;
    font-size: 13px;
    background-color: transparent;
}
.codexbar-iconbtn:hover {
    background-color: #ececec;
    color: #111111;
}
.codexbar-iconbtn label { color: inherit; font-size: 13px; }

/* --- Body --- */
.codexbar-body {
    background-color: #ffffff;
    padding: 14px 18px 6px 18px;
}

.codexbar-provider-title {
    font-size: 18px;
    font-weight: 700;
    color: #111111;
}
.codexbar-plan {
    font-size: 11px;
    font-weight: 600;
    color: #6b6b6b;
}
.codexbar-subtitle {
    font-size: 11px;
    color: #6b6b6b;
}
.codexbar-divider {
    background-color: #e5e5e5;
    min-height: 1px;
    margin: 12px 0;
}
.codexbar-section-title {
    font-size: 13px;
    font-weight: 700;
    color: #111111;
    margin-bottom: 6px;
}
.codexbar-section-detail-left {
    font-size: 11px;
    color: #2b2b2b;
    font-feature-settings: "tnum";
}
.codexbar-section-detail-right {
    font-size: 11px;
    color: #6b6b6b;
}
.codexbar-credits {
    font-size: 13px;
    color: #111111;
    font-feature-settings: "tnum";
    font-weight: 600;
}
.codexbar-credits-label {
    font-size: 11px;
    color: #6b6b6b;
}
.codexbar-error {
    font-size: 12px;
    color: #c53030;
}

/* --- Footer --- */
.codexbar-footer {
    background-color: #ffffff;
    padding: 7px 10px 9px 10px;
    border-top: 1px solid #e5e5e5;
    border-bottom-left-radius: 14px;
    border-bottom-right-radius: 14px;
}
.codexbar-footer-btn {
    padding: 4px 10px;
    border-radius: 6px;
    color: #2b2b2b;
    font-size: 12px;
    background-color: transparent;
}
.codexbar-footer-btn:hover {
    background-color: #ececec;
    color: #111111;
}
.codexbar-footer-btn label { color: inherit; font-size: 12px; }

/* --- Settings view --- */
.codexbar-settings-title {
    font-size: 13px;
    font-weight: 600;
    color: #111111;
}
.codexbar-bar-picker {
    background-color: #ffffff;
    padding: 4px 0 8px 0;
}
.codexbar-provider-icon {
    -gtk-icon-size: 18px;
    margin: 0 2px;
}
.codexbar-settings-list {
    background-color: #ffffff;
}
.codexbar-settings-row {
    padding: 8px 0;
    border-bottom: 1px solid #f0f0f0;
}
.codexbar-settings-row.disabled .codexbar-settings-name {
    color: #9a9a9a;
}
.codexbar-settings-name {
    font-size: 13px;
    font-weight: 600;
    color: #111111;
}
.codexbar-settings-hint {
    font-size: 11px;
    color: #9a9a9a;
}
.codexbar-settings-group {
    font-size: 11px;
    font-weight: 600;
    color: #6b6b6b;
    padding: 14px 0 4px 0;
}

/* --- Progress bar: thin pill, gray track, system-blue fill --- */
levelbar.codex-usage {
    background-color: transparent;
}
levelbar.codex-usage trough {
    background-color: transparent;
    background-image: none;
    padding: 0;
    min-height: 4px;
    border: none;
}
levelbar.codex-usage block.filled {
    background-color: #0a84ff;
    background-image: none;
    min-height: 4px;
    border-radius: 2px;
    border: none;
}
levelbar.codex-usage.warning block.filled  { background-color: #ff9f0a; }
levelbar.codex-usage.critical block.filled { background-color: #ff453a; }
levelbar.codex-usage block.empty {
    background-color: #e5e5e5;
    background-image: none;
    min-height: 4px;
    border-radius: 2px;
    border: none;
}
"""


def load_cached() -> list:
    if LAST_GOOD.exists():
        try:
            return json.loads(LAST_GOOD.read_text())
        except json.JSONDecodeError:
            return []
    return []


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


_ICON_CACHE: dict[str, Path] = {}


def resolve_icon_path(pid: str) -> Path | None:
    """Return a recoloured copy of the provider SVG (dark text colour) so it
    renders against the popup's light background. Upstream SVGs use
    `fill=\"white\"`; we substitute that with our theme dark and cache."""
    name = PROVIDER_ICON_ALIAS.get(pid, pid)
    if name in _ICON_CACHE:
        return _ICON_CACHE[name]
    src = ICONS_DIR / f"ProviderIcon-{name}.svg"
    if not src.exists():
        return None
    out_dir = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "codexbar-waybar" / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}.svg"
    try:
        svg = src.read_text()
        # Recolour mask-style SVGs (single white path) to dark theme text.
        recoloured = svg.replace('fill="white"', 'fill="#1c1c1e"') \
                        .replace("fill='white'", "fill='#1c1c1e'") \
                        .replace('fill="#ffffff"', 'fill="#1c1c1e"') \
                        .replace('fill="#FFFFFF"', 'fill="#1c1c1e"')
        out.write_text(recoloured)
        _ICON_CACHE[name] = out
        return out
    except OSError:
        return None


def make_icon(pid: str, size: int = 18) -> Gtk.Widget | None:
    path = resolve_icon_path(pid)
    if path is None:
        return None
    img = Gtk.Image.new_from_file(str(path))
    img.set_pixel_size(size)
    img.add_css_class("codexbar-provider-icon")
    return img


_RESET_SPACE_AFTER = re.compile(r"^([Rr]esets)(?=\S)")
_RESET_SPACE_BEFORE_PAREN = re.compile(r"(?<=\S)\(")
_RESET_SPACE_AFTER_COMMA = re.compile(r",(?=\S)")
_RESET_SPACE_BEFORE_AMPM = re.compile(r"(?<=\d)(?=[AaPp][Mm]\b)")
_RESET_STARTS_WITH_RESETS = re.compile(r"^[Rr]esets")
_RESET_RELATIVE = re.compile(r"^[Rr]esets in ")

RESET_FORMATS = ("provider", "local", "utc")


def normalize_reset_description(text: str) -> str:
    """Mirror codexbar.sh's reset normalisation. Handles both Claude OAuth
    (\"May 17 at 6:20AM\") and Claude CLI (\"Resets6:20am(Europe/Paris)\")
    by inserting the spaces the providers omit."""
    if not text:
        return text
    text = _RESET_SPACE_AFTER.sub(r"\1 ", text)
    text = _RESET_SPACE_BEFORE_PAREN.sub(" (", text)
    text = _RESET_SPACE_AFTER_COMMA.sub(", ", text)
    text = _RESET_SPACE_BEFORE_AMPM.sub(" ", text)
    return text


def current_reset_format(state: dict | None = None) -> str:
    """Resolve the active reset time format. Env var overrides state.json;
    unknown values fall back to `provider` (current behavior)."""
    env = os.environ.get("CODEXBAR_RESET_TIME_FORMAT")
    if env in RESET_FORMATS:
        return env
    if state is None:
        state = load_state()
    value = state.get("resetTimeFormat")
    return value if value in RESET_FORMATS else "provider"


def _from_description(desc: str) -> str:
    if not desc:
        return ""
    return desc if _RESET_STARTS_WITH_RESETS.match(desc) else f"Resets {desc}"


def format_reset_label(window: dict, mode: str) -> str:
    """Render the reset label for a usage window in the chosen format.

    Mirrors `reset_phrase` in codexbar.sh so the popover and tooltip never
    drift. Returns a string like "Resets 6:12 PM CDT" or "" for no info.
    Relative phrases ("Resets in 2 hours") are preserved even in absolute
    modes, since "in 2 hours" is more useful than a wall-clock time.
    """
    clean = normalize_reset_description(window.get("resetDescription") or "")
    from_desc = _from_description(clean)
    if mode == "provider":
        return from_desc
    if _RESET_RELATIVE.match(clean):
        return from_desc
    resets_at = window.get("resetsAt")
    if not resets_at:
        return from_desc
    try:
        ts = datetime.datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return from_desc
    if mode == "utc":
        ts = ts.astimezone(datetime.timezone.utc)
        tz_suffix = "UTC"
    else:
        ts = ts.astimezone()
        tz_suffix = ts.tzname() or ""
    now = datetime.datetime.now(ts.tzinfo)
    if ts.date() == now.date():
        body = ts.strftime("%-I:%M %p")
    elif ts.year == now.year:
        body = ts.strftime("%b %-d at %-I:%M %p")
    else:
        body = ts.strftime("%b %-d %Y at %-I:%M %p")
    return f"Resets {body} {tz_suffix}".rstrip()


def fetch_fresh() -> list:
    try:
        subprocess.run([str(WRAPPER)], check=False, capture_output=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return load_cached()


def max_pct(entry: dict) -> int:
    if entry.get("error"):
        return 0
    usage = entry.get("usage") or {}
    pcts = [
        (usage.get(k) or {}).get("usedPercent")
        for k in ("primary", "secondary", "tertiary")
    ]
    pcts = [p for p in pcts if isinstance(p, (int, float))]
    return int(max(pcts)) if pcts else 0


def default_provider(data: list) -> str | None:
    """Pick the provider with the highest used% as the initial tab."""
    if not data:
        return None
    healthy = [e for e in data if not e.get("error")]
    pool = healthy or data
    return max(pool, key=max_pct).get("provider")


def load_full_config() -> dict:
    """Returns the canonical config (every provider known to the CLI, with the
    current enabled flag merged in). Uses `codexbar config dump` so the schema
    stays in sync with the CLI version that's actually installed."""
    try:
        result = subprocess.run(
            [CODEXBAR, "config", "dump"],
            capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    # Fallback: read whatever's on disk.
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {"providers": [], "version": 1}


def save_config(enabled: dict[str, bool]) -> None:
    """Write only the providers we want enabled. The CLI fills in defaults for
    any provider missing from the file, so we don't need to list disabled ones."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "providers": [{"id": pid, "enabled": True} for pid, on in enabled.items() if on],
        "version": 1,
    }
    CONFIG_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def open_text_file(path: str) -> None:
    """Open a file in a real text editor.

    Resolution order (first hit wins):
      1. $CODEXBAR_EDITOR — explicit override (graphical command line).
      2. $VISUAL / $EDITOR — terminal editor, opened in a detected terminal.
      3. Common GUI editors discovered on PATH.
      4. xdg-open as a last resort (which is what was wrong before — it sends
         JSON to the browser on most setups).
    """
    explicit = os.environ.get("CODEXBAR_EDITOR")
    if explicit:
        subprocess.Popen([*explicit.split(), path])
        return

    gui_editors = [
        "code", "codium", "code-oss",
        "zed",
        "gnome-text-editor", "gedit", "kate", "mousepad", "xed", "leafpad",
        "sublime_text", "subl",
    ]
    for editor in gui_editors:
        which = subprocess.run(["which", editor], capture_output=True, text=True)
        if which.returncode == 0 and which.stdout.strip():
            subprocess.Popen([editor, path])
            return

    terminal_editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if terminal_editor:
        terminals = [
            ("kitty", ["kitty", "-e"]),
            ("alacritty", ["alacritty", "-e"]),
            ("foot", ["foot"]),
            ("wezterm", ["wezterm", "start", "--"]),
            ("gnome-terminal", ["gnome-terminal", "--"]),
            ("konsole", ["konsole", "-e"]),
            ("xterm", ["xterm", "-e"]),
        ]
        for term, cmd in terminals:
            which = subprocess.run(["which", term], capture_output=True, text=True)
            if which.returncode == 0:
                subprocess.Popen([*cmd, *terminal_editor.split(), path])
                return

    # Last resort. Usually opens the browser for .json — which is exactly what
    # we were trying to avoid — but better than silently failing.
    subprocess.Popen(["xdg-open", path])


class CodexBarPopup(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.codexbar.linux.popup")
        self.window: Gtk.Window | None = None
        self.data: list = []
        self.active_pid: str | None = None
        self.tab_buttons: dict[str, Gtk.Button] = {}
        self.view: str = "usage"             # "usage" | "settings"
        self.settings_switches: dict[str, Gtk.Switch] = {}

    def do_activate(self):  # noqa: N802
        if self.window is None:
            self.window = self.build_window()
        self.window.present()

    def _make_pill(self, label: str, css_classes: list[str], on_click,
                   *, icon_pid: str | None = None) -> Gtk.Widget:
        """A clickable pill made from Gtk.Box + Gtk.Label so we bypass
        Gtk.Button styling. Optionally prefixes a provider SVG icon."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_css_classes(css_classes)
        if icon_pid:
            icon = make_icon(icon_pid, size=14)
            if icon is not None:
                box.append(icon)
        lbl = Gtk.Label(label=label)
        box.append(lbl)
        gesture = Gtk.GestureClick()
        gesture.connect("released", lambda _g, _n, _x, _y: on_click())
        box.add_controller(gesture)
        return box

    def build_window(self) -> Gtk.Window:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gtk.Window().get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        win = Gtk.Window(application=self)
        win.add_css_class("codexbar-popup")
        win.set_decorated(False)
        win.set_resizable(False)

        Gtk4LayerShell.init_for_window(win)
        Gtk4LayerShell.set_layer(win, Gtk4LayerShell.Layer.OVERLAY)
        Gtk4LayerShell.set_anchor(win, Gtk4LayerShell.Edge.TOP, True)
        Gtk4LayerShell.set_anchor(win, Gtk4LayerShell.Edge.RIGHT, True)
        Gtk4LayerShell.set_margin(win, Gtk4LayerShell.Edge.TOP, 6)
        Gtk4LayerShell.set_margin(win, Gtk4LayerShell.Edge.RIGHT, 8)
        Gtk4LayerShell.set_keyboard_mode(win, Gtk4LayerShell.KeyboardMode.ON_DEMAND)

        ctrl = Gtk.EventControllerKey()
        ctrl.connect("key-pressed", self._on_key)
        win.add_controller(ctrl)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class("codexbar-root")
        win.set_child(root)

        self.tabbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.tabbar.add_css_class("codexbar-tabbar")
        root.append(self.tabbar)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.body.add_css_class("codexbar-body")
        root.append(self.body)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        footer.add_css_class("codexbar-footer")
        footer.append(self._make_pill("Settings…", ["codexbar-footer-btn"], self._on_settings_call))
        footer.append(Gtk.Box(hexpand=True))
        footer.append(self._make_pill("About", ["codexbar-footer-btn"], self._on_about_call))
        footer.append(self._make_pill("Quit", ["codexbar-footer-btn"], self.quit))
        root.append(footer)

        self.data = load_cached()
        self.active_pid = default_provider(self.data)
        if os.environ.get("CODEXBAR_INITIAL_VIEW") == "settings":
            self.view = "settings"
        self.render()
        self.refresh(background=True)
        return win

    def _on_key(self, _ctl, keyval, _kc, _state):
        if keyval == 0xff1b:  # Escape
            self.quit()
            return True
        return False

    def _on_settings_call(self):
        self.view = "settings"
        self.render()

    def _on_about_call(self):
        subprocess.Popen(["xdg-open", "https://codexbar.app"])

    def _on_settings_back(self):
        self.view = "usage"
        self.render()

    def _on_settings_save(self):
        enabled = {pid: sw.get_active() for pid, sw in self.settings_switches.items()}
        save_config(enabled)
        self.view = "usage"
        self.render()
        self.refresh(background=True)
        # Nudge waybar so the bar reflects the new provider list without
        # waiting for the next interval. The signal is wired up in codexbar.jsonc.
        subprocess.Popen(["pkill", "-RTMIN+8", "waybar"])

    def refresh(self, *, background: bool):
        def worker():
            new_data = fetch_fresh()
            GLib.idle_add(self._apply_refresh, new_data)
        if background:
            Thread(target=worker, daemon=True).start()
        else:
            self._apply_refresh(fetch_fresh())

    def _apply_refresh(self, new_data: list) -> bool:
        self.data = new_data
        if self.active_pid is None or not any(e.get("provider") == self.active_pid for e in new_data):
            self.active_pid = default_provider(new_data)
        self.render()
        return False

    def render(self):
        self._clear(self.tabbar)
        self._clear(self.body)
        if self.view == "settings":
            self._render_settings_header()
            self._render_settings_body()
            return
        self._render_usage_header()
        self._render_usage_body()

    def _render_usage_header(self):
        if not self.data:
            self.tabbar.append(Gtk.Label(label="Loading…"))
            return
        self.tab_buttons.clear()
        for entry in self.data:
            pid = entry.get("provider", "")
            classes = ["codexbar-tab"]
            if pid == self.active_pid:
                classes.append("active")
            pill = self._make_pill(
                PROVIDER_NAMES.get(pid, pid.title()),
                classes,
                lambda p=pid: self._select(p),
                icon_pid=pid)
            self.tabbar.append(pill)
            self.tab_buttons[pid] = pill
        self.tabbar.append(Gtk.Box(hexpand=True))
        self.tabbar.append(self._make_pill(
            "↻", ["codexbar-iconbtn"], lambda: self.refresh(background=True)))
        self.tabbar.append(self._make_pill(
            "✕", ["codexbar-iconbtn"], self.quit))

    def _render_usage_body(self):
        if not self.data:
            return
        active = next((e for e in self.data if e.get("provider") == self.active_pid), None)
        if active is None:
            return
        self._render_provider(active)

    def _render_settings_header(self):
        back = self._make_pill("← Back", ["codexbar-tab"], self._on_settings_back)
        self.tabbar.append(back)
        title = Gtk.Label(label="Settings", xalign=0.0, hexpand=True)
        title.add_css_class("codexbar-settings-title")
        self.tabbar.append(title)
        save = self._make_pill("Save", ["codexbar-tab", "active"], self._on_settings_save)
        self.tabbar.append(save)

    def _render_settings_body(self):
        self.settings_switches.clear()
        cfg = load_full_config()
        existing = {p.get("id"): bool(p.get("enabled")) for p in cfg.get("providers", [])}

        # --- Section: which provider shows in the bar ---
        bar_title = Gtk.Label(label="Show in bar", xalign=0.0)
        bar_title.add_css_class("codexbar-section-title")
        self.body.append(bar_title)
        bar_hint = Gtk.Label(
            label="Pick a provider to pin to the bar (session • weekly), or leave on Highest.",
            xalign=0.0, wrap=True, max_width_chars=44)
        bar_hint.add_css_class("codexbar-subtitle")
        self.body.append(bar_hint)
        self.body.append(self._build_bar_provider_picker(existing))

        # Divider between sections.
        self.body.append(self._divider())

        # --- Section: reset time format ---
        reset_title = Gtk.Label(label="Reset times", xalign=0.0)
        reset_title.add_css_class("codexbar-section-title")
        self.body.append(reset_title)
        reset_hint = Gtk.Label(
            label="How to render the “Resets …” label. Provider keeps the raw "
                  "string each backend emits; Local/UTC reformat the reset "
                  "timestamp with an explicit timezone.",
            xalign=0.0, wrap=True, max_width_chars=44)
        reset_hint.add_css_class("codexbar-subtitle")
        self.body.append(reset_hint)
        self.body.append(self._build_reset_format_picker())

        # Divider between sections.
        self.body.append(self._divider())

        # --- Section: enabled providers ---
        section_title = Gtk.Label(label="Providers", xalign=0.0)
        section_title.add_css_class("codexbar-section-title")
        self.body.append(section_title)
        section_hint = Gtk.Label(
            label="Toggle which providers feed the bar and the popup.",
            xalign=0.0, wrap=True)
        section_hint.add_css_class("codexbar-subtitle")
        self.body.append(section_hint)

        # Scrollable list.
        scroller = Gtk.ScrolledWindow()
        scroller.set_min_content_height(280)
        scroller.set_propagate_natural_width(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        list_box.add_css_class("codexbar-settings-list")
        scroller.set_child(list_box)
        self.body.append(scroller)

        # Linux-supported first, alphabetised; then unsupported with hint.
        provider_ids = [p.get("id") for p in cfg.get("providers", [])]
        supported = sorted(p for p in provider_ids if p in LINUX_SUPPORTED)
        unsupported = sorted(p for p in provider_ids if p not in LINUX_SUPPORTED)

        for pid in supported:
            list_box.append(self._settings_row(pid, existing.get(pid, False), enabled_ui=True))

        if unsupported:
            divider_label = Gtk.Label(label="macOS-only providers", xalign=0.0)
            divider_label.add_css_class("codexbar-settings-group")
            list_box.append(divider_label)
            for pid in unsupported:
                list_box.append(self._settings_row(pid, existing.get(pid, False), enabled_ui=False))

        # Footer note.
        note = Gtk.Label(
            label=f"Config: {CONFIG_PATH}",
            xalign=0.0, wrap=True)
        note.add_css_class("codexbar-subtitle")
        self.body.append(note)

    def _build_bar_provider_picker(self, existing: dict[str, bool]) -> Gtk.Widget:
        wrap = Gtk.FlowBox()
        wrap.add_css_class("codexbar-bar-picker")
        wrap.set_selection_mode(Gtk.SelectionMode.NONE)
        wrap.set_homogeneous(False)
        wrap.set_max_children_per_line(8)
        current = load_state().get("barProvider")

        def make_chip(pid: str | None, label: str):
            classes = ["codexbar-tab"]
            if pid == current or (pid is None and not current):
                classes.append("active")
            chip = self._make_pill(
                label, classes,
                lambda p=pid: self._on_bar_provider_change(p),
                icon_pid=pid)
            return chip

        wrap.append(make_chip(None, "Highest"))
        enabled_pids = [pid for pid, on in existing.items() if on and pid in LINUX_SUPPORTED]
        for pid in enabled_pids:
            wrap.append(make_chip(pid, PROVIDER_NAMES.get(pid, pid.title())))
        return wrap

    def _on_bar_provider_change(self, pid: str | None):
        state = load_state()
        if pid is None:
            state.pop("barProvider", None)
        else:
            state["barProvider"] = pid
        save_state(state)
        # Re-render so the active chip highlight tracks the click.
        self.render()
        # Nudge waybar so the bar text updates immediately.
        subprocess.Popen(["pkill", "-RTMIN+8", "waybar"])

    def _build_reset_format_picker(self) -> Gtk.Widget:
        wrap = Gtk.FlowBox()
        wrap.add_css_class("codexbar-bar-picker")
        wrap.set_selection_mode(Gtk.SelectionMode.NONE)
        wrap.set_homogeneous(False)
        wrap.set_max_children_per_line(8)
        current = current_reset_format()
        labels = (("provider", "Provider"), ("local", "Local"), ("utc", "UTC"))
        for value, label in labels:
            classes = ["codexbar-tab"]
            if value == current:
                classes.append("active")
            wrap.append(self._make_pill(
                label, classes,
                lambda v=value: self._on_reset_format_change(v)))
        return wrap

    def _on_reset_format_change(self, value: str):
        state = load_state()
        if value == "provider":
            state.pop("resetTimeFormat", None)
        else:
            state["resetTimeFormat"] = value
        save_state(state)
        # Re-render the Settings view so the chip highlight tracks the click,
        # and signal waybar so the tooltip picks up the new format on its
        # next refresh.
        self.render()
        subprocess.Popen(["pkill", "-RTMIN+8", "waybar"])

    def _settings_row(self, pid: str, enabled: bool, *, enabled_ui: bool) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("codexbar-settings-row")
        if not enabled_ui:
            row.add_css_class("disabled")

        icon = make_icon(pid, size=18)
        if icon is not None:
            row.append(icon)

        name = Gtk.Label(label=PROVIDER_NAMES.get(pid, pid.title()), xalign=0.0, hexpand=True)
        name.add_css_class("codexbar-settings-name")
        row.append(name)

        if not enabled_ui:
            hint = Gtk.Label(label="macOS only", xalign=1.0)
            hint.add_css_class("codexbar-settings-hint")
            row.append(hint)

        switch = Gtk.Switch()
        switch.set_active(enabled)
        switch.set_sensitive(enabled_ui)
        switch.set_valign(Gtk.Align.CENTER)
        row.append(switch)
        self.settings_switches[pid] = switch
        return row

    def _select(self, pid: str):
        if pid == self.active_pid:
            return
        self.active_pid = pid
        self.render()

    def _render_provider(self, entry: dict):
        pid = entry.get("provider", "?")
        usage = entry.get("usage") or {}
        identity = usage.get("identity") or {}
        email = usage.get("accountEmail") or identity.get("accountEmail")
        login_method = identity.get("loginMethod") or usage.get("loginMethod")

        # Header row.
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label=PROVIDER_NAMES.get(pid, pid.title()), xalign=0.0, hexpand=True)
        title.add_css_class("codexbar-provider-title")
        header.append(title)
        if login_method:
            plan = Gtk.Label(label=str(login_method).title(), xalign=1.0)
            plan.add_css_class("codexbar-plan")
            header.append(plan)
        self.body.append(header)

        # Subtitle line (status / updated / stale).
        sub_text = "Updated just now"
        if entry.get("stale"):
            sub_text = "Cached — last refresh failed"
        elif entry.get("error"):
            sub_text = "Refresh failed"
        sub_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sub = Gtk.Label(label=sub_text, xalign=0.0, hexpand=True)
        sub.add_css_class("codexbar-subtitle")
        sub_row.append(sub)
        if email:
            email_label = Gtk.Label(label=email, xalign=1.0)
            email_label.add_css_class("codexbar-subtitle")
            sub_row.append(email_label)
        self.body.append(sub_row)

        if entry.get("error"):
            self.body.append(self._divider())
            err = Gtk.Label(
                label=entry["error"].get("message", "Unknown error"),
                xalign=0.0,
                wrap=True,
                max_width_chars=44)
            err.add_css_class("codexbar-error")
            self.body.append(err)
            return

        # Usage windows.
        rendered_any = False
        for key in ("primary", "secondary", "tertiary"):
            window = usage.get(key)
            if not window:
                continue
            self.body.append(self._divider())
            self.body.append(self._section(WINDOW_LABELS.get(key, key.title()), window))
            rendered_any = True

        # Credits (when provider exposes it).
        credits = entry.get("credits") or {}
        remaining = credits.get("remaining")
        if isinstance(remaining, (int, float)):
            self.body.append(self._divider())
            credit_title = Gtk.Label(label="Credits", xalign=0.0)
            credit_title.add_css_class("codexbar-section-title")
            self.body.append(credit_title)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            val = Gtk.Label(label=f"${remaining:,.2f}", xalign=0.0, hexpand=True)
            val.add_css_class("codexbar-credits")
            row.append(val)
            lbl = Gtk.Label(label="remaining", xalign=1.0)
            lbl.add_css_class("codexbar-credits-label")
            row.append(lbl)
            self.body.append(row)
            rendered_any = True

        if not rendered_any:
            self.body.append(self._divider())
            empty = Gtk.Label(label="No usage data for this provider.", xalign=0.0)
            empty.add_css_class("codexbar-subtitle")
            self.body.append(empty)

    def _divider(self) -> Gtk.Widget:
        d = Gtk.Box()
        d.add_css_class("codexbar-divider")
        return d

    def _section(self, title: str, window: dict) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        t = Gtk.Label(label=title, xalign=0.0)
        t.add_css_class("codexbar-section-title")
        box.append(t)

        pct = window.get("usedPercent")
        bar = Gtk.LevelBar()
        bar.add_css_class("codex-usage")
        bar.set_min_value(0)
        bar.set_max_value(100)
        bar.set_value(float(pct) if isinstance(pct, (int, float)) else 0)
        if isinstance(pct, (int, float)):
            if pct >= 90:
                bar.add_css_class("critical")
            elif pct >= 70:
                bar.add_css_class("warning")
        box.append(bar)

        details = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        left_text = (
            f"{int(pct)}% used"
            if isinstance(pct, (int, float))
            else "—"
        )
        left = Gtk.Label(label=left_text, xalign=0.0, hexpand=True)
        left.add_css_class("codexbar-section-detail-left")
        details.append(left)

        reset_text = format_reset_label(window, current_reset_format())
        if reset_text:
            r = Gtk.Label(label=reset_text, xalign=1.0)
            r.add_css_class("codexbar-section-detail-right")
            details.append(r)
        box.append(details)
        return box

    def _clear(self, container: Gtk.Box):
        child = container.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            container.remove(child)
            child = nxt


def main():
    pidfile = CACHE / "popup.pid"
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            pidfile.unlink(missing_ok=True)
            return 0
        except (ValueError, ProcessLookupError, PermissionError):
            pidfile.unlink(missing_ok=True)

    CACHE.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(os.getpid()))
    try:
        app = CodexBarPopup()
        return app.run([])
    finally:
        pidfile.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
