"""Поиск: BM25 по FTS5 плюс поправка на свежесть записи."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .timeutil import age_days

# Веса колонок для bm25 в порядке объявления records_fts:
# title, summary, questions, keywords, atoms, body.
# Пересказ и ключевые слова весят больше сырого текста — ради этого их и пишем.
WEIGHTS = (6.0, 4.0, 4.0, 5.0, 3.0, 1.0)
WORD = re.compile(r"\w+", re.U)


def query_terms(query: str) -> List[str]:
    """Грубое стемминг-усечение: trigram ищет подстроки, поэтому «ошибки» → «ошиб»
    найдёт и «ошибка», и «ошибку». Слова короче трёх символов trigram не умеет.
    Полное слово тоже остаётся в запросе, чтобы точная форма весила больше:
    иначе «мозг» → «моз» поднимал бы «мозаику» наравне с «мозгом».
    Числа и идентификаторы с цифрами не трогаем: «2026» должно искаться целиком."""
    terms: List[str] = []
    for word in WORD.findall(query.lower()):
        if len(word) < 3:
            continue
        variants = [word]
        if word.isalpha() and len(word) >= 4:
            variants.append(word[:-2] if len(word) >= 6 else word[:-1])
        for v in variants:
            if v not in terms:
                terms.append(v)
    return terms


def search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 8,
    source: Optional[str] = None,
    half_life_days: float = 120,
    recency_weight: float = 0.35,
    project_paths: Optional[List[str]] = None,
    days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> List[dict]:
    terms = query_terms(query)
    if not terms:
        return []
    words = [w for w in WORD.findall(query.lower())]
    if len(words) > 1:
        # Точная фраза целиком — ещё одно слагаемое: записи, где она есть, поднимаются выше.
        terms = terms + [" ".join(words)]
    match = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
    weights = ", ".join(str(w) for w in WEIGHTS)
    # Отбор до ранжирования: источник, проект и давность только сужают набор, оценка считается как раньше.
    where, params = ["records_fts MATCH ?"], [match]
    if source:
        where.append("r.source = ?")
        params.append(source)
    if project_paths is not None:
        if not project_paths:
            return []
        where.append("r.project IN (" + ", ".join("?" * len(project_paths)) + ")")
        params.extend(project_paths)
    if days is not None:
        since = (now or datetime.now(timezone.utc)) - timedelta(days=days)
        where.append("r.updated_at > ?")
        params.append(since.strftime("%Y-%m-%dT%H:%M:%SZ"))
    sql = f"""
        SELECT r.id, r.source, r.title, r.ai_title, r.summary, r.project, r.location,
               r.updated_at, bm25(records_fts, {weights}) AS score,
               snippet(records_fts, -1, '', '', '…', 64) AS snippet
        FROM records_fts JOIN records r ON r.id = records_fts.rowid
        WHERE {" AND ".join(where)}
        ORDER BY score
        LIMIT ?
    """
    params.append(max(limit * 10, 100))
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return []
    best = max(-row["score"] for row in rows) or 1.0
    results = []
    for row in rows:
        relevance = -row["score"] / best
        freshness = 0.5 ** (age_days(row["updated_at"]) / half_life_days)
        results.append({
            "id": row["id"],
            "source": row["source"],
            "title": row["ai_title"] or row["title"],
            "summary": row["summary"],
            "snippet": row["snippet"],
            "project": row["project"],
            "location": row["location"],
            "updated_at": row["updated_at"],
            "score": round(relevance * (1 - recency_weight + recency_weight * freshness), 4),
        })
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def get_record(conn: sqlite3.Connection, rid: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM records WHERE id = ?", (rid,)).fetchone()
