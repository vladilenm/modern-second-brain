"""Тонкий слой над imaplib: вход, папки, поиск и выборка писем с разбором ответов сервера.

Логики «что брать» здесь нет — её держат стратегии Gmail и Яндекса (gmail.py, yandex_mail.py).
В тестах вместо Mailbox подставляется поддельный ящик с теми же методами.
"""
from __future__ import annotations

import imaplib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from ..timeutil import to_iso
from .mailparse import MailMessage, parse

TIMEOUT = 60
FETCH_LIMIT = 1048576  # первый мегабайт письма: текст идёт раньше вложений, режутся в основном они
CHUNK = 50             # UID в одной команде FETCH: на 200 письмах Яндекс думал полминуты и рвал соединение
MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
LIST_LINE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$')
STATUS_VALUE = re.compile(rb"(UIDVALIDITY|UIDNEXT)\s+(\d+)")
UID = re.compile(rb"\bUID\s+(\d+)")
FLAGS = re.compile(rb"\bFLAGS\s+\(([^)]*)\)")
LABELS = re.compile(rb'\bX-GM-LABELS\s+\(((?:[^()"]|"(?:[^"\\]|\\.)*")*)\)')
THRID = re.compile(rb"\bX-GM-THRID\s+(\d+)")
INTERNALDATE = re.compile(rb'INTERNALDATE\s+"[^"]+"')
TOKEN = re.compile(rb'"((?:[^"\\]|\\.)*)"|([^\s()]+)')
NEW_ITEM = re.compile(rb"^\d+ \(")


class AuthFailed(RuntimeError):
    """Сервер не принял адрес и пароль."""


@dataclass
class Folder:
    name: str           # имя, как его понимает сервер (modified UTF-7) — для SELECT
    flags: List[str]    # \Sent, \All, \Junk…


@dataclass
class Fetched:
    uid: int
    raw: bytes = b""
    flags: List[str] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)   # X-GM-LABELS — только Gmail
    thread: str = ""                                  # X-GM-THRID — только Gmail
    internal_date: str = ""                           # UTC ISO


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def imap_date(day: datetime) -> str:
    """Дата для SEARCH SINCE: «07-Oct-2024» (английские месяцы, от локали не зависит)."""
    return f"{day.day:02d}-{MONTHS_EN[day.month - 1]}-{day.year}"


def gmail_date(day: datetime) -> str:
    return f"{day.year:04d}/{day.month:02d}/{day.day:02d}"


def _unescape(raw: bytes) -> str:
    return re.sub(rb"\\(.)", rb"\1", raw).decode("utf-8", errors="replace")


def _tokens(raw: bytes) -> List[str]:
    return [_unescape(quoted) if quoted else atom.decode("utf-8", errors="replace")
            for quoted, atom in TOKEN.findall(raw)]


def _name(raw: bytes) -> str:
    raw = raw.strip()
    if raw.startswith(b'"') and raw.endswith(b'"'):
        return _unescape(raw[1:-1])
    return raw.decode("utf-8", errors="replace")


def parse_list(lines) -> List[Folder]:
    folders = []
    for line in lines or []:
        if not isinstance(line, bytes):
            continue  # имя литералом — у наших почтовиков не встречается
        match = LIST_LINE.match(line)
        if match:
            folders.append(Folder(_name(match.group("name")), match.group("flags").decode().split()))
    return folders


def parse_status(data) -> Tuple[int, int]:
    values = dict(STATUS_VALUE.findall(b" ".join(d for d in data if isinstance(d, bytes))))
    return int(values.get(b"UIDVALIDITY", 0)), int(values.get(b"UIDNEXT", 1))


def _internal(raw: bytes) -> str:
    parsed = imaplib.Internaldate2tuple(raw)
    return to_iso(time.mktime(parsed)) if parsed else ""


def parse_fetch(data) -> List[Fetched]:
    """Ответ FETCH из imaplib: кортеж (шапка, литерал письма) и хвост шапки отдельной строкой,
    либо одна строка, если тело не просили."""
    items: List[Tuple[bytes, bytes]] = []
    for part in data or []:
        if isinstance(part, tuple):
            items.append((part[0], part[1]))
        elif isinstance(part, bytes):
            if NEW_ITEM.match(part) or not items:
                items.append((part, b""))
            else:
                meta, raw = items[-1]
                items[-1] = (meta + part, raw)
    out = []
    for meta, raw in items:
        uid = UID.search(meta)
        if not uid:
            continue
        flags, labels = FLAGS.search(meta), LABELS.search(meta)
        thread, idate = THRID.search(meta), INTERNALDATE.search(meta)
        out.append(Fetched(
            uid=int(uid.group(1)),
            raw=raw or b"",
            flags=_tokens(flags.group(1)) if flags else [],
            labels=_tokens(labels.group(1)) if labels else [],
            thread=thread.group(1).decode() if thread else "",
            internal_date=_internal(idate.group(0)) if idate else "",
        ))
    return out


