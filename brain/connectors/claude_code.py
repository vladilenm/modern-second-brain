"""Сессии Claude Code (CLI и вкладка Code в приложении): ~/.claude/projects/<проект>/<id>.jsonl."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List

from ..timeutil import to_iso
from .base import FileConnector, Record, clip, first_line, home_relative, iter_jsonl, render_dialog

# Служебные сообщения, которые Claude Code подкладывает от имени пользователя.
SKIP_PREFIXES = (
    "<task-notification>",
    "Another Claude session",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<local-command-caveat>",
    "Caveat: The messages below",
    "[Request interrupted",
)
DROP_BLOCKS = re.compile(r"<(system-reminder|command-message)>.*?</\1>", re.S)
UNWRAP_TAGS = re.compile(r"</?(?:command-name|command-args|pasted_content)[^>]*>")
IMAGE_LINES = re.compile(r"^\[Image[^\]\n]*\]\s*$", re.M)


def clean_user_text(content) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        # tool_result — это вывод инструментов, а не слова человека.
        text = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    else:
        return ""
    if text.lstrip().startswith(SKIP_PREFIXES):
        return ""
    text = DROP_BLOCKS.sub("", text)
    text = UNWRAP_TAGS.sub("", text)
    text = IMAGE_LINES.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def assistant_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text").strip()
    return ""


class ClaudeCodeConnector(FileConnector):
    name = "claude_code"

    def files(self) -> Iterable[Path]:
        for root in self.settings.get("roots", []):
            base = Path(root).expanduser()
            if base.is_dir():
                # Только основные сессии: подпапки subagents/ — это побочные ветки агентов.
                yield from sorted(base.glob("*/*.jsonl"))

    def parse(self, path: Path) -> List[Record]:
        title = ""
        cwd = None
        started = updated = None
        turns = []
        for item in iter_jsonl(path):
            kind = item.get("type")
            if kind == "custom-title" and item.get("customTitle"):
                title = item["customTitle"]
                continue
            if kind not in ("user", "assistant") or item.get("isSidechain") or item.get("isMeta"):
                continue
            message = item.get("message") or {}
            if kind == "user":
                text, who = clean_user_text(message.get("content")), "Пользователь"
            else:
                text, who = assistant_text(message.get("content")), "Claude"
            if not text:
                continue
            ts = to_iso(item.get("timestamp"))
            started = started or ts
            updated = ts or updated
            cwd = cwd or item.get("cwd")
            turns.append((who, clip(text)))
        user_turns = [t for who, t in turns if who == "Пользователь"]
        if not user_turns:
            return []
        updated = updated or to_iso(path.stat().st_mtime)
        return [Record(
            source=self.name,
            external_id=path.stem,
            title=title or first_line(user_turns[0]),
            body=render_dialog(turns),
            updated_at=updated,
            started_at=started,
            location=home_relative(str(path)),
            project=home_relative(cwd),
        )]
