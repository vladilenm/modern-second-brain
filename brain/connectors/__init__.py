"""Реестр коннекторов. Новый источник = новый файл + строка в ALL + секция в config.

Отсюда же берут список источников и их названия CLI, MCP, панель и значок в строке меню."""
from __future__ import annotations

from typing import Dict, List

from .base import ApiConnector, Batch, Connector, FileConnector
from .chatgpt_export import ChatGPTExportConnector
from .claude_code import ClaudeCodeConnector
from .codex import CodexConnector
from .github import GitHubConnector
from .gmail import GmailConnector
from .kimi import KimiConnector
from .notion import NotionConnector
from .yandex_mail import YandexMailConnector

ALL = {c.name: c for c in (ClaudeCodeConnector, CodexConnector, ChatGPTExportConnector, GitHubConnector,
                           GmailConnector, YandexMailConnector, KimiConnector, NotionConnector)}


def enabled(cfg: dict, only: str = None) -> List[Connector]:
    out = []
    for name, cls in ALL.items():
        if only and name != only:
            continue
        if cfg["sources"].get(name, {}).get("enabled"):
            out.append(cls(cfg))
    return out


def registry() -> List[dict]:
    """Источники в порядке показа. source — ключ записей, connector — ключ настроек и sync_state."""
    return [{"source": c.source, "connector": c.name, "title": c.title, "about": c.about,
             "type": "file" if issubclass(c, FileConnector) else "api", "hint": c.empty_hint}
            for c in ALL.values()]


def source_names() -> Dict[str, str]:
    return {c.source: c.title for c in ALL.values()}
