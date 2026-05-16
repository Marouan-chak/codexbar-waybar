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
}

WINDOW_LABELS = {
    "primary": "Session",
    "secondary": "Weekly",
    "tertiary": "Monthly",
}

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


class CodexBarPopup(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.codexbar.linux.popup")
        self.window: Gtk.Window | None = None
        self.data: list = []
        self.active_pid: str | None = None
        self.tab_buttons: dict[str, Gtk.Button] = {}

    def do_activate(self):  # noqa: N802
        if self.window is None:
            self.window = self.build_window()
        self.window.present()

    def _make_pill(self, label: str, css_classes: list[str], on_click) -> Gtk.Widget:
        """A clickable pill made from Gtk.Box + Gtk.Label so we bypass
        Gtk.Button styling. Returns the box."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.set_css_classes(css_classes)
        lbl = Gtk.Label(label=label)
        box.append(lbl)
        gesture = Gtk.GestureClick()
        gesture.connect("released", lambda _g, _n, _x, _y: on_click())
        box.add_controller(gesture)
        # Pointer cursor on hover.
        box.set_cursor(Gtk.Window().get_display().__class__ and None)  # noqa: just leave default
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
        self.render()
        self.refresh(background=True)
        return win

    def _on_key(self, _ctl, keyval, _kc, _state):
        if keyval == 0xff1b:  # Escape
            self.quit()
            return True
        return False

    def _on_settings(self, _btn):
        self._on_settings_call()

    def _on_about(self, _btn):
        self._on_about_call()

    def _on_settings_call(self):
        path = Path.home() / ".codexbar" / "config.json"
        subprocess.Popen(["xdg-open", str(path)])

    def _on_about_call(self):
        subprocess.Popen(["xdg-open", "https://codexbar.app"])

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

        if not self.data:
            self.tabbar.append(Gtk.Label(label="Loading…"))
            return

        # Tab strip.
        self.tab_buttons.clear()
        for entry in self.data:
            pid = entry.get("provider", "")
            classes = ["codexbar-tab"]
            if pid == self.active_pid:
                classes.append("active")
            pill = self._make_pill(
                PROVIDER_NAMES.get(pid, pid.title()),
                classes,
                lambda p=pid: self._select(p))
            self.tabbar.append(pill)
            self.tab_buttons[pid] = pill
        # Right side: refresh + close.
        self.tabbar.append(Gtk.Box(hexpand=True))
        self.tabbar.append(self._make_pill(
            "↻", ["codexbar-iconbtn"], lambda: self.refresh(background=True)))
        self.tabbar.append(self._make_pill(
            "✕", ["codexbar-iconbtn"], self.quit))

        # Body: active provider card.
        active = next((e for e in self.data if e.get("provider") == self.active_pid), None)
        if active is None:
            return
        self._render_provider(active)

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

        reset = window.get("resetDescription") or ""
        if reset:
            reset_text = reset if reset.lower().startswith("reset") else f"Resets {reset}"
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
