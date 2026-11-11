"""Контекст для нейронки и терминала: что было недавно и карточка проекта — готовым текстом."""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from . import projects, render
from .timeutil import parse

# Основные знания идут первыми и в этом порядке; команды и факты — счётчиком.
MAIN = ("decision", "todo", "error", "fix")
FORMS = {
    "decision": ("решение", "решения", "решений"), "todo": ("задача", "задачи", "задач"),
    "error": ("ошибка", "ошибки", "ошибок"), "fix": ("исправление", "исправления", "исправлений"),
    "command": ("команда", "команды", "команд"), "fact": ("факт", "факта", "фактов"),
}
RECORDS = ("запись", "записи", "записей")
PROJECTS = ("проект", "проекта", "проектов")
DAYS = ("день", "дня", "дней")
KNOWLEDGE = ("знание", "знания", "знаний")
TOP_ATOMS, TOP_BARE, TOP_PROJECTS = 5, 3, 10
LIST_LIMIT, CARD_RECORDS, CARD_ATOMS, SUMMARY_CHARS = 50, 10, 10, 300
ATOM = re.compile(r"^\[(\w+)\]\s*(.*)$", re.S)
ISO = "%Y-%m-%dT%H:%M:%SZ"


def words(n: int, forms: Tuple[str, str, str]) -> str:
    a = abs(n) % 100
    b = a % 10
    form = forms[2] if 10 < a < 20 else forms[1] if 1 < b < 5 else forms[0] if b == 1 else forms[2]
    return f"{n} {form}"


def one_line(text: str) -> str:
    return " ".join((text or "").split())


