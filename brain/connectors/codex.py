"""Сессии Codex: ~/.codex/sessions/ГГГГ/ММ/ДД/rollout-*.jsonl."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Tuple

from ..timeutil import to_iso
from .base import FileConnector, Record, clip, first_line, home_relative, iter_jsonl, render_dialog

ATTACHED_FILES = "# Files mentioned by the user"
MY_REQUEST = "## My request:"
MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


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


def assistant_text(blocks) -> str:
    return "\n".join(
        (b.get("text") or "") for b in blocks or [] if isinstance(b, dict) and b.get("type") == "output_text"
    ).strip()


class CodexConnector(FileConnector):
    name = "codex"

    def files(self) -> Iterable[Path]:
        for root in self.settings.get("roots", []):
            base = Path(root).expanduser()
            if base.is_dir():
                yield from sorted(base.rglob("rollout-*.jsonl"))

    def parse(self, path: Path) -> List[Record]:
        meta = None
        started = updated = None
        turns = []
        title = ""
        for item in iter_jsonl(path):
            payload = item.get("payload") or {}
            if item.get("type") == "session_meta" and meta is None:
                meta = payload
                # Ревьюеры (guardian) и порождённые агенты — не разговоры с человеком.
                if payload.get("thread_source") != "user":
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
            source=self.name,
            external_id=str(meta.get("id") or path.stem),
            title=title or first_line(user_turns[0]),
            body=render_dialog(turns),
            updated_at=updated or to_iso(path.stat().st_mtime),
            started_at=started,
            location=home_relative(str(path)),
            project=home_relative(meta.get("cwd")),
        )]
