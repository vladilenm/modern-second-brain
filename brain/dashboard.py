"""HTML-панель состояния базы: brain dashboard → data/dashboard.html.

Один файл без сервера: данные вшиваются в страницу при сборке, вся отрисовка в браузере.
Файл лежит рядом с базой (в git не попадает) и содержит то же, что и база, поэтому права 600.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from . import status as status_mod
from .timeutil import now_iso

TEMPLATE = Path(__file__).with_name("dashboard.html")
PREVIEW_CHARS = 6000
SYNC_NAMES = {"claude_code": "claude_code", "codex": "codex", "chatgpt": "chatgpt_export"}


def _lines(text):
    return [line for line in (text or "").splitlines() if line.strip()]


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _files_seen(conn: sqlite3.Connection, cfg: dict) -> dict:
    """Сколько файлов источника уже просмотрено: разница с числом записей — служебные сессии без разговора."""
    roots = {
        "claude_code": cfg["sources"]["claude_code"].get("roots", []),
        "codex": cfg["sources"]["codex"].get("roots", []),
        "chatgpt": [cfg["inbox"]],
    }
    paths = [r["path"] for r in conn.execute("SELECT path FROM file_state")]
    out = {}
    for source, dirs in roots.items():
        prefixes = [str(Path(d).expanduser()) + "/" for d in dirs]
        out[source] = sum(1 for p in paths if p.startswith(tuple(prefixes)))
    return out


def collect(conn: sqlite3.Connection, cfg: dict) -> dict:
    st = status_mod.read(status_mod.status_path(cfg))
    state = st.get("state") or "none"
    if state in ("sync", "summarize") and not _alive(st.get("pid")):
        state = "interrupted"
    records = []
    for r in conn.execute(
        """SELECT id, source, title, ai_title, project, location, started_at, updated_at,
                  summarized_hash = content_hash AS card, summary, questions, keywords, atoms,
                  summary_error, substr(body, 1, ?) AS preview, length(body) AS body_len
           FROM records ORDER BY updated_at DESC""",
        (PREVIEW_CHARS,),
    ):
        records.append({
            "id": r["id"],
            "source": r["source"],
            "title": r["ai_title"] or r["title"],
            "project": r["project"],
            "location": r["location"],
            "started_at": r["started_at"],
            "updated_at": r["updated_at"],
            "card": bool(r["card"]),
            "summary": r["summary"],
            "questions": _lines(r["questions"]),
            "keywords": r["keywords"],
            "atoms": _lines(r["atoms"]),
            "error": None if r["card"] else r["summary_error"],
            "preview": r["preview"],
            "body_len": r["body_len"],
        })
    sync = {row["source"]: dict(row) for row in conn.execute("SELECT * FROM sync_state")}
    return {
        "generated_at": now_iso(),
        "db_path": cfg["db_path"],
        "state": state,
        "status": st,
        "sync": {source: sync.get(name) for source, name in SYNC_NAMES.items()},
        "waiting": {source: ((st.get("last_sync") or {}).get(name) or {}).get("waiting", 0)
                    for source, name in SYNC_NAMES.items()},
        "files_seen": _files_seen(conn, cfg),
        "batch_limit": int(cfg["llm"].get("batch_limit", 40)),
        "preview_chars": PREVIEW_CHARS,
        "records": records,
    }


def build(conn: sqlite3.Connection, cfg: dict) -> Path:
    # «</» внутри JSON закрыл бы тег <script> раньше времени.
    payload = json.dumps(collect(conn, cfg), ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload, 1)
    out = Path(cfg["db_path"]).expanduser().parent / "dashboard.html"
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(html, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, out)
    return out
