"""Локальные разговоры Kimi Code / CLI, включая встроенный CLI в Kimi Desktop.

Новый формат — sessions/<workspace>/<id>/agents/main/wire.jsonl;
старый Python CLI — sessions/<md5(cwd)>/<id>/{wire,context}.jsonl.
Wire сохраняет разговор до сжатия контекста; context — запасной вариант.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable, List

from ..timeutil import to_iso
from .base import FileConnector, Record, clip, first_line, home_relative, iter_jsonl, render_dialog

DROP_BLOCKS = re.compile(
    r"<(system|system-reminder|plugin-instructions|active-viewer-context|skill-loaded)\b[^>]*>.*?</\1>",
    re.S | re.I,
)
META = re.compile(r"<meta\b[^>]*\b(?:awareness|timestamp)=[^>]*?/>", re.I)


def text_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p["text"] for p in content if isinstance(p, dict)
                         and p.get("type") == "text" and isinstance(p.get("text"), str))
    return ""


def clean_user(content) -> str:
    text = META.sub("", DROP_BLOCKS.sub("", text_content(content)))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def timestamp(value):
    # TypeScript CLI пишет миллисекунды; Python CLI — секунды.
    if isinstance(value, (int, float)) and value > 100_000_000_000:
        value /= 1000
    try:
        return to_iso(value)
    except (ValueError, OverflowError, OSError):
        return None


def read_object(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: ожидался JSON-объект")
    return value


def modern_turns(path: Path):
    seen = set()
    for item in iter_jsonl(path):
        if item.get("agentId", "main") != "main":
            continue
        kind = item.get("type")
        if kind in ("turn.prompt", "turn.steer"):
            # agent.message.appended дублирует ввод и иногда помечает навык как user.
            # Источник правды для ввода — turn.*, где есть настоящий origin.
            if (item.get("origin") or {}).get("kind") == "user":
                yield "Пользователь", clean_user(item.get("input")), timestamp(item.get("time"))
        elif kind in ("agent.message.appended", "human.agent.message.appended"):
            envelope = item.get("message") or {}
            message = envelope.get("message", envelope)
            if message.get("role") != "assistant":
                continue
            mid = (envelope.get("meta") or {}).get("messageId")
            if mid and mid in seen:
                continue
            if mid:
                seen.add(mid)
            yield "Kimi", text_content(message.get("content")).strip(), timestamp(item.get("time"))


def legacy_turns(path: Path):
    if path.name == "context.jsonl":
        for item in iter_jsonl(path):
            if item.get("role") == "user":
                yield "Пользователь", clean_user(item.get("content")), None
            elif item.get("role") == "assistant":
                yield "Kimi", text_content(item.get("content")).strip(), None
        return
    chunks, updated, compacting = [], None, False
    for item in iter_jsonl(path):
        message = item.get("message") or {}
        kind, payload = message.get("type"), message.get("payload") or {}
        ts = timestamp(item.get("timestamp"))
        if kind == "ContentPart" and payload.get("type") == "text" and not compacting:
            chunks.append(text_content([payload]))
            updated = ts or updated
            continue
        if kind in ("TurnBegin", "SteerInput", "TurnEnd", "StepBegin", "StepInterrupted", "CompactionBegin"):
            if chunks:
                yield "Kimi", "".join(chunks).strip(), updated
                chunks = []
        if kind in ("TurnBegin", "SteerInput"):
            yield "Пользователь", clean_user(payload.get("user_input")), ts
        elif kind == "CompactionBegin":
            compacting = True
        elif kind == "CompactionEnd":
            compacting = False
    if chunks:
        yield "Kimi", "".join(chunks).strip(), updated


class KimiConnector(FileConnector):
    name = "kimi"
    source = "kimi"
    title = "Kimi Code"
    about = "разговоры в Kimi Code / Kimi CLI"
    empty_hint = "Создай разговор в Kimi Code; для другой папки истории укажи sources.kimi.roots в config.json."

    def files(self) -> Iterable[Path]:
        seen = set()
        for root in self.roots():
            for session in sorted(Path(root).glob("*/*")):
                if not session.is_dir():
                    continue
                # Ровно один журнал на сессию, без agents/agent-* и subagents/.
                for relative in ("agents/main/wire.jsonl", "wire.jsonl", "context.jsonl"):
                    path = session / relative
                    if path.is_file():
                        key = path.resolve()
                        if key not in seen:
                            seen.add(key)
                            yield path
                        break

    def project(self, session: Path, state: dict):
        if state.get("cwd"):
            return state["cwd"]
        home = session.parents[1].parent
        index = home / "session_index.jsonl"
        if index.is_file():
            for entry in iter_jsonl(index):
                if (entry.get("sessionId") == session.name
                        and Path(entry.get("sessionDir", "")).parent.name == session.parent.name):
                    return entry.get("workDir")
        for entry in read_object(home / "kimi.json").get("work_dirs", []):
            project = entry.get("path")
            if not project:
                continue
            bucket = hashlib.md5(project.encode("utf-8")).hexdigest()
            kaos = entry.get("kaos", "local")
            if session.parent.name == (bucket if kaos == "local" else f"{kaos}_{bucket}"):
                return project
        return None

    def parse(self, path: Path) -> List[Record]:
        modern = path.parent.name == "main" and path.parent.parent.name == "agents"
        session = path.parents[2] if modern else path.parent
        state = read_object(session / "state.json")
        session_kind = (state.get("custom") or {}).get("sessionKind")
        if session.name.startswith("ctitle-") or session_kind not in (None, "conversation"):
            return []
        turns, dates = [], []
        for who, text, ts in (modern_turns(path) if modern else legacy_turns(path)):
            if text:
                turns.append((who, clip(text)))
                if ts:
                    dates.append(ts)
        questions = [text for who, text in turns if who == "Пользователь"]
        if not questions:
            return []
        return [Record(
            source=self.source,
            external_id=f"{session.parent.name}/{session.name}",
            title=state.get("title") or first_line(questions[0]),
            body=render_dialog(turns),
            started_at=min(dates) if dates else timestamp(state.get("createdAt")),
            updated_at=max(dates) if dates else timestamp(state.get("updatedAt")) or to_iso(path.stat().st_mtime),
            project=home_relative(self.project(session, state)),
            location=home_relative(str(path)),
        )]
