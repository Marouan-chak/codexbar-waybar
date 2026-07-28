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
    "abacus": "Abacus AI",
    "alibaba": "Alibaba",
    "alibabatokenplan": "Alibaba Token Plan",
    "amp": "Amp",
    "antigravity": "Antigravity",
    "augment": "Augment",
    "azureopenai": "Azure OpenAI",
    "bedrock": "AWS Bedrock",
    "chutes": "Chutes",
    "clawrouter": "ClawRouter",
    "codebuff": "Codebuff",
    "codex": "Codex",
    "claude": "Claude",
    "commandcode": "Command Code",
    "copilot": "Copilot",
    "crof": "Crof",
    "crossmodel": "CrossModel",
    "cursor": "Cursor",
    "deepgram": "Deepgram",
    "deepseek": "DeepSeek",
    "devin": "Devin",
    "doubao": "Doubao",
    "elevenlabs": "ElevenLabs",
    "factory": "Droid",
    "gemini": "Gemini",
    "grok": "Grok",
    "groq": "Groq",
    "jetbrains": "JetBrains AI",
    "kilo": "Kilo",
    "kimi": "Kimi",
    "kimik2": "Kimi K2",
    "kiro": "Kiro",
    "litellm": "LiteLLM",
    "llmproxy": "LLM Proxy",
    "manus": "Manus",
    "mimo": "Xiaomi MiMo",
    "minimax": "MiniMax",
    "mistral": "Mistral",
    "moonshot": "Moonshot / Kimi API",
    "ollama": "Ollama",
    "openai": "OpenAI",
    "opencode": "OpenCode",
    "opencodego": "OpenCode Go",
    "openrouter": "OpenRouter",
    "perplexity": "Perplexity",
    "poe": "Poe",
    "qoder": "Qoder",
    "sakana": "Sakana AI",
    "stepfun": "StepFun",
    "synthetic": "Synthetic",
    "t3chat": "T3 Chat",
    "venice": "Venice",
    "vertexai": "Vertex AI",
    "warp": "Warp",
    "windsurf": "Windsurf",
    "zai": "z.ai",
    "zed": "Zed",
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
    "azureopenai": "codex",
    "alibabatokenplan": "alibaba",
    "moonshot": "kimi",
    "kimik2": "kimi",
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


def provider_label(pid: str) -> str:
    return PROVIDER_NAMES.get(pid, pid.replace("-", " ").title())


def entry_key(entry: dict) -> str:
    provider = entry.get("provider") or "unknown"
    account = entry.get("account")
    if account:
        return f"{provider}\0{account}"
    identity = ((entry.get("usage") or {}).get("identity") or {})
    identity_account = identity.get("accountEmail") or identity.get("accountOrganization")
    if identity_account:
        return f"{provider}\0{identity_account}"
    return str(provider)


def entry_label(entry: dict, all_entries: list | None = None) -> str:
    pid = str(entry.get("provider") or "unknown")
    label = provider_label(pid)
    if all_entries is not None:
        duplicates = sum(1 for other in all_entries if other.get("provider") == pid)
        if duplicates <= 1:
            return label
    account = entry.get("account")
    identity = ((entry.get("usage") or {}).get("identity") or {})
    account = account or identity.get("accountEmail") or identity.get("accountOrganization")
    return f"{label} · {account}" if account else label


def money(value: float, currency: str = "USD") -> str:
    symbol = "$" if currency.upper() == "USD" else f"{currency.upper()} "
    return f"{symbol}{value:,.2f}"


def compact_number(value: int | float) -> str:
    if isinstance(value, int) or float(value).is_integer():
        return f"{int(value):,}"
    return f"{float(value):,.2f}"


