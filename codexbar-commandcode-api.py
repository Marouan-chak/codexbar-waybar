#!/usr/bin/env python3
"""Linux API usage reader for Command Code.

Reads a Command Code Cookie header from env/config and emits one
CodexBar-compatible JSON array entry.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROVIDER = "commandcode"
SOURCE = "api"
API_BASE = "https://api.commandcode.ai"
WEB_ORIGIN = "https://commandcode.ai"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
PLANS = {
    "individual-go": ("Go", 10.0),
    "individual-pro": ("Pro", 30.0),
    "individual-max": ("Max", 150.0),
    "individual-ultra": ("Ultra", 300.0),
}


def emit(entry: dict) -> int:
    print(json.dumps([entry], separators=(",", ":")))
    return 0


def error(message: str, *, kind: str = "provider", code: int = 1) -> int:
    return emit({
        "provider": PROVIDER,
        "source": SOURCE,
        "error": {"kind": kind, "code": code, "message": message},
    })


def normalize_cookie_header(raw: str | None) -> str:
    if not raw:
        return ""
    text = raw.strip()
    if not text:
        return ""
    for line in text.splitlines():
        candidate = line.strip().strip("'\"")
        if candidate.lower().startswith("cookie:"):
            return candidate.split(":", 1)[1].strip()
    match = re.search(r"cookie:\s*([^'\"]+)", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text


def read_cookie_file(path: str | None) -> str:
    if not path:
        return ""
    try:
        return Path(path).expanduser().read_text()
    except OSError:
        return ""


def cookie_from_codexbar_config() -> str:
    path = Path(os.environ.get("CODEXBAR_CONFIG", str(Path.home() / ".codexbar/config.json")))
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    providers = data.get("providers")
    if not isinstance(providers, list):
        return ""
    for provider in providers:
        if not isinstance(provider, dict) or provider.get("id") != PROVIDER:
            continue
        for key in ("cookieHeader", "manualCookieHeader", "cookie"):
            value = provider.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def read_cookie_header() -> str:
    sources = (
        os.environ.get("CODEXBAR_COMMANDCODE_COOKIE"),
        os.environ.get("COMMANDCODE_COOKIE_HEADER"),
        read_cookie_file(os.environ.get("CODEXBAR_COMMANDCODE_COOKIE_FILE")),
        read_cookie_file(os.environ.get("COMMANDCODE_COOKIE_FILE")),
        cookie_from_codexbar_config(),
    )
    for source in sources:
        cookie = normalize_cookie_header(source)
        if cookie:
            return cookie
    return ""


def load_json(url: str, cookie: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Cookie": cookie,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": USER_AGENT,
            "Origin": WEB_ORIGIN,
            "Referer": f"{WEB_ORIGIN}/",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError("Command Code session cookie is invalid or expired.") from exc
        raise RuntimeError(f"Command Code API error: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Command Code network error: {exc.reason}") from exc

    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Command Code parse error: invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Command Code parse error: expected JSON object")
    return value


def as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def parse_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso_utc(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def format_usd(value: float) -> str:
    return f"${value:,.2f}" if abs(value) < 100 else f"${value:,.0f}"


def parse_credits(payload: dict[str, Any]) -> dict[str, float]:
    credits = payload.get("credits")
    if not isinstance(credits, dict):
        raise RuntimeError("Command Code parse error: missing credits object")
    monthly = as_float(credits.get("monthlyCredits"))
    if monthly is None:
        raise RuntimeError("Command Code parse error: missing monthlyCredits")
    return {
        "monthly": monthly,
        "purchased": as_float(credits.get("purchasedCredits")) or 0.0,
        "premium": as_float(credits.get("premiumMonthlyCredits")) or 0.0,
        "opensource": as_float(credits.get("opensourceMonthlyCredits")) or 0.0,
    }


def parse_subscription(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not payload.get("success"):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    plan_id = data.get("planId")
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise RuntimeError("Command Code parse error: missing planId")
    return {
        "planID": plan_id.strip().lower(),
        "status": str(data.get("status") or "unknown"),
        "currentPeriodEnd": parse_time(data.get("currentPeriodEnd")),
    }


def entry_from_payloads(credits_payload: dict[str, Any], subscription_payload: dict[str, Any]) -> dict:
    credits = parse_credits(credits_payload)
    subscription = parse_subscription(subscription_payload)
    plan_name = None
    total = None
    period_end = None
    status = None
    if subscription:
        status = subscription["status"]
        period_end = subscription["currentPeriodEnd"]
        plan_id = subscription["planID"]
        plan = PLANS.get(plan_id)
        if status.lower() == "active" and plan is None:
            raise RuntimeError(f"Command Code unknown active planId: {plan_id}")
        if plan:
            plan_name, total = plan

    primary = None
    login_parts = []
    if plan_name:
        login_parts.append(plan_name)
    if total and total > 0:
        used = max(0.0, min(total, total - credits["monthly"]))
        primary = {
            "usedPercent": round(max(0.0, min(100.0, used / total * 100.0)), 1),
            "windowMinutes": None,
        }
        if period_end:
            primary["resetsAt"] = iso_utc(period_end)
        login_parts.append(f"{format_usd(used)} of {format_usd(total)}")
    elif credits["monthly"] > 0 or credits["purchased"] > 0:
        primary = {"usedPercent": 0, "windowMinutes": None}
        if period_end:
            primary["resetsAt"] = iso_utc(period_end)
        login_parts.append(f"{format_usd(credits['monthly'])} remaining")
    if credits["purchased"] > 0:
        login_parts.append(f"+ {format_usd(credits['purchased'])} credits")
    if status and not plan_name:
        login_parts.append(status.title())

    usage = {
        "identity": {
            "loginMethod": " - ".join(login_parts) if login_parts else "Command Code",
        },
    }
    if primary:
        usage["primary"] = primary

    return {
        "provider": PROVIDER,
        "source": SOURCE,
        "usage": usage,
        "credits": {
            "remaining": round(
                credits["monthly"] + credits["purchased"] + credits["premium"] + credits["opensource"],
                2,
            ),
        },
    }


def self_test() -> int:
    entry = entry_from_payloads(
        {"credits": {"monthlyCredits": 12, "purchasedCredits": 2}},
        {"success": True, "data": {
            "planId": "individual-pro",
            "status": "active",
            "currentPeriodEnd": "2026-07-01T00:00:00.000Z",
        }},
    )
    assert entry["usage"]["primary"]["usedPercent"] == 60.0
    assert entry["credits"]["remaining"] == 14.0
    assert normalize_cookie_header("Cookie: better-auth.session_token=abc") == "better-auth.session_token=abc"
    print("commandcode helper self-test ok")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    cookie = read_cookie_header()
    if not cookie:
        return error(
            "Set CODEXBAR_COMMANDCODE_COOKIE or CODEXBAR_COMMANDCODE_COOKIE_FILE "
            "to a Command Code Cookie header.",
        )

    try:
        credits = load_json(f"{API_BASE}/internal/billing/credits", cookie)
        subscription = load_json(f"{API_BASE}/internal/billing/subscriptions", cookie)
        return emit(entry_from_payloads(credits, subscription))
    except RuntimeError as exc:
        return error(str(exc))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # pragma: no cover - last-resort UI error
        sys.exit(error(f"Command Code usage failed: {exc}", kind="runtime"))
