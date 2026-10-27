"""Общий контракт коннекторов.

Коннектор отвечает на один вопрос: «что появилось или изменилось с прошлого раза?»
и возвращает записи в общем формате Record. Дубли, индекс, пересказ и свежесть
делает остальная система.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

MAX_MESSAGE_CHARS = 20000


@dataclass
class Record:
    source: str
    external_id: str
    title: str
    body: str
    updated_at: str
    started_at: Optional[str] = None
    location: Optional[str] = None
    project: Optional[str] = None


class FileConnector:
    """Коннектор к источнику, который лежит файлами на диске.

    Курсор ведёт sync.py: файл перечитывается, только если у него изменились
    время или размер, и только когда в нём idle_minutes ничего не менялось
    (чтобы не пересказывать недописанный разговор).
    """

    name = ""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.settings = cfg["sources"].get(self.name, {})

    @property
    def idle_seconds(self) -> float:
        return float(self.settings.get("idle_minutes", 30)) * 60

    def files(self) -> Iterable[Path]:
        raise NotImplementedError

    def parse(self, path: Path) -> List[Record]:
        raise NotImplementedError


def iter_jsonl(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                yield item


def clip(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[обрезано {len(text) - limit} символов]"


def first_line(text: str, limit: int = 80) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def render_dialog(turns: List[tuple]) -> str:
    return "\n\n".join(f"{who}: {text}" for who, text in turns)


def home_relative(path: Optional[str]) -> Optional[str]:
    if not path:
        return path
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home) else path
