"""Сессии Codex: ~/.codex/sessions/ГГГГ/ММ/ДД/rollout-*.jsonl.

Закончившиеся сессии Codex со временем сжимает в rollout-*.jsonl.zst — читаем и их.

Правка сообщения заводит ветку — новый файл rollout-…-<тред>_<ветка>.jsonl. В нём только строки
после правки, а начало истории — в session_meta.history_base: строки файла thread_id до
end_ordinal_exclusive (у основного файла это id треда, у ветки — id после «_»). Разговор
собираем по самой новой ветке, брошенные ветки в него не попадают."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from ..timeutil import to_iso
from .base import FileConnector, Record, clip, first_line, home_relative, iter_jsonl, render_dialog

ATTACHED_FILES = "# Files mentioned by the user"
MY_REQUEST = "## My request:"
MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
ROLLOUT = re.compile(r"rollout-(?P<started>[\dT-]{19})-(?P<thread>[0-9a-f-]{36})(?:_(?P<branch>[0-9a-f-]{36}))?"
                     r"\.jsonl(?:\.zst)?$")


def user_text(blocks) -> Tuple[str, str]:
    """Возвращает (текст для базы, текст для заголовка)."""
    parts, typed = [], []
    for b in blocks or []:
        if not isinstance(b, dict) or b.get("type") != "input_text":
            continue
        text = (b.get("text") or "").strip()
        # Всё в тегах (<environment_context>, <image>, контекст браузера и приложений)
        # и инструкции AGENTS.md Codex подкладывает сам.
        if not text or text.startswith("<") or text.startswith("# AGENTS.md instructions"):
            continue
        if text.startswith(ATTACHED_FILES):
            # Список приложенных файлов, а после «## My request:» — сам запрос (или он в следующем блоке).
            files, _, text = text.partition(MY_REQUEST)
            parts.append(clip(files.strip(), 3000))
            text = text.strip()
            if not text:
                continue
        text = MARKDOWN_LINK.sub(r"\1", text)
        parts.append(text)
        typed.append(text)
    return "\n\n".join(parts), "\n".join(typed)


def from_human(meta: dict) -> bool:
    """Разговор с человеком, а не ревьюер (guardian) или порождённый агент.
    В старых сессиях thread_source нет: там у человека source — строка ("vscode", "cli"),
    а у агентов — объект {"subagent": …}."""
    if meta.get("thread_source"):
        return meta["thread_source"] == "user"
    return meta.get("source") in ("vscode", "cli")


def assistant_text(blocks) -> str:
    return "\n".join(
        (b.get("text") or "") for b in blocks or [] if isinstance(b, dict) and b.get("type") == "output_text"
    ).strip()


class CodexConnector(FileConnector):
    name = "codex"
    source = "codex"
    title = "Codex"
    about = "разговоры в Codex"
    _parts: Optional[Dict[str, Path]] = None   # файл каждого куска истории: основного и веток
    _newest: Optional[Dict[str, Path]] = None  # самая новая ветка треда

    def files(self) -> Iterable[Path]:
        for root in self.settings.get("roots", []):
            base = Path(root).expanduser()
            if base.is_dir():
                yield from sorted(p for p in base.rglob("rollout-*.jsonl*")
                                  if p.name.endswith((".jsonl", ".jsonl.zst")))

    def _index(self) -> None:
        self._parts, self._newest, started = {}, {}, {}
        for p in self.files():
            m = ROLLOUT.match(p.name)
            if not m:
                continue
            self._parts[m["branch"] or m["thread"]] = p
            if m["started"] >= started.get(m["thread"], ""):
                started[m["thread"]] = m["started"]
                self._newest[m["thread"]] = p

    def _history(self, path: Path, end: Optional[int] = None) -> Iterator[dict]:
        """Строки файла вместе с началом истории, от которого отошла ветка; end — до какой строки."""
        items = iter_jsonl(path)
        try:
            first = next(items, None)
            if first is None:
                return
            base = (first.get("payload") or {}).get("history_base") if first.get("type") == "session_meta" else None
            parent = self._parts.get(base.get("thread_id")) if isinstance(base, dict) else None
            if parent and parent != path:
                yield from self._history(parent, base.get("end_ordinal_exclusive"))
            yield first
            for item in items:
                ordinal = item.get("ordinal")
                if end is not None and isinstance(ordinal, int) and ordinal >= end:
                    break
                yield item
        finally:
            items.close()  # бросили файл на середине — zstd больше не нужен

    def parse(self, path: Path) -> List[Record]:
        if self._parts is None:
            self._index()
        m = ROLLOUT.match(path.name)
        if m:
            path = self._newest.get(m["thread"], path)
        meta = None
        started = updated = None
        turns = []
        title = ""
        for item in self._history(path):
            payload = item.get("payload") or {}
            if item.get("type") == "session_meta" and meta is None:
                meta = payload
                if not from_human(payload):
                    return []
                continue
            if item.get("type") != "response_item" or payload.get("type") != "message":
                continue
            role = payload.get("role")
            if role == "user":
                (text, typed), who = user_text(payload.get("content")), "Пользователь"
                title = title or first_line(typed)
            elif role == "assistant":
                text, who = assistant_text(payload.get("content")), "Codex"
            else:
                continue
            if not text:
                continue
            ts = to_iso(item.get("timestamp"))
            started = started or ts
            updated = ts or updated
            turns.append((who, clip(text)))
        user_turns = [t for who, t in turns if who == "Пользователь"]
        if meta is None or not user_turns:
            return []
        return [Record(
            source=self.source,
            external_id=str(meta.get("id") or path.name.split(".", 1)[0]),
            title=title or first_line(user_turns[0]),
            body=render_dialog(turns),
            updated_at=updated or to_iso(path.stat().st_mtime),
            started_at=started,
            location=home_relative(str(path)),
            project=home_relative(meta.get("cwd")),
        )]
