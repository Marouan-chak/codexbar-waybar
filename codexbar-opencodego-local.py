#!/usr/bin/env python3
"""Linux local usage reader for OpenCode Go.

Reads the same local OpenCode database path used by CodexBar's local reader and
emits one CodexBar-compatible JSON array entry.
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

PROVIDER = "opencodego"
SOURCE = "local"
FIVE_HOURS = 5 * 60 * 60
WEEK = 7 * 24 * 60 * 60
LIMITS = {"session": 12.0, "weekly": 30.0, "monthly": 60.0}


def emit(entry: dict) -> int:
    print(json.dumps([entry], separators=(",", ":")))
    return 0


def error(message: str, *, kind: str = "provider", code: int = 1) -> int:
    return emit({
        "provider": PROVIDER,
        "source": SOURCE,
        "error": {"kind": kind, "code": code, "message": message},
    })


def iso_utc(timestamp_ms: int) -> str:
    value = dt.datetime.fromtimestamp(timestamp_ms / 1000, tz=dt.timezone.utc)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def human_reset(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "Resets in less than a minute"
    minutes = seconds // 60
    if minutes < 60:
        return f"Resets in {minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"Resets in {hours}h {minutes}m" if minutes else f"Resets in {hours}h"
    days, hours = divmod(hours, 24)
    return f"Resets in {days}d {hours}h" if hours else f"Resets in {days}d"


def percent(used: float, limit: float) -> float:
    if not used or not (used > 0) or limit <= 0:
        return 0.0
    return round(max(0.0, min(100.0, used / limit * 100.0)), 1)


def has_auth_key(path: Path) -> bool:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    entry = data.get("opencode-go")
    if not isinstance(entry, dict):
        return False
    key = entry.get("key")
    return isinstance(key, str) and bool(key.strip())


def has_table(db: sqlite3.Connection, name: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


MESSAGE_USAGE_SQL = """
    SELECT
      CAST(COALESCE(json_extract(data, '$.time.created'), time_created) AS INTEGER) AS createdMs,
      CAST(json_extract(data, '$.cost') AS REAL) AS cost
    FROM message
    WHERE json_valid(data)
      AND json_extract(data, '$.providerID') = 'opencode-go'
      AND json_extract(data, '$.role') = 'assistant'
      AND json_type(data, '$.cost') IN ('integer', 'real')
"""

MESSAGE_AND_PART_USAGE_SQL = """
    WITH message_costs AS (
      SELECT
        id AS messageID,
        CAST(COALESCE(json_extract(data, '$.time.created'), time_created) AS INTEGER) AS createdMs,
        CAST(json_extract(data, '$.cost') AS REAL) AS cost
      FROM message
      WHERE json_valid(data)
        AND json_extract(data, '$.providerID') = 'opencode-go'
        AND json_extract(data, '$.role') = 'assistant'
        AND json_type(data, '$.cost') IN ('integer', 'real')
    )
    SELECT createdMs, cost
    FROM message_costs
    UNION ALL
    SELECT
      CAST(COALESCE(json_extract(p.data, '$.time.created'), p.time_created, m.time_created) AS INTEGER)
        AS createdMs,
      CAST(json_extract(p.data, '$.cost') AS REAL) AS cost
    FROM part p
    JOIN message m ON m.id = p.message_id
    WHERE json_valid(p.data)
      AND json_valid(m.data)
      AND json_extract(p.data, '$.type') = 'step-finish'
      AND json_type(p.data, '$.cost') IN ('integer', 'real')
      AND json_extract(m.data, '$.providerID') = 'opencode-go'
      AND json_extract(m.data, '$.role') = 'assistant'
      AND NOT EXISTS (
        SELECT 1
        FROM message_costs
        WHERE message_costs.messageID = p.message_id
      )