def atoms_of(text: Optional[str]) -> List[Tuple[str, str]]:
    """Строки поля atoms вида «[decision] текст» → пары (тип, текст). Строка без типа получает тип ''."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = ATOM.match(line)
        out.append((m.group(1), m.group(2)) if m else ("", line))
    return out


def int_arg(value, name: str, low: int, high: Optional[int]) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: нужно целое число")
    if number < low or (high is not None and number > high):
        raise ValueError(f"{name}: допустимо от {low}" + (f" до {high}" if high is not None else ""))
    return number


def _label(kind: str) -> str:
    return FORMS[kind][0] if kind in FORMS else "знание"


def _group_text(g: dict, full: bool) -> str:
    tally = [words(len(g["records"]), RECORDS)]
    for kind in MAIN:
        count = sum(1 for a in g["main"] if a[0] == kind)
        if count:
            tally.append(words(count, FORMS[kind]))
    lines = [f"## {g['name']} — {' · '.join(tally)}"]
    main = g["main"] if full else g["main"][:TOP_ATOMS]
    for kind, text, rid, cont in main + (g["rest"] if full else []):
        lines.append(f"- {_label(kind)}: {one_line(text)} [id {rid}{', продолжение' if cont else ''}]")
    if not full:
        tail = []
        if len(g["main"]) > len(main):
            tail.append(f"ещё {words(len(g['main']) - len(main), KNOWLEDGE)}")
        if g["rest"]:
            tail.append(f"команды и факты: {len(g['rest'])}")
        if tail:
            lines.append("…" + ", ".join(tail))
    bare = g["bare"] if full else g["bare"][:TOP_BARE]
    if bare:
        more = len(g["bare"]) - len(bare)
        lines.append("Без знаний: " + "; ".join(f"«{one_line(r['ai_title'] or r['title'])}» [id {r['id']}]" for r in bare)
                     + (f"; …ещё {more}" if more else ""))
    return "\n".join(lines)


def recent(conn: sqlite3.Connection, days=3, project: Optional[str] = None, source: Optional[str] = None,
           now: Optional[datetime] = None) -> str:
    """Что изменилось за days суток: по проектам решения, задачи, ошибки и исправления из карточек."""
    days = int_arg(days, "days", 1, 30)
    since = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    wanted = projects.find(conn, project) if project else None
    sql = "SELECT id, source, title, ai_title, project, started_at, updated_at, atoms FROM records WHERE updated_at > ?"
    params = [since.strftime(ISO)]
    if source:
        sql += " AND source = ?"
        params.append(source)
    groups: dict = {}
    count = 0
    for r in conn.execute(sql + " ORDER BY updated_at DESC", params):
        key, name = projects.project_key(r["project"])
        if wanted and key != wanted["key"]:
            continue
        count += 1
        g = groups.setdefault(key, {"key": key, "name": name if key else "Без проекта",
                                    "records": [], "main": [], "rest": [], "bare": []})
        g["records"].append(r)
        items = atoms_of(r["atoms"])
        if not items:
            g["bare"].append(r)
            continue
        # Запись начата до начала периода — продолжение: у атомов нет своей даты.
        started = parse(r["started_at"])
        cont = bool(started and started <= since)
        for kind, text in items:
            (g["main"] if kind in MAIN else g["rest"]).append((kind, text, r["id"], cont))
    if not count:
        latest = conn.execute("SELECT MAX(updated_at) FROM records").fetchone()[0]
        return f"За {words(days, DAYS)} записей не было." + (
            f" Последняя запись в базе — {latest[:10]}." if latest else " База пуста.")
    for g in groups.values():
        g["main"].sort(key=lambda a: MAIN.index(a[0]))
    period = f"Что нового за {words(days, DAYS)}"
    if wanted:
        g = groups[wanted["key"]]
        g["name"] = wanted["name"]
        return f"{period} в проекте {wanted['name']}: {words(count, RECORDS)}\n\n" + _group_text(g, full=True)
    # Строки пришли от свежих к старым, сортировка устойчива: при равном числе знаний свежий проект выше.
    keyed = sorted((g for g in groups.values() if g["key"]), key=lambda g: -len(g["main"]))
    shown = keyed[:TOP_PROJECTS] + [g for g in groups.values() if not g["key"]]
    head = f"{period}: {words(count, RECORDS)}" + (f", {words(len(keyed), PROJECTS)}" if keyed else "")
    parts = [head] + [_group_text(g, full=False) for g in shown]
    hidden = keyed[TOP_PROJECTS:]
    if hidden:
        parts.append(f"…ещё {words(len(hidden), PROJECTS)}: "
                     + ", ".join(f"{g['name']} ({words(len(g['records']), RECORDS)})" for g in hidden)
                     + ". Сузь параметром project.")
    return "\n\n".join(parts)


def _project_list(conn: sqlite3.Connection) -> str:
    items = projects.listing(conn)
    keyed = [p for p in items if p["key"]]
    loose = sum(p["count"] for p in items if not p["key"])
    lines = [f"{p['name']} — {words(p['count'], RECORDS)}, последняя {p['latest'][:10]}" for p in keyed[:LIST_LIMIT]]
    if len(keyed) > LIST_LIMIT:
        lines.append(f"…ещё {words(len(keyed) - LIST_LIMIT, PROJECTS)}")
    return f"Проектов: {len(keyed)}, записей без проекта: {loose}" + ("\n\n" + "\n".join(lines) if lines else "")


def project(conn: sqlite3.Connection, name: Optional[str] = None) -> str:
    """Без имени — список проектов. С именем — пути, источники, последние записи, решения и задачи."""
    if not (name or "").strip():
        return _project_list(conn)
    p = projects.find(conn, name)
    rows = [r for r in conn.execute(
        """SELECT id, source, title, ai_title, summary, project, started_at, updated_at, atoms
           FROM records WHERE project IS NOT NULL ORDER BY updated_at DESC""")
        if projects.project_key(r["project"])[0] == p["key"]]
    sources = Counter(render.source_name(r["source"]) for r in rows)
    first = min((r["started_at"] or r["updated_at"]) for r in rows)[:10]
    parts = [f"Проект {p['name']}\n"
             f"Пути: {'; '.join(p['paths'])}\n"
             f"Записей: {len(rows)} ({', '.join(f'{s} {c}' for s, c in sources.most_common())})"
             f" · {first} — {rows[0]['updated_at'][:10]}"]
    lines = ["Последние записи:"]
    for r in rows[:CARD_RECORDS]:
        lines.append(f"[id {r['id']}] {one_line(r['ai_title'] or r['title'])} · "
                     f"{render.source_name(r['source'])} · {r['updated_at'][:10]}")
        summary = one_line(r["summary"])
        if summary:
            lines.append("  " + (summary if len(summary) <= SUMMARY_CHARS else summary[:SUMMARY_CHARS - 1] + "…"))
    parts.append("\n".join(lines))
    for kind, title in (("decision", "Последние решения:"),
                        ("todo", "Последние задачи (статус выполнения в базе не хранится):")):
        found = [f"- {one_line(text)} [id {r['id']}, {r['updated_at'][:10]}]"
                 for r in rows for k, text in atoms_of(r["atoms"]) if k == kind][:CARD_ATOMS]
        if found:
            parts.append(title + "\n" + "\n".join(found))
    return "\n\n".join(parts)
