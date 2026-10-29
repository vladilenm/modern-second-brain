"""Синхронизация: прогоняет коннекторы и складывает новое в базу.

Файловые источники перечитываются по mtime и размеру файла, API-источники — с сохранённого курсора.
У каждого источника своё расписание (every_minutes), а launchd запускает sync раз в 15 минут.
Источник, чей прошлый прогон упал, запускается в следующем sync, не дожидаясь расписания.
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import connectors, db
from .timeutil import parse

# launchd стартует в :00, :15, :30 и :45, а прогон длится минуты: без запаса
# источник «раз в час» срабатывал бы раз в час с четвертью.
GRACE_SECONDS = 5 * 60


def excluded_prefixes(cfg: dict) -> List[str]:
    return [os.path.expanduser(p).rstrip("/") for p in cfg.get("exclude_projects") or []]


def is_excluded(project: Optional[str], prefixes: List[str]) -> bool:
    if not project:
        return False
    path = os.path.expanduser(project)
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def purge_excluded(conn: sqlite3.Connection, prefixes: List[str]) -> int:
    """Удаляет из базы записи проектов, которые исключили уже после того, как они попали в базу."""
    ids = [r["id"] for r in conn.execute("SELECT id, project FROM records WHERE project IS NOT NULL")
           if is_excluded(r["project"], prefixes)]
    db.delete_records(conn, ids)
    conn.commit()
    return len(ids)


def purge_connector(conn: sqlite3.Connection, connector) -> int:
    """То же для правила самого коннектора (например, exclude_repos у GitHub)."""
    ids = [r["id"] for r in conn.execute(
        "SELECT id, project FROM records WHERE source = ? AND project IS NOT NULL", (connector.source,))
        if connector.excludes(r["project"])]
    db.delete_records(conn, ids)
    conn.commit()
    return len(ids)


def seconds_left(conn: sqlite3.Connection, connector, now: float) -> float:
    """Сколько ждать до следующего запуска источника; 0 — пора."""
    if connector.every_seconds <= 0:
        return 0.0
    row = conn.execute("SELECT last_run_at, last_error FROM sync_state WHERE source = ?",
                       (connector.name,)).fetchone()
    # Упавший прогон повторяем в следующем: ночью launchd будит Mac на секунды, сеть
    # не успевает подняться, и без этого ошибка висела бы ещё час после пробуждения.
    last = parse(row["last_run_at"]) if row and not row["last_error"] else None
    if last is None:
        return 0.0
    left = connector.every_seconds - (now - last.timestamp())
    return 0.0 if left <= GRACE_SECONDS else left


def _skip(rec, connector, prefixes: List[str]) -> bool:
    return is_excluded(rec.project, prefixes) or connector.excludes(rec.project)


def _counts() -> dict:
    return {"new": 0, "changed": 0, "waiting": 0, "errors": 0, "excluded": 0}


def _sync_files(conn: sqlite3.Connection, connector, prefixes: List[str], rescan: bool,
                now: float) -> Tuple[dict, Optional[str]]:
    counts = _counts()
    last_error = None
    seen = set()
    for path in connector.files():
        try:
            st = path.stat()
        except OSError:
            continue
        seen.add(str(path))
        if now - st.st_mtime < connector.idle_seconds:
            counts["waiting"] += 1  # разговор ещё идёт, заберём в следующий раз
            continue
        key = str(path)
        if not rescan and db.get_file_state(conn, key) == (st.st_mtime, st.st_size):
            continue
        try:
            for rec in connector.parse(path):
                if _skip(rec, connector, prefixes):
                    counts["excluded"] += 1
                    continue
                status = db.upsert(conn, rec)
                counts["new"] += status == "new"
                counts["changed"] += status == "changed"
            db.set_file_state(conn, key, st.st_mtime, st.st_size)
            conn.commit()
        except Exception as exc:  # один битый файл не должен ронять весь источник
            conn.rollback()
            counts["errors"] += 1
            last_error = f"{path.name}: {exc}"
    # Исчезнувшие файлы забываем: Codex сжимает старые сессии в новый файл .zst,
    # и без этого панель считала бы такую сессию дважды.
    db.forget_files(conn, connector.roots(), seen)
    conn.commit()
    error = f"ошибок в файлах: {counts['errors']}, последняя — {last_error}" if last_error else None
    return counts, error


def _sync_api(conn: sqlite3.Connection, connector, prefixes: List[str],
              rescan: bool) -> Tuple[dict, Optional[str]]:
    counts = _counts()
    if rescan:
        db.reset_cursor(conn, connector.name)
        conn.commit()
    try:
        for batch in connector.fetch(db.get_cursor(conn, connector.name)):
            new = changed = excluded = 0
            for rec in batch.records:
                if _skip(rec, connector, prefixes):
                    excluded += 1
                    continue
                status = db.upsert(conn, rec)
                new += status == "new"
                changed += status == "changed"
            db.set_cursor(conn, connector.name, batch.cursor)
            conn.commit()
            counts["new"] += new
            counts["changed"] += changed
            counts["excluded"] += excluded
    except Exception as exc:  # курсор остаётся на последней записанной пачке
        conn.rollback()
        counts["errors"] += 1
        return counts, str(exc) if isinstance(exc, RuntimeError) else f"{type(exc).__name__}: {exc}"
    return counts, None


def sync(cfg: dict, conn: sqlite3.Connection, only: Optional[str] = None,
         log: Callable[[str], None] = print, rescan: bool = False) -> Dict[str, dict]:
    """only — один источник, без оглядки на расписание. rescan=True забирает всё заново
    (файлы — целиком, API — с начала, сбросив курсор) и тоже не ждёт расписания;
    неизменившиеся записи всё равно отсекаются по хешу."""
    now = time.time()
    totals = {}
    prefixes = excluded_prefixes(cfg)
    removed = purge_excluded(conn, prefixes) if prefixes else 0
    if removed:
        log(f"удалено записей из исключённых проектов: {removed}")
    for connector in connectors.enabled(cfg, only):
        left = 0.0 if (only or rescan) else seconds_left(conn, connector, now)
        if left:
            log(f"{connector.name}: рано, следующий запуск через {int(left // 60) + 1} мин")
            continue
        removed = purge_connector(conn, connector)
        if removed:
            log(f"{connector.name}: удалено исключённых записей: {removed}")
        if isinstance(connector, connectors.ApiConnector):
            counts, error = _sync_api(conn, connector, prefixes, rescan)
        else:
            counts, error = _sync_files(conn, connector, prefixes, rescan, now)
        db.record_run(conn, connector.name, error, counts["new"], counts["changed"])
        conn.commit()
        totals[connector.name] = counts
        log(f"{connector.name}: новых {counts['new']}, изменённых {counts['changed']}, "
            f"ждут тишины {counts['waiting']}, исключено {counts['excluded']}, ошибок {counts['errors']}")
        if error:
            log(f"  {error}")
    return totals
