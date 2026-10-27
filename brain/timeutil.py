"""Время: всё храним как ISO-строки в UTC вида 2026-10-27T09:50:12Z."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional, Union


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(value: Union[str, float, int, None]) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    s = re.sub(r"\.\d+", "", str(value).strip()).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso(value: Union[str, float, int, None]) -> Optional[str]:
    dt = parse(value)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def age_days(iso: Optional[str], now: Optional[datetime] = None) -> float:
    dt = parse(iso)
    if dt is None:
        return 3650.0
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400)
