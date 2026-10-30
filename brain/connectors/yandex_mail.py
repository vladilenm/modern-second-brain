"""Яндекс.Почта: треды собираем сами, по локальному индексу заголовков.

IMAP Яндекса медленно отдаёт заголовки (около 0,15 с на письмо) и не справляется с поиском по ним
(UID SEARCH HEADER отвечает «Backend error»), поэтому заголовок каждого письма забираем один раз и храним
в таблице mail_index: папка, UID, Message-ID и корень треда. Дальше читаем заголовки только новых писем,
а тред собираем по индексу. Корень — первый из References, иначе In-Reply-To, иначе само письмо;
почтовик, который пишет только In-Reply-To, рвёт длинную переписку на части.

Моё письмо — то, что лежит в папке отправленных (флаг \\Sent), помеченное — с флагом \\Flagged.
Спам, корзину, черновики и шаблоны не читаем.
"""
from __future__ import annotations

import imaplib
import time
from typing import Dict, List, Optional, Set, Tuple

from .. import db
from .mail import MailConnector
from .mailparse import MailMessage, thread_root

SKIP_FLAGS = {"\\Junk", "\\Spam", "\\Trash", "\\Drafts", "\\Templates", "\\Noselect"}
HEADER_CHUNK = 50      # заголовков за команду: на 200 письмах Яндекс думал полминуты и рвал соединение
RECONNECT_EVERY = 300  # долгую сессию Яндекс замедляет вдвое-втрое; свежее соединение снова быстрое
INDEX_BUDGET = 300     # секунд на заголовки за прогон: первый проход ящика растягивается на несколько прогонов


