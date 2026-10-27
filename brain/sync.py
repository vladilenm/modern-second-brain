"""Синхронизация: прогоняет коннекторы и складывает новое в базу."""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Callable, Dict, List, Optional

from . import connectors, db


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


def sync(cfg: dict, conn: sqlite3.Connection, only: Optional[str] = None,
         log: Callable[[str], None] = print, rescan: bool = False) -> Dict[str, dict]:
    """rescan=True перечитывает все файлы (нужно после правки коннектора);
    неизменившиеся записи всё равно отсекаются по хешу."""
    now = time.time()
    totals = {}
    prefixes = excluded_prefixes(cfg)
    removed = purge_excluded(conn, prefixes) if prefixes else 0
    if removed:
        log(f"удалено записей из исключённых проектов: {removed}")
    for connector in connectors.enabled(cfg, only):
        new = changed = waiting = errors = excluded = 0
        last_error = None
        for path in connector.files():
            try:
                st = path.stat()
            except OSError:
                continue
            if now - st.st_mtime < connector.idle_seconds:
                waiting += 1  # разговор ещё идёт, заберём в следующий раз
                continue
            key = str(path)
            if not rescan and db.get_file_state(conn, key) == (st.st_mtime, st.st_size):
                continue
            try:
                for rec in connector.parse(path):
                    if is_excluded(rec.project, prefixes):
                        excluded += 1
                        continue
                    status = db.upsert(conn, rec)
                    new += status == "new"
                    changed += status == "changed"
                db.set_file_state(conn, key, st.st_mtime, st.st_size)
                conn.commit()
            except Exception as exc:  # один битый файл не должен ронять весь источник
                conn.rollback()
                errors += 1
                last_error = f"{path.name}: {exc}"
        error = f"ошибок в файлах: {errors}, последняя — {last_error}" if errors else None
        db.record_run(conn, connector.name, error, new, changed)
        conn.commit()
        totals[connector.name] = {"new": new, "changed": changed, "waiting": waiting, "errors": errors,
                                  "excluded": excluded}
        log(f"{connector.name}: новых {new}, изменённых {changed}, ждут тишины {waiting}, "
            f"исключено {excluded}, ошибок {errors}")
        if last_error:
            log(f"  последняя ошибка: {last_error}")
    return totals
