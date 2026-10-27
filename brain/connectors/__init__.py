"""Реестр коннекторов. Новый источник = новый файл + строка в ALL + секция в config."""
from __future__ import annotations

from typing import List

from .base import FileConnector
from .chatgpt_export import ChatGPTExportConnector
from .claude_code import ClaudeCodeConnector
from .codex import CodexConnector

ALL = {c.name: c for c in (ClaudeCodeConnector, CodexConnector, ChatGPTExportConnector)}


def enabled(cfg: dict, only: str = None) -> List[FileConnector]:
    out = []
    for name, cls in ALL.items():
        if only and name != only:
            continue
        if cfg["sources"].get(name, {}).get("enabled"):
            out.append(cls(cfg))
    return out
