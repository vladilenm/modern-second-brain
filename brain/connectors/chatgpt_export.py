"""Экспорт ChatGPT: архив из Settings → Data controls → Export data, положенный в папку inbox/.

API для чтения истории у OpenAI нет, поэтому источник полуручной: кладёшь новый
архив — скрипт добавляет новые и изменившиеся чаты, остальные отсекает по хешу.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional

from ..timeutil import to_iso
from .base import FileConnector, Record, clip, first_line, render_dialog

CONVERSATIONS_FILE = re.compile(r"(^|/)conversations(-\d+)?\.json$")


def active_branch(conv: dict) -> list:
    """Сообщения текущей ветки разговора: от current_node вверх к корню."""
    mapping = conv.get("mapping") or {}
    node_id = conv.get("current_node")
    chain = []
    seen = set()
    while node_id and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        node = mapping[node_id]
        if node.get("message"):
            chain.append(node["message"])
        node_id = node.get("parent")
    return list(reversed(chain))


def message_text(msg: dict) -> str:
    content = msg.get("content") or {}
    if content.get("content_type") not in ("text", "multimodal_text"):
        return ""
    if (msg.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
        return ""
    return "\n".join(p for p in content.get("parts") or [] if isinstance(p, str)).strip()


def conversation_to_record(conv: dict, location: str) -> Optional[Record]:
    turns = []
    for msg in active_branch(conv):
        role = (msg.get("author") or {}).get("role")
        if role not in ("user", "assistant"):
            continue
        text = message_text(msg)
        if text:
            turns.append(("Пользователь" if role == "user" else "ChatGPT", clip(text)))
    user_turns = [t for who, t in turns if who == "Пользователь"]
    if not user_turns:
        return None
    return Record(
        source="chatgpt",
        external_id=str(conv.get("conversation_id") or conv.get("id")),
        title=conv.get("title") or first_line(user_turns[0]),
        body=render_dialog(turns),
        updated_at=to_iso(conv.get("update_time") or conv.get("create_time")) or "1970-01-01T00:00:00Z",
        started_at=to_iso(conv.get("create_time")),
        location=location,
    )


class ChatGPTExportConnector(FileConnector):
    name = "chatgpt_export"

    def files(self) -> Iterable[Path]:
        inbox = Path(self.cfg["inbox"]).expanduser()
        if inbox.is_dir():
            yield from sorted(p for p in inbox.iterdir() if p.suffix.lower() in (".zip", ".json"))

    def _load(self, path: Path) -> List[dict]:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        conversations = []
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if CONVERSATIONS_FILE.search(name):
                    data = json.loads(zf.read(name).decode("utf-8"))
                    if isinstance(data, list):
                        conversations.extend(data)
        return conversations

    def parse(self, path: Path) -> List[Record]:
        records = []
        for conv in self._load(path):
            if isinstance(conv, dict):
                rec = conversation_to_record(conv, f"chatgpt:{path.name}")
                if rec:
                    records.append(rec)
        return records
