"""Разбор писем без сети: байты письма → MailMessage, HTML → текст, без цитат и подписи.

Чистые функции: их проверяют тесты на готовых письмах, а IMAP живёт отдельно в mailbox.py.
"""
from __future__ import annotations

import email
import email.policy
import re
from dataclasses import dataclass, field
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from typing import List, Tuple

from ..timeutil import to_iso
from .base import clip

MESSAGE_ID = re.compile(r"<([^<>\s]+)>")
SUBJECT_PREFIX = re.compile(r"^\s*(?:(?:re|fwd?|aw|wg|ответ|отв|пересл)\s*(?:\[\d+\]|\(\d+\))?\s*:\s*)+", re.I)
# Шапка цитаты: ниже неё — прошлые письма, они в треде и так есть.
QUOTE_HEADER = re.compile(
    r"^\s*(?:"
    r"On\s.{0,300}\swrote:"                                                       # по-английски
    r"|.{0,300}(?:\d{1,2}:\d{2}|@).{0,300}(?:написал(?:\(а\)|а)?|пишет):"        # по-русски
    r"|.{0,200}\d{1,2}:\d{2}.{0,200}<[^<>\s@]+@[^<>\s]+>:"                        # «…, 10:00, Анна <a@x.ru>:»
    r"|-{2,}\s*(?:Original Message|Исходное сообщение)\s*-{2,}"
    r")\s*$", re.I)
OUTLOOK_FROM = re.compile(r"^\s*(?:From|От):\s", re.I)
OUTLOOK_SENT = re.compile(r"^\s*(?:Sent|Отправлено|Date|Дата):\s", re.I)
# Пересылаемое письмо — это содержание, после такой строки ничего не режем.
FORWARDED = re.compile(r"^\s*-{5,}\s*(?:Forwarded message|Пересылаемое сообщение)\s*-{5,}\s*$", re.I)
BLOCK_TAGS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "hr"}
SKIP_TAGS = {"script", "style", "head", "title", "blockquote"}  # blockquote — цитата прошлого письма


@dataclass
class MailMessage:
    message_id: str                    # без угловых скобок; у письма без заголовка — пусто
    in_reply_to: str
    references: List[str]
    date: str                          # UTC ISO
    sender: Tuple[str, str]            # (имя, адрес в нижнем регистре)
    recipients: List[Tuple[str, str]]
    subject: str
    text: str
    attachments: List[str] = field(default_factory=list)
    mine: bool = False                 # моё письмо — решает стратегия ящика
    flagged: bool = False


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _Text()
    parser.feed(markup)
    parser.close()
    lines = [" ".join(line.replace("\xa0", " ").split()) for line in "".join(parser.parts).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def strip_quotes(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    out: List[str] = []
    for i, line in enumerate(lines):
        if FORWARDED.match(line):
            out.extend(lines[i:])
            break
        if line.rstrip() == "--":
            break  # подпись
        if QUOTE_HEADER.match(line):
            break
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        # Длинную шапку «On … wrote:» почтовики переносят на вторую строку.
        if line.lstrip().startswith("On ") and QUOTE_HEADER.match(line + " " + nxt):
            break
        if OUTLOOK_FROM.match(line) and OUTLOOK_SENT.match(nxt):
            break
        if line.lstrip().startswith(">"):
            continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def normalize_subject(subject: str) -> str:
    return SUBJECT_PREFIX.sub("", subject or "").strip() or "(без темы)"


def thread_root(message: MailMessage) -> str:
    """Корень треда: первый из References, иначе In-Reply-To, иначе само письмо."""
    if message.references:
        return message.references[0]
    return message.in_reply_to or message.message_id


def _header(msg, name: str) -> str:
    try:
        return str(msg.get(name) or "").strip()
    except Exception:  # кривой заголовок — не повод терять письмо
        return ""


def _addresses(value: str) -> List[Tuple[str, str]]:
    return [(n.strip(), a.strip().lower()) for n, a in getaddresses([value]) if a.strip()]


def _date(value: str, fallback: str) -> str:
    try:
        return to_iso(parsedate_to_datetime(value).timestamp()) or fallback
    except (TypeError, ValueError, IndexError, OverflowError):
        return fallback


def _content(part) -> str:
    try:
        return part.get_content()
    except (LookupError, UnicodeError, KeyError, AssertionError):  # неизвестная кодировка
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")


def _body(msg) -> Tuple[str, List[str]]:
    names = [str(part.get_filename()) for part in msg.walk() if part.get_filename()]
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return "", names
    text = _content(part)
    if part.get_content_subtype() == "html":
        text = html_to_text(text)
    return strip_quotes(text), names


def parse(raw: bytes, fallback_date: str = "") -> MailMessage:
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception as exc:
        return MailMessage("", "", [], fallback_date, ("", ""), [], "", f"[не удалось разобрать письмо: {exc}]")
    ids = MESSAGE_ID.findall(_header(msg, "Message-ID"))
    reply = MESSAGE_ID.findall(_header(msg, "In-Reply-To"))
    senders = _addresses(_header(msg, "From"))
    try:
        text, names = _body(msg)
    except Exception as exc:  # битое письмо не должно стопорить источник
        text, names = f"[не удалось разобрать письмо: {exc}]", []
    return MailMessage(
        message_id=ids[0] if ids else "",
        in_reply_to=reply[0] if reply else "",
        references=MESSAGE_ID.findall(_header(msg, "References")),
        date=_date(_header(msg, "Date"), fallback_date),
        sender=senders[0] if senders else ("", ""),
        recipients=_addresses(", ".join(_header(msg, h) for h in ("To", "Cc"))),
        subject=_header(msg, "Subject"),
        text=text,
        attachments=names,
    )


def _person(name: str, address: str) -> str:
    if name and address:
        return f"{name} <{address}>"
    return address or name or "(без отправителя)"


def render_thread(messages: List[MailMessage]) -> Tuple[str, str]:
    """Название и текст записи: шапка с участниками, затем письма по времени."""
    messages = sorted(messages, key=lambda m: m.date)
    title = normalize_subject(messages[0].subject)
    me = {m.sender[1] for m in messages if m.mine and m.sender[1]}
    people: List[str] = []
    seen = set()
    for m in messages:
        for name, address in [m.sender] + m.recipients:
            if address and address not in seen:
                seen.add(address)
                people.append(f"я <{address}>" if address in me else _person(name, address))
    blocks = []
    for m in messages:
        head = "я" if m.mine else _person(*m.sender)
        when = m.date[:16].replace("T", " ") if m.date else "—"
        block = f"{head}, {when}:\n{clip(m.text) if m.text else '(пустое письмо)'}"
        if m.attachments:
            block += "\n[вложения: " + ", ".join(m.attachments) + "]"
        blocks.append(block)
    head = "\n".join([f"Переписка: {title}", "Участники: " + ", ".join(people), f"Писем: {len(messages)}"])
    return title, head + "\n\n" + "\n\n".join(blocks)
