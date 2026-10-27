"""Маскировка секретов до записи в базу.

В разговорах с ИИ часто оказываются ключи и токены. Они не должны попасть
ни в поисковую выдачу, ни к модели, которая пишет пересказ.
"""
from __future__ import annotations

import re

MASK = "[секрет скрыт]"

# (шаблон, замена). Где есть подпись («ключ:», api_key=, Bearer) — подпись оставляем.
RULES = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), MASK),
    (re.compile(r"\b(?:sk|sc|pk|rk)[-_][A-Za-z0-9_-]{20,}"), MASK),  # sk-…, sk-ant-…, sc_…, pk_live_…
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), MASK),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), MASK),  # JWT
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"), MASK),  # токен Telegram-бота
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"), r"\1" + MASK),
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|пароль|ключ)(\s*[:=]\s*)([\"']?)"
                r"[A-Za-z0-9_\-./+=]{16,}\3"), r"\1\2\3" + MASK + r"\3"),
]


def redact(text: str) -> str:
    for pattern, replacement in RULES:
        text = pattern.sub(replacement, text)
    return text
