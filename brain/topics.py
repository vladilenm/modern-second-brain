"""Темы записей и похожие записи — для графа и карточек проектов в панели.

Тема — ключевое слово карточки, сведённое к основе: «рилс», «рилса», «рилсу» — одна тема.
Похожесть — косинус бинарных векторов тем с весом idf: редкая общая тема значит больше частой.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

MONTHS = "январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр"
DATE = re.compile(rf"^(\d{{4}}-\d\d(-\d\d)?|\d{{1,2}} ({MONTHS})[а-я]* \d{{4}}|({MONTHS})[а-я]* \d{{4}}|\d{{4}})$")
NUMBER = re.compile(r"^[\d\s.,:×x%+\-/]+$")
# Только расширения файлов: «next.js» и «node.js» — технологии, а не файлы.
FILE = re.compile(r"^[^\s/]+\.(md|mdx|mp4|mov|m4a|mp3|wav|srt|ass|vtt|json|jsonl|txt|py|ts|tsx|jsx|html|css|png"
                  r"|jpe?g|webp|gif|svg|pdf|ya?ml|toml|sh|csv|log|plist|db|sqlite|swift|lock|env)$")
ENDINGS = sorted("ами ями ого его ому ему ыми ими ов ев ей ой ий ый ая яя ое ее ые ие ом ем ах ях ам ям "
                 "а я ы и у ю е о".split(), key=len, reverse=True)
STOP = set("для как что это или при про над под без его еще ещё уже так тоже чтобы если когда где".split())


def _stem(word: str) -> str:
    if re.fullmatch(r"[а-я]+", word) and len(word) > 4:
        for e in ENDINGS:
            if word.endswith(e) and len(word) - len(e) >= 3:
                return word[:-len(e)]
    elif re.fullmatch(r"[a-z]+", word) and len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalize(keyword: str) -> Optional[str]:
    k = " ".join((keyword or "").lower().replace("ё", "е").split()).strip(" .,;:!?«»\"'()")
    if len(k) < 3 or DATE.match(k) or NUMBER.match(k) or FILE.match(k):
        return None
    return " ".join(_stem(w) for w in k.split(" "))


def record_topics(keywords: Optional[str], title: Optional[str]) -> List[Tuple[str, str]]:
    """Темы записи без повторов: из ключевых слов карточки, а если их нет — из слов названия."""
    raw = [w.strip() for w in (keywords or "").split(",") if w.strip()]
    if not raw:
        raw = [w for w in re.findall(r"[\w-]+", (title or "").lower()) if len(w) > 3 and w not in STOP]
    out, seen = [], set()
    for w in raw:
        key = normalize(w)
        if key and key not in seen:
            seen.add(key)
            out.append((key, w))
    return out


MIN_SIM = 0.12
MAX_NEIGHBORS = 3
MAX_SHARE = 0.25   # тема из большей доли записей ничего не различает («claude code»)
SHOWN = 8


@dataclass
class Topics:
    shown: Dict[int, List[str]]
    neighbors: Dict[int, List[Tuple[int, float]]]
    pairs: List[Tuple[int, int, float]]
    keys: Dict[int, List[str]]
    idf: Dict[str, float]
    display: Dict[str, str]

    def project_topics(self, ids: List[int], limit: int = 5) -> List[str]:
        """Темы проекта: повторяющиеся в его записях важнее, внутри — по «число записей × idf»."""
        count = Counter(k for i in ids for k in self.keys.get(i, []))
        many = len(ids) >= 3
        ranked = sorted(count, key=lambda k: (many and count[k] < 2, -count[k] * self.idf[k], k))
        return [self.display[k] for k in ranked[:limit]]


def build(rows: Iterable[Tuple[int, Optional[str], Optional[str]]]) -> Topics:
    per = {rid: record_topics(kw, title) for rid, kw, title in rows}
    n = len(per)
    forms: Dict[str, Counter] = defaultdict(Counter)
    for ts in per.values():
        for key, word in ts:
            forms[key][word] += 1
    keys = {rid: [k for k, _ in ts] for rid, ts in per.items()}
    df = Counter(k for ks in keys.values() for k in ks)
    idf = {k: math.log(n / d) for k, d in df.items()}
    display = {k: c.most_common(1)[0][0] for k, c in forms.items()}
    cap = MAX_SHARE * n if n >= 20 else n
    useful = {k for k, d in df.items() if 2 <= d <= cap and idf[k] > 0}
    shown = {rid: [display[k] for k in sorted(ks, key=lambda k: (k not in useful, -idf[k], k))[:SHOWN]]
             for rid, ks in keys.items()}

    vec = {rid: [k for k in ks if k in useful] for rid, ks in keys.items()}
    norm = {rid: math.sqrt(sum(idf[k] ** 2 for k in v)) for rid, v in vec.items()}
    postings: Dict[str, List[int]] = defaultdict(list)
    for rid, v in vec.items():
        for k in v:
            postings[k].append(rid)
    neighbors: Dict[int, List[Tuple[int, float]]] = {}
    edges: Dict[Tuple[int, int], float] = {}
    for rid, v in vec.items():
        dot: Dict[int, float] = defaultdict(float)
        for k in v:
            for other in postings[k]:
                if other != rid:
                    dot[other] += idf[k] ** 2
        sims = sorted(((round(s / (norm[rid] * norm[o]), 3), o) for o, s in dot.items()), key=lambda x: (-x[0], x[1]))
        neighbors[rid] = [(o, s) for s, o in sims if s >= MIN_SIM][:MAX_NEIGHBORS]
        for o, s in neighbors[rid]:
            edges[(min(rid, o), max(rid, o))] = s
    pairs = sorted((a, b, w) for (a, b), w in edges.items())
    return Topics(shown, neighbors, pairs, keys, idf, display)
