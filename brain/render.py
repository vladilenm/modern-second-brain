"""Текстовое представление результатов — общее для CLI и MCP."""
from __future__ import annotations

import sqlite3
from typing import List

SOURCE_NAMES = {"claude_code": "Claude Code", "codex": "Codex", "chatgpt": "ChatGPT"}


def result_text(r: dict) -> str:
    meta = " · ".join(x for x in (SOURCE_NAMES.get(r["source"], r["source"]), r["updated_at"][:10], r["project"]) if x)
    lines = [f"[id {r['id']}] {r['title']}", f"  {meta}"]
    text = r["summary"] or r["snippet"]
    if text:
        text = " ".join(text.split())
        lines.append("  " + (text if len(text) <= 500 else text[:499] + "…"))
    return "\n".join(lines)


def results_text(results: List[dict]) -> str:
    return "\n\n".join(result_text(r) for r in results)


def render_record(row: sqlite3.Row, offset: int = 0, max_chars: int = 20000) -> str:
    parts = [
        f"[id {row['id']}] {row['ai_title'] or row['title']}",
        f"Источник: {SOURCE_NAMES.get(row['source'], row['source'])}",
        f"Даты: {row['started_at'] or '—'} — {row['updated_at']}",
        f"Проект: {row['project'] or '—'}",
        f"Файл: {row['location'] or '—'}",
    ]
    if offset == 0:
        if row["summary"]:
            parts.append("\nКратко:\n" + row["summary"])
        if row["atoms"]:
            parts.append("\nАтомы:\n" + row["atoms"])
    body = row["body"]
    end = min(len(body), offset + max_chars)
    parts.append(f"\nРазговор (символы {offset}–{end} из {len(body)}):\n" + body[offset:end])
    if end < len(body):
        parts.append(f"\n…продолжение: offset={end}")
    return "\n".join(parts)