class Mailbox:
    def __init__(self, host: str, address: str, password: str, gmail: bool = False,
                 connect: Optional[Callable[[], imaplib.IMAP4]] = None):
        self.address = address
        self.password = password
        self.gmail = gmail
        self._connect = connect or (lambda: imaplib.IMAP4_SSL(host, 993, timeout=TIMEOUT))
        self.imap = None

    def open(self) -> "Mailbox":
        self.imap = self._connect()
        try:
            self.imap.login(self.address, self.password)
        except imaplib.IMAP4.error as exc:
            raise AuthFailed(str(exc)) from None
        return self

    def close(self) -> None:
        try:
            self.imap.logout()
        except Exception:
            pass  # закрываем как можем: результат прогона от этого не зависит

    def reconnect(self) -> "Mailbox":
        """Новое соединение вместо оборванного (папку после него надо выбрать заново)."""
        self.close()
        return self.open()

    @staticmethod
    def _ok(typ, data, what: str):
        if typ != "OK":
            raise imaplib.IMAP4.error(f"{what}: {data!r}")
        return data

    def folders(self) -> List[Folder]:
        return parse_list(self._ok(*self.imap.list(), "LIST"))

    def select(self, name: str) -> Tuple[int, int]:
        """Открывает папку только на чтение (флаги «прочитано» не трогаем) и возвращает UIDVALIDITY и UIDNEXT."""
        validity, uidnext = parse_status(self._ok(*self.imap.status(quote(name), "(UIDVALIDITY UIDNEXT)"), "STATUS"))
        self._ok(*self.imap.select(quote(name), readonly=True), "SELECT")
        return validity, uidnext

    def _search(self, *criteria: str) -> List[int]:
        data = self._ok(*self.imap.uid("SEARCH", *criteria), "SEARCH")
        return sorted(int(x) for x in b" ".join(d for d in data if isinstance(d, bytes)).split())

    def uids_after(self, uid: int) -> List[int]:
        # «UID n:*» при n больше последнего UID отдаёт последнее письмо — его отсекаем.
        return [u for u in self._search("UID", f"{uid + 1}:*") if u > uid]

    def uids_since(self, day: datetime, flagged: bool = False) -> List[int]:
        criteria = ("FLAGGED", "SINCE", imap_date(day)) if flagged else ("SINCE", imap_date(day))
        return self._search(*criteria)

    def gmail_search(self, query: str, thread: str = "") -> List[int]:
        criteria = ("X-GM-THRID", thread) if thread else ()
        return self._search(*criteria, "X-GM-RAW", quote(query))

    def gmail_thread_uids(self, thread: str) -> List[int]:
        return self._search("X-GM-THRID", thread)

    def _fetch(self, uids: Iterable[int], items: str) -> List[Fetched]:
        uids = sorted(set(uids))
        out: List[Fetched] = []
        for i in range(0, len(uids), CHUNK):
            part = uids[i:i + CHUNK]
            typ, data = self.imap.uid("FETCH", ",".join(str(u) for u in part), items)
            if typ == "OK":
                out.extend(parse_fetch(data))
                continue
            # Яндекс отвечает NO на всю пачку, если хоть одно письмо в ней недоступно (зашифровано):
            # берём поштучно, недоступные пропускаем — без них тред всё равно читается.
            for uid in part:
                typ, data = self.imap.uid("FETCH", str(uid), items)
                if typ == "OK":
                    out.extend(parse_fetch(data))
        return out

    def gmail_threads(self, uids: Iterable[int]) -> Dict[int, str]:
        return {f.uid: f.thread for f in self._fetch(uids, "(UID X-GM-THRID)") if f.thread}

    def headers(self, uids: Iterable[int]) -> Dict[int, MailMessage]:
        items = "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES)])"
        return {f.uid: parse(f.raw) for f in self._fetch(uids, items)}

    def fetch(self, uids: Iterable[int], body: bool = True) -> List[Fetched]:
        items = ["UID", "FLAGS", "INTERNALDATE"]
        if self.gmail:
            items += ["X-GM-LABELS", "X-GM-THRID"]
        if body:
            items.append(f"BODY.PEEK[]<0.{FETCH_LIMIT}>")
        return self._fetch(uids, "(" + " ".join(items) + ")")