class YandexMailConnector(MailConnector):
    name = "yandex_mail"
    source = "yandex_mail"
    title = "Яндекс.Почта"
    about = "переписка в Яндекс.Почте"
    host = "imap.yandex.ru"
    auth_hint = " и включён ли IMAP в настройках почты («Почтовые программы»)"
    index = None

    def candidates(self, box, pos: dict) -> Tuple[Set[str], dict]:
        self.index = db.connect(self.cfg["db_path"])
        # «Отправленные» первыми: по ним видно, какие треды мои, — они появятся уже после первого прогона.
        self.folders = sorted((f for f in box.folders() if not SKIP_FLAGS & set(f.flags)),
                              key=lambda f: ("\\Sent" not in f.flags, f.name != "INBOX", f.name))
        self.sent = {f.name for f in self.folders if "\\Sent" in f.flags}
        self.deadline = time.monotonic() + float(self.settings.get("index_budget_seconds", INDEX_BUDGET))
        self.fetched = 0
        for folder in self.folders:
            validity, _ = self._select(box, folder.name)
            self._index_folder(box, folder.name, validity)
        if self.fetched:
            box.reconnect()  # к концу долгой индексации Яндекс рвёт сессию — остаток прогона на свежем соединении
        flagged: Set[str] = set()
        for folder in self.folders:
            self._select(box, folder.name)
            for uid in box.uids_since(self.since, flagged=True):
                root = self._root_of(folder.name, uid)
                if root:
                    flagged.add(root)
        # Пересобрать: треды писем, впервые попавших в индекс, и треды новых пометок.
        keys = {r["root"] for r in self.index.execute(
            "SELECT DISTINCT root FROM mail_index WHERE source = ? AND pending = 1 AND root != ''", (self.source,))}
        keys |= flagged - set(pos.get("flagged") or [])
        mine = {k for k in keys if k in flagged or self._has_sent(k)}
        # Чужие треды — моего письма нет, пометки нет: тела не качаем и больше к ним не возвращаемся.
        self.done(sorted(keys - mine))
        self.index.execute("UPDATE mail_index SET pending = 0 WHERE source = ? AND root = ''", (self.source,))
        self.index.commit()
        return mine, {"flagged": sorted(flagged)}

    def _index_folder(self, box, folder: str, validity: int) -> None:
        q = self.index.execute
        if q("SELECT 1 FROM mail_index WHERE source = ? AND folder = ? AND validity != ? LIMIT 1",
             (self.source, folder, validity)).fetchone():
            q("DELETE FROM mail_index WHERE source = ? AND folder = ?", (self.source, folder))  # папку пересоздали
        top = q("SELECT MAX(uid) FROM mail_index WHERE source = ? AND folder = ?", (self.source, folder)).fetchone()[0]
        new = box.uids_since(self.since) if top is None else box.uids_after(top)
        for i in range(0, len(new), HEADER_CHUNK):
            if time.monotonic() >= self.deadline:
                return  # время прогона вышло — остальное дочитаем в следующем
            part = new[i:i + HEADER_CHUNK]
            heads = self._headers(box, folder, part)
            rows = [(self.source, folder, validity, uid, head.message_id, thread_root(head))
                    for uid, head in heads.items()]
            # Письма, которые сервер не отдал (у Яндекса — с флагом encrypted), — пустышками,
            # чтобы не спотыкаться о них каждый прогон.
            rows += [(self.source, folder, validity, uid, "", "") for uid in part if uid not in heads]
            self.index.executemany(
                "INSERT OR REPLACE INTO mail_index (source, folder, validity, uid, message_id, root, pending) "
                "VALUES (?, ?, ?, ?, ?, ?, 1)", rows)
            self.index.commit()  # прогресс сохраняется: оборвался прогон — продолжим со следующей пачки

    @staticmethod
    def _select(box, folder: str) -> Tuple[int, int]:
        try:
            return box.select(folder)
        except (imaplib.IMAP4.abort, OSError):  # оборвал соединение — одна попытка на свежем
            box.reconnect()
            return box.select(folder)

    def _headers(self, box, folder: str, uids: List[int]) -> Dict[int, MailMessage]:
        if self.fetched >= RECONNECT_EVERY:
            box.reconnect()
            box.select(folder)
            self.fetched = 0
        try:
            heads = box.headers(uids)
        except (imaplib.IMAP4.abort, OSError):  # оборвал соединение — одна попытка на свежем
            box.reconnect()
            box.select(folder)
            heads = box.headers(uids)
        self.fetched += len(uids)
        return heads

    def _root_of(self, folder: str, uid: int) -> Optional[str]:
        row = self.index.execute("SELECT root FROM mail_index WHERE source = ? AND folder = ? AND uid = ?",
                                 (self.source, folder, uid)).fetchone()
        return row["root"] if row else None

    def _has_sent(self, root: str) -> bool:
        folders = sorted(self.sent)
        if not folders:
            return False
        marks = ", ".join("?" * len(folders))
        return self.index.execute(
            f"SELECT 1 FROM mail_index WHERE source = ? AND root = ? AND folder IN ({marks}) LIMIT 1",
            [self.source, root] + folders).fetchone() is not None

    def thread(self, box, root: str) -> List[MailMessage]:
        by_folder: Dict[str, List[int]] = {}
        for r in self.index.execute("SELECT folder, uid FROM mail_index WHERE source = ? AND root = ? ORDER BY uid",
                                    (self.source, root)):
            by_folder.setdefault(r["folder"], []).append(r["uid"])
        messages: List[MailMessage] = []
        by_id: Dict[str, MailMessage] = {}
        for name in sorted(by_folder):
            if self.fetched >= RECONNECT_EVERY:
                box.reconnect()
                self.fetched = 0
            self._select(box, name)
            self.fetched += len(by_folder[name])
            for fetched in box.fetch(by_folder[name]):
                m = self.message(fetched, name in self.sent)
                if m.message_id and m.message_id in by_id:  # копия письма в другой папке
                    by_id[m.message_id].mine |= m.mine
                    by_id[m.message_id].flagged |= m.flagged
                    continue
                if m.message_id:
                    by_id[m.message_id] = m
                messages.append(m)
        return messages

    def done(self, keys: List[str]) -> None:
        if self.index is None or not keys:
            return
        self.index.executemany("UPDATE mail_index SET pending = 0 WHERE source = ? AND root = ?",
                               [(self.source, k) for k in keys])
        self.index.commit()

    def close(self) -> None:
        if self.index is not None:
            self.index.close()
            self.index = None

    def external_id(self, key: str) -> str:
        return f"root:{key}"
