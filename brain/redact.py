"""Маскировка секретов до записи в базу.

В разговорах с ИИ часто оказываются ключи и токены. Они не должны попасть
ни в поисковую выдачу, ни к модели, которая пишет пересказ.
"""
from __future__ import annotations

import re

MASK = "[секрет скрыт]"
CARD_MASK = "[карта скрыта]"
# 13–19 цифр подряд или группами через пробел/дефис. Проверка Луна отсекает номера заказов и телефоны.
CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _card(match: "re.Match") -> str:
    digits = re.sub(r"\D", "", match.group())
    return CARD_MASK if 13 <= len(digits) <= 19 and _luhn(digits) else match.group()

# (шаблон, замена). Где есть подпись («ключ:», api_key=, Bearer) — подпись оставляем.
RULES = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), MASK),
    (re.compile(r"\b(?:sk|sc|pk|rk)[-_][A-Za-z0-9_-]{20,}"), MASK),  # sk-…, sk-ant-…, sc_…, pk_live_…
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), MASK),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"), MASK),
    (re.compile(r"\b(?:ntn_|secret_)[A-Za-z0-9_-]{20,}"), MASK),  # Notion
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), MASK),  # JWT
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"), MASK),  # токен Telegram-бота
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"), r"\1" + MASK),
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|пароль|ключ)(\s*[:=]\s*)([\"']?)"
                r"[A-Za-z0-9_\-./+=]{16,}\3"), r"\1\2\3" + MASK + r"\3"),
    (CARD, _card),  # номера банковских карт — чаще всего в письмах
]


def redact(text: str) -> str:
    for pattern, replacement in RULES:
        text = pattern.sub(replacement, text)
    return text
