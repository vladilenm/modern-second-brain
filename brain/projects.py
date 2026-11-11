"""Проекты: ключ проекта, список проектов и поиск проекта по имени — общее для панели, CLI и MCP."""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from typing import Dict, List, Optional, Tuple


class ProjectError(ValueError):
    """Имя проекта пустое, не найдено или подходит нескольким проектам."""


def project_key(project: Optional[str]) -> Tuple[str, str]:
    """Ключ и имя проекта. Папка ~/…/club-landing и репозиторий vladilenm/club_landing — один проект."""
    p = (project or "").rstrip("/")
    if "/scratch-workspaces/" in p:
        return "scratch", "Черновики"
    if not p:
        return "", "без проекта"
    name = p.split("/")[-1] or p
    return re.sub(r"[\s_]+", "-", name.lower()), name


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
