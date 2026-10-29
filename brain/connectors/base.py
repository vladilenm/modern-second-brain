"""Общий контракт коннекторов.

Коннектор отвечает на один вопрос: «что появилось или изменилось с прошлого раза?»
и возвращает записи в общем формате Record. Дубли, индекс, пересказ и свежесть
делает остальная система.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

MAX_MESSAGE_CHARS = 20000
# launchd не передаёт PATH Homebrew, поэтому zstd ищем и там.
ZSTD_FALLBACKS = ("/opt/homebrew/bin/zstd", "/usr/local/bin/zstd")


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
    kind: str = "dialog"  # dialog | discussion | changelog | document — от него зависит промпт пересказа


@dataclass
class Batch:
    """Пачка записей API-коннектора и позиция, до которой всё в ней уже отдано."""
    records: List[Record]
    cursor: str


class Connector:
    """Общее у всех коннекторов: ключ настроек, источник записей и как его показывать людям."""

    name = ""        # ключ в config.sources и в sync_state
    source = ""      # значение Record.source
    title = ""       # название для людей: «Claude Code», «GitHub»
    about = ""       # что лежит в источнике — для описания MCP-сервера
    empty_hint = ""  # что сделать, если записей пока нет; панель показывает это вместо пустой строки
    default_every_minutes = 0  # 0 — в каждом прогоне sync

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.settings = cfg["sources"].get(self.name, {})

    @property
    def every_seconds(self) -> float:
        """Как часто запускать источник (every_minutes в config)."""
        return float(self.settings.get("every_minutes", self.default_every_minutes)) * 60

    def excludes(self, project: Optional[str]) -> bool:
        """Своё правило исключения записей сверх общего exclude_projects."""
        return False


class FileConnector(Connector):
    """Коннектор к источнику, который лежит файлами на диске.

    Курсор ведёт sync.py: файл перечитывается, только если у него изменились
    время или размер, и только когда в нём idle_minutes ничего не менялось
    (чтобы не пересказывать недописанный разговор).
    """

    # Один файл — одна запись. Тогда разница между просмотренными файлами и записями —
    # служебные сессии без разговора, и панель её показывает.
    one_record_per_file = True

    @property
    def idle_seconds(self) -> float:
        return float(self.settings.get("idle_minutes", 30)) * 60

    def roots(self) -> List[str]:
        """Папки, которые читает коннектор."""
        return [str(Path(r).expanduser()) for r in self.settings.get("roots", [])]

    def files(self) -> Iterable[Path]:
        raise NotImplementedError

    def parse(self, path: Path) -> List[Record]:
        raise NotImplementedError


class ApiConnector(Connector):
    """Коннектор к источнику за API: отвечает «что нового после курсора» пачками.

    Формат курсора знает только сам коннектор. sync хранит его в базе и сдвигает
    в одном коммите с записями пачки: упали посреди — записанное остаётся,
    следующий прогон продолжит с последней записанной пачки.
    """

    default_every_minutes = 60

    @property
    def backfill_days(self) -> int:
        """На сколько дней назад забирать при первом запуске."""
        return int(self.settings.get("backfill_days", 730))

    def fetch(self, cursor: Optional[str]) -> Iterator[Batch]:
        raise NotImplementedError


def zstd_path() -> str:
    path = shutil.which("zstd") or next((p for p in ZSTD_FALLBACKS if os.path.exists(p)), None)
    if not path:
        raise RuntimeError("не найден zstd — поставь его: brew install zstd")
    return path


def read_lines(path: Path) -> Iterator[str]:
    """Строки текстового файла. Сжатые .zst распаковывает zstd: в Python 3.9 модуля для него нет."""
    if path.suffix != ".zst":
        with open(path, encoding="utf-8", errors="ignore") as fh:
            yield from fh
        return
    with subprocess.Popen([zstd_path(), "-dcq", "--", str(path)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        done = False
        try:
            with io.TextIOWrapper(proc.stdout, encoding="utf-8", errors="ignore") as text:
                yield from text
            done = True
        finally:
            if not done:
                proc.kill()  # разбор бросил файл на середине — распаковывать остальное незачем
        err = proc.stderr.read().decode("utf-8", "ignore").strip()
        if proc.wait():
            raise RuntimeError(f"zstd: {err or 'не удалось распаковать'}")


def iter_jsonl(path: Path) -> Iterator[dict]:
    for line in read_lines(path):
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
