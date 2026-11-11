"""Telegram: посты своих публичных каналов.

Читаем публичную страницу предпросмотра канала (t.me/s/<канал>): без входа в аккаунт, без ключей
и без внешних библиотек. Закрытые каналы и «Избранное» так не достать — для них нужна пользовательская
сессия, это отдельная задача.

Один пост с текстом — одна запись. Счётчики просмотров и реакции не берём: они меняются постоянно,
а запись должна меняться, только когда пост отредактировали.

Курсор — JSON {"канал": {"max": номер самого нового известного поста, "back": до какого номера
дочитана история (нет ключа — история дочитана)}}. Каждый прогон перечитывает свежие страницы
(новые посты и правки недавних), затем продолжает дочитывать историю, если она не закончена.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Iterator, List, Optional

from ..timeutil import to_iso
from .base import ApiConnector, Batch, Record, clip, first_line

BASE = "https://t.me"
CHANNEL = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
VOID = {"br", "img", "meta", "link", "input", "hr", "source", "wbr", "area", "base", "col", "embed", "param", "track"}
FRESH_PAGES = 2      # столько свежих страниц перечитываем каждый прогон: правки недавних постов
MAX_POST_CHARS = 20000


def channel_name(spec: str) -> str:
    """«vladilen_ai», «@vladilen_ai» или ссылка t.me/vladilen_ai → имя канала."""
    value = (spec or "").strip()
    if "t.me/" in value:
        parts = [p for p in value.split("t.me/", 1)[1].split("?")[0].split("/") if p]
        value = parts[1] if len(parts) > 1 and parts[0] == "s" else (parts[0] if parts else "")
    value = value.lstrip("@")
    if not CHANNEL.match(value):
        raise RuntimeError(f"Telegram: не понял канал «{spec}» — укажи имя канала, @имя или ссылку t.me/имя")
    return value


class PageParser(HTMLParser):
    """Посты со страницы t.me/s/<канал>: номер, время, текст, признак пересланного."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.posts: List[dict] = []
        self.cur: Optional[dict] = None
        self.stack: List[list] = []  # [тег, роль, ссылка, сколько кусков текста было до тега]

    def _roles(self):
        return {item[1] for item in self.stack}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "meta" and a.get("property") == "og:title":
            self.title = (a.get("content") or "").strip()
        if tag in VOID:
            if tag == "br" and self.cur is not None and "text" in self._roles():
                self.cur["text"].append("\n")
            return
        roles = self._roles()
        role, href = "", None
        if tag == "div" and a.get("data-post"):
            role = "post"
            self.cur = {"post": a["data-post"], "time": None, "text": [], "fwd": False}
            self.posts.append(self.cur)
        elif self.cur is not None:
            if "tgme_widget_message_reply" in cls:
                role = "reply"  # цитата поста, на который отвечают: её текст — не текст этого поста
            elif "js-message_text" in cls and not roles & {"reply", "text"}:
                role = "text"
            elif "tgme_widget_message_forwarded_from" in cls:
                # Имя источника Telegram отдаёт через раз (бывает пустым), поэтому берём только сам факт.
                self.cur["fwd"] = True
            elif tag == "time" and a.get("datetime") and "reply" not in roles:
                self.cur["time"] = a["datetime"]
            elif tag == "a" and "text" in roles:
                href = a.get("href")
        self.stack.append([tag, role, href, len(self.cur["text"]) if self.cur is not None else 0])

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        while self.stack:
            item = self.stack.pop()
            if item[0] != tag:
                continue  # незакрытый тег внутри — пропускаем
            _, role, href, start = item
            if role == "post":
                self.cur = None
            elif href and self.cur is not None and href.startswith(("http://", "https://")):
                shown = "".join(self.cur["text"][start:])
                # Ссылку дописываем, только если её адреса нет в самом тексте (иначе дубль).
                if href.rstrip("/") not in shown and shown.strip().lstrip("@#") not in href.rstrip("/").split("/")[-1:]:
                    self.cur["text"].append(f" ({href})")
            return

    def handle_data(self, data):
        if self.cur is None:
            return
        if "text" in self._roles():
            self.cur["text"].append(data)


def parse_page(html: str, channel: str) -> dict:
    parser = PageParser()
    parser.feed(html)
    parser.close()
    posts = []
    for p in parser.posts:
        name, _, number = p["post"].partition("/")
        if name.lower() != channel.lower() or not number.isdigit():
            continue
        text = re.sub(r"\n{3,}", "\n\n", "".join(p["text"]).replace("\xa0", " ")).strip()
        posts.append({"id": int(number), "time": to_iso(p["time"]), "text": text, "fwd": p["fwd"]})
    return {"title": parser.title, "posts": posts}


