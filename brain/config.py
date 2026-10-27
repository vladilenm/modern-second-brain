"""Настройки: значения по умолчанию плюс config.json в корне проекта."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "db_path": str(ROOT / "data" / "brain.db"),
    "inbox": str(ROOT / "inbox"),
    # Папки проектов, разговоры из которых в базу не берём (и удаляем, если уже есть).
    # Подпапки тоже исключаются: "~/Documents/dev/bot" убирает и "~/Documents/dev/bot/worker".
    "exclude_projects": [],
    "llm": {
        # Любой OpenAI-совместимый API: OpenRouter, DeepSeek, Gemini, Ollama и т.д.
        "base_url": "",
        "api_key": "",
        "model": "",
        "json_mode": True,
        "timeout": 180,
        # Длинные разговоры режем: начало + конец, чтобы не платить за мегабайты.
        "max_input_chars": 60000,
        # Сколько записей пересказывать за один запуск sync.
        "batch_limit": 40,
    },
    "search": {
        # Через сколько дней вес свежести падает вдвое.
        "half_life_days": 120,
        # Доля итоговой оценки, которая зависит от свежести (0 — не учитывать дату).
        "recency_weight": 0.35,
    },
    "sources": {
        "claude_code": {"enabled": True, "roots": ["~/.claude/projects"], "idle_minutes": 30},
        "codex": {"enabled": True, "roots": ["~/.codex/sessions", "~/.codex/archived_sessions"], "idle_minutes": 30},
        "chatgpt_export": {"enabled": True, "idle_minutes": 1},
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def config_path() -> Path:
    return Path(os.environ.get("BRAIN_CONFIG", ROOT / "config.json"))


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    path = config_path()
    if path.exists():
        cfg = _merge(cfg, json.loads(path.read_text(encoding="utf-8")))
    if os.environ.get("BRAIN_DB"):
        cfg["db_path"] = os.environ["BRAIN_DB"]
    if os.environ.get("BRAIN_LLM_API_KEY"):
        cfg["llm"]["api_key"] = os.environ["BRAIN_LLM_API_KEY"]
    return cfg