"""


def read_rows(database: Path) -> list[tuple[int, float]]:
    uri = f"file:{database}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=0.25) as db:
        sql = MESSAGE_AND_PART_USAGE_SQL if has_table(db, "part") else MESSAGE_USAGE_SQL
        rows = []
        for created_ms, cost in db.execute(sql):
            if created_ms is None or cost is None:
                continue
            created_ms = int(created_ms)
            cost = float(cost)
            if created_ms > 0 and cost >= 0:
                rows.append((created_ms, cost))
        return rows


def start_of_utc_week(now: dt.datetime) -> dt.datetime:
    midnight = dt.datetime(now.year, now.month, now.day, tzinfo=dt.timezone.utc)
    return midnight - dt.timedelta(days=midnight.weekday())


def anchored_month(calendar_month: dt.datetime, anchor: dt.datetime) -> dt.datetime:
    last_day = calendar.monthrange(calendar_month.year, calendar_month.month)[1]
    day = min(anchor.day, last_day)
    return dt.datetime(
        calendar_month.year,
        calendar_month.month,
        day,
        anchor.hour,
        anchor.minute,
        anchor.second,
        anchor.microsecond,
        tzinfo=dt.timezone.utc,
    )


def add_month(value: dt.datetime, delta: int) -> dt.datetime:
    month_index = (value.year * 12 + value.month - 1) + delta
    year, month = divmod(month_index, 12)
    return value.replace(year=year, month=month + 1, day=1)


def month_bounds(now: dt.datetime, rows: list[tuple[int, float]]) -> tuple[int, int]:
    anchor_ms = min((created for created, _ in rows), default=None)
    current_month = dt.datetime(now.year, now.month, 1, tzinfo=dt.timezone.utc)
    if anchor_ms is None:
        start = current_month
        end = add_month(start, 1)
        return int(start.timestamp() * 1000), int(end.timestamp() * 1000)

    anchor = dt.datetime.fromtimestamp(anchor_ms / 1000, tz=dt.timezone.utc)
    start = anchored_month(current_month, anchor)
    if start > now:
        start = anchored_month(add_month(current_month, -1), anchor)
    end = anchored_month(add_month(start, 1), anchor)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def sum_cost(rows: list[tuple[int, float]], start_ms: int, end_ms: int) -> float:
    return sum(cost for created, cost in rows if start_ms <= created < end_ms)


def rolling_reset(rows: list[tuple[int, float]], now_ms: int) -> int:
    session_start = now_ms - FIVE_HOURS * 1000
    oldest = min((created for created, _ in rows if session_start <= created < now_ms), default=now_ms)
    return max(0, int((oldest + FIVE_HOURS * 1000 - now_ms) / 1000))


def make_window(used: float, limit: float, reset_at_ms: int, reset_in: int | None = None,
                minutes: int | None = None) -> dict:
    window = {
        "usedPercent": percent(used, limit),
        "resetsAt": iso_utc(reset_at_ms),
    }
    if minutes is not None:
        window["windowMinutes"] = minutes
    if reset_in is not None:
        window["resetDescription"] = human_reset(reset_in)
    return window


def main() -> int:
    home = Path.home()
    auth_path = Path(os.environ.get(
        "CODEXBAR_OPENCODEGO_AUTH",
        str(home / ".local/share/opencode/auth.json"),
    ))
    database = Path(os.environ.get(
        "CODEXBAR_OPENCODEGO_DB",
        str(home / ".local/share/opencode/opencode.db"),
    ))

    has_auth = has_auth_key(auth_path)
    if not database.exists():
        if has_auth:
            return error("OpenCode Go local usage history is unavailable: database not found.")
        return error("OpenCode Go not detected. Log in with OpenCode Go or use it locally first.")

    try:
        rows = read_rows(database)
    except sqlite3.Error as exc:
        return error(f"OpenCode Go SQLite error reading local usage: {exc}", kind="runtime")

    if not rows:
        if has_auth:
            return error("OpenCode Go local usage history is unavailable: no local usage rows.")
        return error("OpenCode Go not detected. Log in with OpenCode Go or use it locally first.")

    now = dt.datetime.now(dt.timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    session_start = now_ms - FIVE_HOURS * 1000
    week_start = int(start_of_utc_week(now).timestamp() * 1000)
    week_end = week_start + WEEK * 1000
    month_start, month_end = month_bounds(now, rows)

    session_cost = sum_cost(rows, session_start, now_ms)
    weekly_cost = sum_cost(rows, week_start, week_end)
    monthly_cost = sum_cost(rows, month_start, month_end)
    session_reset = rolling_reset(rows, now_ms)

    entry = {
        "provider": PROVIDER,
        "source": SOURCE,
        "usage": {
            "primary": make_window(
                session_cost,
                LIMITS["session"],
                now_ms + session_reset * 1000,
                session_reset,
                minutes=FIVE_HOURS // 60,
            ),
            "secondary": make_window(
                weekly_cost,
                LIMITS["weekly"],
                week_end,
                max(0, int((week_end - now_ms) / 1000)),
                minutes=WEEK // 60,
            ),
            "tertiary": make_window(
                monthly_cost,
                LIMITS["monthly"],
                month_end,
                max(0, int((month_end - now_ms) / 1000)),
            ),
            "identity": {
                "loginMethod": f"Local usage - ${session_cost:.2f} session, ${weekly_cost:.2f} weekly",
            },
        },
    }
    return emit(entry)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # pragma: no cover - last-resort UI error
        sys.exit(error(f"OpenCode Go local usage failed: {exc}", kind="runtime"))
