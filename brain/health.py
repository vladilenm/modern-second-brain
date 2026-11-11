"""Здоровье источников: не молчит ли источник дольше, чем для него обычно.

Синхронизация может проходить без ошибок и при этом ничего не приносить. По истории источника
считаем его обычную паузу между днями с новыми записями и сравниваем с нынешней тишиной.
Это статистика, а не диагноз: причину тишины она не называет.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .timeutil import parse

HISTORY_DAYS = 365
MIN_ACTIVE_DAYS = 6   # меньше — ритм источника ещё не понятен
MIN_SILENT_DAYS = 3   # короче этого не беспокоим даже ежедневные источники
FACTOR = 3            # во сколько раз тишина должна превысить обычную паузу


def sources(conn: sqlite3.Connection, now: Optional[datetime] = None) -> List[dict]:
    """По каждому источнику: дата последней записи, сколько дней тишины, обычная пауза и вердикт quiet."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=HISTORY_DAYS)
    active: dict = {}
    latest: dict = {}
    # День активен, если в него обновилась запись (updated_at) или запись попала в базу (ingested_at).
    for r in conn.execute("SELECT source, updated_at, ingested_at FROM records"):
        for value in (r["updated_at"], r["ingested_at"]):
            dt = parse(value)
            if dt is None or dt > now:
                continue
            if dt >= since:
                active.setdefault(r["source"], set()).add(dt.date())
        dt = parse(r["updated_at"])
        if dt and (r["source"] not in latest or dt > latest[r["source"]]):
            latest[r["source"]] = dt
    out = []
    for source in sorted(latest):
        days = sorted(active.get(source, ()))
        usual = None
        if len(days) >= MIN_ACTIVE_DAYS:
            gaps = sorted((b - a).days for a, b in zip(days, days[1:]))
            usual = gaps[math.ceil(0.9 * len(gaps)) - 1]
        last = max([latest[source]] + [datetime.combine(d, datetime.min.time(), timezone.utc) for d in days[-1:]])
        silent = max(0, int((now - last).total_seconds() // 86400))
        out.append({"source": source, "latest": latest[source].strftime("%Y-%m-%dT%H:%M:%SZ"), "silent_days": silent,
                    "usual_days": usual,
                    "quiet": usual is not None and silent >= MIN_SILENT_DAYS and silent > FACTOR * usual})
    return out


def quiet(conn: sqlite3.Connection, now: Optional[datetime] = None) -> List[dict]:
    return [s for s in sources(conn, now) if s["quiet"]]
