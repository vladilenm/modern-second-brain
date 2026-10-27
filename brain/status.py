"""Файл состояния для значка в строке меню (menubar/).

data/status.json рядом с базой: что brain делает прямо сейчас и что лежит в базе.
Значок перечитывает файл раз в пару секунд. Пишем атомарно, чтобы он не прочитал половину.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

from . import summarize
from .timeutil import now_iso


def status_path(cfg: dict) -> Path:
    return Path(cfg["db_path"]).expanduser().parent / "status.json"


def read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def snapshot(conn: sqlite3.Connection) -> dict:
    sources = {}
    for r in conn.execute(
        """SELECT source, COUNT(*) AS n,
                  SUM(summarized_hash IS NOT NULL AND summarized_hash = content_hash) AS done,
                  MAX(updated_at) AS latest
           FROM records GROUP BY source"""
    ):
        sources[r["source"]] = {"records": r["n"], "summarized": r["done"] or 0, "latest": r["latest"]}
    recent = [
        {"id": r["id"], "source": r["source"], "title": r["ai_title"] or r["title"], "at": r["summarized_at"]}
        for r in conn.execute(
            """SELECT id, source, title, ai_title, summarized_at FROM records
               WHERE summarized_at IS NOT NULL ORDER BY summarized_at DESC LIMIT 8"""
        )
    ]
    errors = [
        {"source": s["source"], "error": s["last_error"]}
        for s in conn.execute("SELECT source, last_error FROM sync_state WHERE last_error IS NOT NULL")
    ]
    return {"sources": sources, "pending": summarize.pending_count(conn), "recent": recent, "sync_errors": errors}


class Reporter:
    """Один прогон sync или summarize. Если процесс умрёт посреди работы,
    значок увидит, что pid мёртв, и не будет вечно показывать «синхронизирую»."""

    def __init__(self, cfg: dict, conn: sqlite3.Connection):
        self.path = status_path(cfg)
        self.conn = conn
        self.data = read(self.path)
        self.data.update(pid=os.getpid(), started_at=now_iso(), finished_at=None, error=None, progress=None)
        self.data.update(snapshot(conn))

    def update(self, **fields) -> None:
        self.data.update(fields)
        self.data["updated_at"] = now_iso()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # значок — не повод ронять синхронизацию

    def progress(self, done: int, total: int, current: Optional[str]) -> None:
        self.update(state="summarize", progress={"done": done, "total": total, "current": current})

    def finish(self, error: Optional[str] = None) -> None:
        self.data.update(snapshot(self.conn))
        self.update(state="error" if error else "idle", finished_at=now_iso(), error=error, progress=None)
