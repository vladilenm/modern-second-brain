"""HTML-панель состояния базы: brain dashboard → data/dashboard.html.

Один файл без сервера: данные вшиваются в страницу при сборке, вся отрисовка в браузере.
Файл лежит рядом с базой (в git не попадает) и содержит то же, что и база, поэтому права 600.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Tuple

from . import connectors, health, status as status_mod, topics
from .projects import project_key
from .timeutil import now_iso

WEB = Path(__file__).with_name("web")
SCRIPTS = ("graph-vendor.js", "charts.js", "graph.js", "graph-3d.js", "app.js")
PREVIEW_CHARS = 6000


def _lines(text):
    return [line for line in (text or "").splitlines() if line.strip()]


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _files_seen(conn: sqlite3.Connection, cfg: dict) -> dict:
    """Сколько файлов источника уже просмотрено: разница с числом записей — служебные сессии без разговора.
    Считаем только там, где один файл — одна запись."""
    paths = [r["path"] for r in conn.execute("SELECT path FROM file_state")]
    out = {}
    for cls in connectors.ALL.values():
        if issubclass(cls, connectors.FileConnector) and cls.one_record_per_file:
            prefixes = tuple(d.rstrip("/") + "/" for d in cls(cfg).roots())
            out[cls.source] = sum(1 for p in paths if prefixes and p.startswith(prefixes))
    return out


def _name_parts(key: str, group: list) -> set:
    """Части путей проекта («vladilenm», «club-landing») — чтобы не считать их темами."""
    segs = {seg for _, p, _ in group for seg in (p or "").lower().replace("_", "-").split("/")}
    return {key} | {seg for seg in segs if len(seg) >= 4}


def _projects(records: list, stop=()) -> dict:
    """Темы и ключ проекта у каждой записи; проекты с путями и главными темами; рёбра графа."""
    t = topics.build(((r["id"], r["keywords"], r["title"]) for r in records), stop=stop)
    groups = defaultdict(list)
    for r in records:
        key, name = project_key(r["project"])
        r["pkey"] = key
        r["topics"] = t.shown.get(r["id"], [])
        # Все темы записи, а не только восемь самых редких: по ним считаются страницы тем.
        r["topics_all"] = [t.display[k] for k in t.keys.get(r["id"], [])]
        groups[key].append((name, r["project"], r["id"]))
    projects = {
        key: {"name": Counter(name for name, _, _ in g).most_common(1)[0][0],
              "paths": sorted({p for _, p, _ in g if p}),
              "topics": t.project_topics([i for _, _, i in g], exclude=_name_parts(key, g))}
        for key, g in groups.items()
    }
    return {"projects": projects, "graph": {"edges": [list(p) for p in t.pairs]}}


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
    last_sync = st.get("last_sync") or {}
    meta = connectors.registry()
    return {
        "generated_at": now_iso(),
        "db_path": cfg["db_path"],
        "state": state,
        "status": st,
        "sources_meta": meta,
        "sync": {m["source"]: sync.get(m["connector"]) for m in meta},
        "waiting": {m["source"]: (last_sync.get(m["connector"]) or {}).get("waiting", 0) for m in meta},
        "files_seen": _files_seen(conn, cfg),
        # Тишина источников: сколько дней нет новых записей и какая пауза для источника обычна.
        "silence": {s["source"]: s for s in health.sources(conn)},
        "batch_limit": int(cfg["llm"].get("batch_limit", 40)),
        "preview_chars": PREVIEW_CHARS,
        "records": records,
        **_projects(records, cfg.get("topic_stopwords") or ()),
    }


def assemble(web: Path = WEB) -> str:
    """Шаблон страницы со вшитыми стилями и скриптами. «</script» внутри JS закрыл бы тег раньше времени."""
    css = (web / "style.css").read_text(encoding="utf-8")
    js = "\n".join((web / name).read_text(encoding="utf-8") for name in SCRIPTS)
    if re.search(r"</style", css, re.I) or re.search(r"</script|<!--", js, re.I):
        raise ValueError("в brain/web: «</script» в JS или «</style» в CSS сломает страницу")
    html = (web / "index.html").read_text(encoding="utf-8")
    return html.replace("/*__CSS__*/", css, 1).replace("/*__JS__*/", js, 1)


def build(conn: sqlite3.Connection, cfg: dict) -> Path:
    # «</» внутри JSON закрыл бы тег <script> раньше времени.
    payload = json.dumps(collect(conn, cfg), ensure_ascii=False).replace("</", "<\\/")
    # Данные вшиваются последними: в тексте разговоров может встретиться и «/*__JS__*/».
    html = assemble().replace("/*__DATA__*/null", payload, 1)
    out = Path(cfg["db_path"]).expanduser().parent / "dashboard.html"
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(html, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, out)
    return out
