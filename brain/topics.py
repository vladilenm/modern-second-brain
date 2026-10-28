"""Темы записей и похожие записи — для графа и карточек проектов в панели.

Тема — ключевое слово карточки, сведённое к основе: «рилс», «рилса», «рилсу» — одна тема.
Похожесть — косинус бинарных векторов тем с весом idf: редкая общая тема значит больше частой.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

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