def build_record(channel: str, title: str, post: dict) -> Optional[Record]:
    """Пост без текста (только фото или видео) записью не становится."""
    if not post["text"] or not post["time"]:
        return None
    head = [f"Канал: {title or channel} (@{channel})"]
    if post["fwd"]:
        head.append("Пересланный пост")
    return Record(source="telegram", external_id=f"{channel}/{post['id']}",
                  title=first_line(post["text"]) or f"Пост {post['id']}",
                  body="\n".join(head) + "\n\n" + clip(post["text"], MAX_POST_CHARS),
                  updated_at=post["time"], started_at=post["time"],
                  location=f"https://t.me/{channel}/{post['id']}", kind="document")


class TelegramConnector(ApiConnector):
    name = source = "telegram"
    title = "Telegram"
    about = "посты его публичных Telegram-каналов"
    default_every_minutes = 60
    empty_hint = "Укажи свои публичные каналы в sources.telegram.channels (README, раздел «Telegram»)."

    def page(self, channel: str, before: Optional[int]) -> dict:
        base = (self.settings.get("api_base") or BASE).rstrip("/")
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"):
            raise RuntimeError("Telegram: api_base должен быть адресом HTTPS")
        url = f"{base}/s/{channel}" + (f"?before={before}" if before else "")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) brain-sync",
                                                   "Accept": "text/html"})
        pause = float(self.settings.get("request_pause", 1.0))
        if pause > 0:
            time.sleep(pause)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read(5_000_000).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            exc.close()
            raise RuntimeError(f"Telegram: HTTP {exc.code} на странице канала @{channel}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"Telegram: нет связи ({getattr(exc, 'reason', exc)})") from None
        return parse_page(html, channel)

    def fetch(self, cursor: Optional[str]) -> Iterator[Batch]:
        specs = [c for c in self.settings.get("channels") or [] if str(c).strip()]
        if not specs:
            raise RuntimeError("Telegram: не указаны каналы — добавь sources.telegram.channels в config.json")
        try:
            pos = json.loads(cursor) if cursor else {}
        except ValueError:
            pos = {}
        if not isinstance(pos, dict):
            pos = {}
        budget = int(self.settings.get("max_pages_per_run", 40))
        problems: List[str] = []
        for spec in specs:
            channel = channel_name(str(spec))
            state = pos.get(channel) if isinstance(pos.get(channel), dict) else None
            first_time = state is None
            if first_time:
                state = {}
            known = int(state.get("max") or 0)
            # 1. Свежие страницы: новые посты и правки недавних. Номер max сдвигаем, только дойдя до известного.
            before, top, pages, low = None, 0, 0, None
            while budget > 0:
                data = self.page(channel, before)
                budget -= 1
                pages += 1
                ids = [p["id"] for p in data["posts"]]
                if not ids:
                    if pages == 1:
                        problems.append(f"Telegram: у канала @{channel} нет публичной страницы с постами")
                    else:
                        # Старше постов нет: история дочитана (первый проход) или канал короче свежих страниц.
                        if first_time:
                            state.pop("back", None)
                        else:
                            state["max"] = max(known, top)
                        pos[channel] = state
                        yield Batch([], json.dumps(pos, sort_keys=True))
                    break
                top, low = max(top, max(ids)), min(ids)
                records = [r for r in (build_record(channel, data["title"], p) for p in data["posts"]) if r]
                if first_time:
                    # Первый проход — это сразу и история: запоминаем, докуда дочитали.
                    state.update(max=max(int(state.get("max") or 0), top), back=low)
                    pos[channel] = state
                elif low <= known and pages >= FRESH_PAGES or low <= 1:
                    state["max"] = max(known, top)
                    pos[channel] = state
                    yield Batch(records, json.dumps(pos, sort_keys=True))
                    break
                yield Batch(records, json.dumps(pos, sort_keys=True))
                if low <= 1:
                    break
                before = low
            if low is None:
                continue
            if first_time:
                if low <= 1:
                    state.pop("back", None)
                    yield Batch([], json.dumps(pos, sort_keys=True))
                continue
            # 2. История, если прошлые прогоны её не дочитали.
            while budget > 0 and state.get("back"):
                data = self.page(channel, int(state["back"]))
                budget -= 1
                ids = [p["id"] for p in data["posts"]]
                if not ids or min(ids) >= int(state["back"]):
                    state.pop("back", None)
                    yield Batch([], json.dumps(pos, sort_keys=True))
                    break
                state["back"] = min(ids)
                if min(ids) <= 1:
                    state.pop("back", None)
                records = [r for r in (build_record(channel, data["title"], p) for p in data["posts"]) if r]
                yield Batch(records, json.dumps(pos, sort_keys=True))
        if problems:
            raise RuntimeError("; ".join(problems))
