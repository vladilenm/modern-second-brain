"""SQLite-база: записи, полнотекстовый индекс FTS5 и отметки синхронизации."""
from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Optional, Tuple

from .connectors.base import Record
from .redact import redact
from .timeutil import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    location TEXT,
    project TEXT,
    started_at TEXT,
    updated_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    ai_title TEXT,
    summary TEXT,
    questions TEXT,
    keywords TEXT,
    atoms TEXT,
    summarized_hash TEXT,
    summarized_at TEXT,
    summary_error TEXT,
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS records_updated ON records (updated_at);

-- Отдельная копия текста под поиск. trigram ищет по подстрокам,
-- поэтому «клуб» находит и «клуба», и «клубу».
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5 (
    title, summary, questions, keywords, atoms, body,
    tokenize = 'trigram'
);

-- Курсор файловых коннекторов: файл перечитываем, только если он изменился.
CREATE TABLE IF NOT EXISTS file_state (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    source TEXT PRIMARY KEY,
    last_run_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    last_new INTEGER NOT NULL DEFAULT 0,
    last_changed INTEGER NOT NULL DEFAULT 0
);
"""


def connect(path: str) -> sqlite3.Connection:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return conn


def content_hash(rec: Record) -> str:
    return hashlib.sha256(f"{rec.title}\0{rec.body}".encode("utf-8")).hexdigest()


def upsert(conn: sqlite3.Connection, rec: Record) -> str:
    """Возвращает 'new', 'changed' или 'same'."""
    rec.title = redact(rec.title)
    rec.body = redact(rec.body)
    h = content_hash(rec)
    row = conn.execute(
        "SELECT id, content_hash FROM records WHERE source = ? AND external_id = ?",
        (rec.source, rec.external_id),
    ).fetchone()
    if row and row["content_hash"] == h:
        return "same"
    if row:
        conn.execute(
            """UPDATE records SET title = ?, body = ?, location = ?, project = ?,
                   started_at = ?, updated_at = ?, content_hash = ?, ingested_at = ?
               WHERE id = ?""",
            (rec.title, rec.body, rec.location, rec.project, rec.started_at,
             rec.updated_at, h, now_iso(), row["id"]),
        )
        rid, status = row["id"], "changed"
    else:
        cur = conn.execute(
            """INSERT INTO records (source, external_id, title, body, location, project,
                   started_at, updated_at, content_hash, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rec.source, rec.external_id, rec.title, rec.body, rec.location, rec.project,
             rec.started_at, rec.updated_at, h, now_iso()),
        )
        rid, status = cur.lastrowid, "new"
    reindex(conn, rid)
    return status


def reindex(conn: sqlite3.Connection, rid: int) -> None:
    r = conn.execute("SELECT * FROM records WHERE id = ?", (rid,)).fetchone()
    conn.execute("DELETE FROM records_fts WHERE rowid = ?", (rid,))
    if r is None:
        return
    title = " ".join(x for x in (r["ai_title"], r["title"]) if x)
    conn.execute(
        """INSERT INTO records_fts (rowid, title, summary, questions, keywords, atoms, body)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (rid, title, r["summary"] or "", r["questions"] or "", r["keywords"] or "",
         r["atoms"] or "", r["body"]),
    )


def delete_records(conn: sqlite3.Connection, ids) -> None:
    for rid in ids:
        conn.execute("DELETE FROM records_fts WHERE rowid = ?", (rid,))
        conn.execute("DELETE FROM records WHERE id = ?", (rid,))


def get_file_state(conn: sqlite3.Connection, path: str) -> Optional[Tuple[float, int]]:
    row = conn.execute("SELECT mtime, size FROM file_state WHERE path = ?", (path,)).fetchone()
    return (row["mtime"], row["size"]) if row else None


def set_file_state(conn: sqlite3.Connection, path: str, mtime: float, size: int) -> None:
    conn.execute(
        "INSERT INTO file_state (path, mtime, size) VALUES (?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET mtime = excluded.mtime, size = excluded.size",
        (path, mtime, size),
    )


def record_run(conn: sqlite3.Connection, source: str, error: Optional[str], new: int, changed: int) -> None:
    now = now_iso()
    conn.execute(
        """INSERT INTO sync_state (source, last_run_at, last_success_at, last_error, last_new, last_changed)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(source) DO UPDATE SET
               last_run_at = excluded.last_run_at,
               last_success_at = COALESCE(excluded.last_success_at, sync_state.last_success_at),
               last_error = excluded.last_error,
               last_new = excluded.last_new,
               last_changed = excluded.last_changed""",
        (source, now, None if error else now, error, new, changed),
    )