def parse_iso_datetime(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def format_datetime_label(value: str | None) -> str:
    ts = parse_iso_datetime(value)
    if ts is None:
        return ""
    ts = ts.astimezone()
    now = datetime.datetime.now(ts.tzinfo)
    if ts.date() == now.date():
        return ts.strftime("%-I:%M %p %Z")
    if ts.year == now.year:
        return ts.strftime("%b %-d at %-I:%M %p %Z")
    return ts.strftime("%b %-d %Y at %-I:%M %p %Z")


def summarize_status(entry: dict) -> str | None:
    status = entry.get("status")
    if not isinstance(status, dict):
        return None
    indicator = status.get("indicator")
    description = status.get("description")
    if not indicator and not description:
        return None
    label = str(indicator or "unknown").replace("_", " ").title()
    return f"{label}: {description}" if description else label


def summarize_pace(entry: dict) -> list[str]:
    pace = entry.get("pace")
    if not isinstance(pace, dict):
        return []
    lines: list[str] = []
    for key, title in (("primary", "Session pace"), ("secondary", "Weekly pace")):
        data = pace.get(key)
        if isinstance(data, dict) and data.get("summary"):
            lines.append(f"{title}: {data['summary']}")
    return lines


def summarize_provider_cost(usage: dict) -> str | None:
    cost = usage.get("providerCost")
    if not isinstance(cost, dict):
        return None
    used = cost.get("used")
    limit = cost.get("limit")
    currency = str(cost.get("currencyCode") or "USD")
    period = cost.get("period") or "Budget"
    if isinstance(used, (int, float)) and isinstance(limit, (int, float)) and limit > 0:
        return f"{period}: {money(float(used), currency)} / {money(float(limit), currency)}"
    if isinstance(used, (int, float)):
        return f"{period}: {money(float(used), currency)} used"
    return None


def summarize_reset_credits(usage: dict) -> str | None:
    snapshot = usage.get("codexResetCredits")
    if not isinstance(snapshot, dict):
        return None
    credits = [
        c for c in snapshot.get("credits", [])
        if isinstance(c, dict) and c.get("status") == "available"
    ]
    available = snapshot.get("availableCount")
    count = int(available) if isinstance(available, int) else len(credits)
    expiring = sorted(
        (c for c in credits if c.get("expires_at")),
        key=lambda c: c.get("expires_at") or "")
    if expiring:
        expiry = format_datetime_label(expiring[0].get("expires_at"))
        if expiry:
            return f"{count} available; next expires {expiry}"
    if count:
        return f"{count} available; no expiry"
    return "None available"


def summarize_credit_limit(limit: dict | None) -> str | None:
    if not isinstance(limit, dict):
        return None
    title = limit.get("title") or "Monthly credit limit"
    used = limit.get("used")
    cap = limit.get("limit")
    remaining = limit.get("remaining")
    if isinstance(used, (int, float)) and isinstance(cap, (int, float)) and cap > 0:
        return f"{title}: {money(float(used))} / {money(float(cap))}"
    if isinstance(remaining, (int, float)):
        return f"{title}: {money(float(remaining))} remaining"
    return None


def summarize_openai_dashboard(entry: dict) -> list[str]:
    dashboard = entry.get("openaiDashboard")
    if not isinstance(dashboard, dict):
        return []
    lines: list[str] = []
    account_plan = dashboard.get("accountPlan")
    if account_plan:
        lines.append(f"Plan: {account_plan}")
    credits = dashboard.get("creditsRemaining")
    if isinstance(credits, (int, float)):
        lines.append(f"Dashboard credits: {money(float(credits))}")
    limit = summarize_credit_limit(dashboard.get("codexCreditLimit"))
    if limit:
        lines.append(limit)
    breakdown = dashboard.get("usageBreakdown") or dashboard.get("dailyBreakdown") or []
    if isinstance(breakdown, list) and breakdown:
        recent = breakdown[:7]
        total = sum(
            float(day.get("totalCreditsUsed", 0))
            for day in recent
            if isinstance(day, dict) and isinstance(day.get("totalCreditsUsed"), (int, float))
        )
        if total > 0:
            lines.append(f"Recent dashboard spend: {money(total)} over {len(recent)} days")
    return lines


def summarize_usage_details(usage: dict) -> list[str]:
    lines: list[str] = []
    cost = summarize_provider_cost(usage)
    if cost:
        lines.append(cost)

    openrouter = usage.get("openRouterUsage")
    if isinstance(openrouter, dict):
        balance = openrouter.get("balance")
        total_usage = openrouter.get("totalUsage")
        if isinstance(balance, (int, float)):
            text = f"OpenRouter balance: {money(float(balance))}"
            if isinstance(total_usage, (int, float)):
                text += f" ({money(float(total_usage))} used)"
            lines.append(text)

    sakana = usage.get("sakanaPayAsYouGo")
    if isinstance(sakana, dict):
        balance = sakana.get("creditBalance")
        total = sakana.get("periodUsageTotal")
        if isinstance(balance, (int, float)):
            text = f"Pay-as-you-go balance: {money(float(balance))}"
            if isinstance(total, (int, float)):
                text += f"; {money(float(total))} used"
            lines.append(text)

    openai_api = usage.get("openAIAPIUsage")
    if isinstance(openai_api, dict):
        daily = openai_api.get("daily")
        if isinstance(daily, list) and daily:
            total_cost = sum(
                float(day.get("costUSD", 0))
                for day in daily
                if isinstance(day, dict) and isinstance(day.get("costUSD"), (int, float))
            )
            requests = sum(
                int(day.get("requests", 0))
                for day in daily
                if isinstance(day, dict) and isinstance(day.get("requests"), int)
            )
            lines.append(f"API history: {money(total_cost)} across {requests:,} requests")

    reset_credits = summarize_reset_credits(usage)
    if reset_credits:
        lines.append(f"Reset credits: {reset_credits}")

    if usage.get("commandCodeSubscriptionEnrichmentUnavailable"):
        lines.append("Subscription lookup unavailable")
    if usage.get("commandCodeMonthlyGrantDepleted"):
        lines.append("Monthly grant depleted")

    expires = format_datetime_label(usage.get("subscriptionExpiresAt"))
    renews = format_datetime_label(usage.get("subscriptionRenewsAt"))
    if renews:
        lines.append(f"Subscription renews {renews}")
    elif expires:
        lines.append(f"Subscription expires {expires}")
    return lines


def default_provider(data: list, state: dict | None = None) -> str | None:
    """Pick the configured popup provider, or the highest used% as fallback."""
    if not data:
        return None
    if state is None:
        state = load_state()
    preferred = state.get("popupProvider")
    if isinstance(preferred, str) and preferred:
        matches = [entry for entry in data if entry.get("provider") == preferred]
        if matches:
            healthy_matches = [entry for entry in matches if not entry.get("error")]
            return entry_key((healthy_matches or matches)[0])
    healthy = [entry for entry in data if not entry.get("error")]
    pool = healthy or data
    return entry_key(max(pool, key=max_pct))

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
        if self.active_pid is None or not any(entry_key(e) == self.active_pid for e in new_data):
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
            key = entry_key(entry)
            classes = ["codexbar-tab"]
            if key == self.active_pid:
                classes.append("active")
            pill = self._make_pill(
                entry_label(entry, self.data),
                classes,
                lambda k=key: self._select(k),
                icon_pid=pid)
            self.tabbar.append(pill)
            self.tab_buttons[key] = pill
        self.tabbar.append(Gtk.Box(hexpand=True))
        self.tabbar.append(self._make_pill(
            "↻", ["codexbar-iconbtn"], lambda: self.refresh(background=True)))
        self.tabbar.append(self._make_pill(
            "✕", ["codexbar-iconbtn"], self.quit))

    def _render_usage_body(self):
        if not self.data:
            return
        active = next((e for e in self.data if entry_key(e) == self.active_pid), None)
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
        self.body.append(self._divider())

        # --- Section: which provider is selected when the popup opens ---
        popup_title = Gtk.Label(label="Open popup on", xalign=0.0)
        popup_title.add_css_class("codexbar-section-title")
        self.body.append(popup_title)
        popup_hint = Gtk.Label(
            label="Choose the provider tab selected when the popup opens, or use Highest.",
            xalign=0.0, wrap=True, max_width_chars=44)
        popup_hint.add_css_class("codexbar-subtitle")
        self.body.append(popup_hint)
        self.body.append(self._build_popup_provider_picker(existing))

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

        # The Linux CLI's config dump is authoritative. If upstream exposes a
        # provider there, keep it selectable here.
        provider_ids = [p.get("id") for p in cfg.get("providers", [])]
        supported = sorted(p for p in provider_ids if isinstance(p, str))

        for pid in supported:
            list_box.append(self._settings_row(pid, existing.get(pid, False), enabled_ui=True))

        # Footer note.
        note = Gtk.Label(
            label=f"Config: {CONFIG_PATH}",
            xalign=0.0, wrap=True)
        note.add_css_class("codexbar-subtitle")
        self.body.append(note)

    def _build_provider_picker(self, existing: dict[str, bool], current: str | None,
                               on_change) -> Gtk.Widget:
        wrap = Gtk.FlowBox()
        wrap.add_css_class("codexbar-bar-picker")
        wrap.set_selection_mode(Gtk.SelectionMode.NONE)
        wrap.set_homogeneous(False)
        wrap.set_max_children_per_line(8)

        def make_chip(pid: str | None, label: str):
            classes = ["codexbar-tab"]
            if pid == current or (pid is None and not current):
                classes.append("active")
            return self._make_pill(
                label, classes,
                lambda p=pid: on_change(p),
                icon_pid=pid)

        wrap.append(make_chip(None, "Highest"))
        enabled_pids = [pid for pid, enabled in existing.items() if enabled]
        for pid in enabled_pids:
            wrap.append(make_chip(pid, provider_label(pid)))
        return wrap

    def _build_bar_provider_picker(self, existing: dict[str, bool]) -> Gtk.Widget:
        current = load_state().get("barProvider")
        return self._build_provider_picker(existing, current, self._on_bar_provider_change)

    def _build_popup_provider_picker(self, existing: dict[str, bool]) -> Gtk.Widget:
        enabled_pids = [pid for pid, enabled in existing.items() if enabled]
        current = load_state().get("popupProvider")
        if current not in enabled_pids:
            current = None
        return self._build_provider_picker(existing, current, self._on_popup_provider_change)

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

    def _on_popup_provider_change(self, pid: str | None):
        state = load_state()
        if pid is None:
            state.pop("popupProvider", None)
        else:
            state["popupProvider"] = pid
        save_state(state)
        # Re-render so the active chip highlight tracks the click.
        self.render()

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

        name = Gtk.Label(label=provider_label(pid), xalign=0.0, hexpand=True)
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

    def _select(self, key: str):
        if key == self.active_pid:
            return
        self.active_pid = key
        self.render()

    def _render_provider(self, entry: dict):
        pid = entry.get("provider", "?")
        usage = entry.get("usage") or {}
        identity = usage.get("identity") or {}
        email = usage.get("accountEmail") or identity.get("accountEmail")
        login_method = identity.get("loginMethod") or usage.get("loginMethod")

        # Header row.
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label=provider_label(pid), xalign=0.0, hexpand=True)
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
        else:
            status_text = summarize_status(entry)
            if status_text:
                sub_text = status_text
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

        for item in usage.get("extraRateWindows") or []:
            if not isinstance(item, dict):
                continue
            window = item.get("window")
            if not isinstance(window, dict):
                continue
            self.body.append(self._divider())
            title = item.get("title") or item.get("id") or "Extra quota"
            self.body.append(self._section(str(title), window, usage_known=item.get("usageKnown", True)))
            rendered_any = True

        # Credits (when provider exposes it).
        credits = entry.get("credits") or {}
        remaining = credits.get("remaining")
        credit_lines = []
        if isinstance(remaining, (int, float)):
            credit_lines.append((money(float(remaining)), "remaining"))
        limit_line = summarize_credit_limit(credits.get("codexCreditLimit"))
        if limit_line:
            credit_lines.append((limit_line, ""))
        if credit_lines:
            self.body.append(self._divider())
            credit_title = Gtk.Label(label="Credits", xalign=0.0)
            credit_title.add_css_class("codexbar-section-title")
            self.body.append(credit_title)
            for value, label in credit_lines:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                val = Gtk.Label(label=value, xalign=0.0, hexpand=True)
                val.add_css_class("codexbar-credits")
                row.append(val)
                if label:
                    lbl = Gtk.Label(label=label, xalign=1.0)
                    lbl.add_css_class("codexbar-credits-label")
                    row.append(lbl)
                self.body.append(row)
            rendered_any = True

        detail_lines = [
            *summarize_pace(entry),
            *summarize_openai_dashboard(entry),
            *summarize_usage_details(usage),
        ]
        plan_info = entry.get("antigravityPlanInfo")
        if isinstance(plan_info, dict):
            plan = (
                plan_info.get("planDisplayName")
                or plan_info.get("displayName")
                or plan_info.get("planShortName")
                or plan_info.get("planName")
            )
            if plan:
                detail_lines.append(f"Plan: {plan}")
        if detail_lines:
            self.body.append(self._divider())
            details_title = Gtk.Label(label="Details", xalign=0.0)
            details_title.add_css_class("codexbar-section-title")
            self.body.append(details_title)
            for line in detail_lines:
                detail = Gtk.Label(label=line, xalign=0.0, wrap=True, max_width_chars=48)
                detail.add_css_class("codexbar-subtitle")
                self.body.append(detail)
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

    def _section(self, title: str, window: dict, *, usage_known: bool = True) -> Gtk.Widget:
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
        if not usage_known:
            left_text = "Reset tracked"
        elif isinstance(pct, (int, float)):
            regen = window.get("nextRegenPercent")
            left_text = f"{int(pct)}% used"
            if isinstance(regen, (int, float)) and regen > 0:
                left_text += f" · +{compact_number(regen)}% next regen"
        else:
            left_text = "—"
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
