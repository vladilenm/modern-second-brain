"""Проекты: ключ проекта, список проектов и поиск проекта по имени — общее для панели, CLI и MCP."""
from __future__ import annotations

import fnmatch
import re
import sqlite3
from collections import Counter
from typing import Dict, List, Optional, Tuple


class ProjectError(ValueError):
    """Имя проекта пустое, не найдено или подходит нескольким проектам."""


# Папки, которые инструменты заводят сами: это разговоры без проекта, а не проекты.
#   ~/Documents/Codex/<дата>/<имя>       — чат Codex без папки (new-chat, files-mentioned-by-the-user-…)
#   ~/.codex/.chatgpt-projects/g-p-…     — проект ChatGPT внутри Codex
#   ~/Documents/kimi/tasks/<дата>/<id>   — задача Kimi
#   /tmp, /private/tmp, /private/var     — временные папки
NO_PROJECT = re.compile(
    r"^(?:~/Documents/Codex/\d{4}-\d{2}-\d{2}/[^/]+"
    r"|~/\.codex/\.chatgpt-projects/.*"
    r"|~/Documents/kimi/tasks/.*"
    r"|(?:/private)?/(?:tmp|var)/.*"
    r"|~|~/Desktop|~/Documents|~/Downloads)$"
)
# Рабочая копия Claude Code (…/проект/.claude/worktrees/имя) относится к самому проекту.
WORKTREE = re.compile(r"^(.+?)/\.claude/worktrees/[^/]+(?:/.*)?$")
HOME = re.compile(r"^/Users/[^/]+(?=/|$)")
NONE = ("", "без проекта")

_aliases: Dict[str, str] = {}
_ignore: List[str] = []


def _slug(name: str) -> str:
    return re.sub(r"[\s_]+", "-", name.strip().lower())


def configure(cfg: Optional[dict]) -> None:
    """Настройки из config.json: project_aliases {«как в базе»: «каким проектом считать»}
    и project_ignore — шаблоны путей или имён, которые проектом не считаются."""
    cfg = cfg or {}
    _aliases.clear()
    _aliases.update({_slug(k): str(v) for k, v in (cfg.get("project_aliases") or {}).items()})
    _ignore[:] = [str(x) for x in (cfg.get("project_ignore") or [])]


def project_key(project: Optional[str]) -> Tuple[str, str]:
    """Ключ и имя проекта. Папка ~/…/club-landing и репозиторий vladilenm/club_landing — один проект."""
    p = HOME.sub("~", (project or "").rstrip("/"))
    if "/scratch-workspaces/" in p:
        return "scratch", "Черновики"
    tree = WORKTREE.match(p)
    if tree:
        p = tree.group(1)
    if not p or NO_PROJECT.match(p):
        return NONE
    name = p.split("/")[-1] or p
    key = _slug(name)
    if any(fnmatch.fnmatchcase(p, pat) or fnmatch.fnmatchcase(key, _slug(pat)) for pat in _ignore):
        return NONE
    if key in _aliases:
        name = _aliases[key]
        key = _slug(name)
    return key, name


def listing(conn: sqlite3.Connection) -> List[dict]:
    """Проекты базы, свежие сверху. Записи без проекта — элемент с пустым ключом."""
    groups: Dict[str, dict] = {}
    for r in conn.execute("SELECT project, COUNT(*) AS n, MAX(updated_at) AS latest FROM records GROUP BY project"):
        key, name = project_key(r["project"])
        g = groups.setdefault(key, {"key": key, "names": Counter(), "paths": [], "count": 0, "latest": ""})
        g["names"][name] += r["n"]
        if r["project"]:
            g["paths"].append(r["project"])
        g["count"] += r["n"]
        g["latest"] = max(g["latest"], r["latest"] or "")
    items = [{"key": g["key"], "name": g["names"].most_common(1)[0][0], "paths": sorted(g["paths"]),
              "count": g["count"], "latest": g["latest"]} for g in groups.values()]
    items.sort(key=lambda p: p["latest"], reverse=True)
    return items


def find(conn: sqlite3.Connection, name: str) -> dict:
    """Проект по нестрогому имени: ключ, путь или их часть. Несколько кандидатов — ProjectError со списком."""
    query = (name or "").strip()
    if not query:
        raise ProjectError("Не указано имя проекта.")
    items = [p for p in listing(conn) if p["key"]]
    key = project_key(query)[0]
    exact = [p for p in items if p["key"] == key]
    if len(exact) == 1:
        return exact[0]
    low = query.lower()
    found = [p for p in items
             if key in p["key"] or low in p["name"].lower() or any(low in path.lower() for path in p["paths"])]
    if len(found) == 1:
        return found[0]
    if not found:
        raise ProjectError(f"Проект «{query}» не найден. Список проектов — инструмент project без имени.")
    names = ", ".join(p["name"] for p in found[:10])
    raise ProjectError(f"Под «{query}» подходит несколько проектов: {names}. Уточни имя.")
