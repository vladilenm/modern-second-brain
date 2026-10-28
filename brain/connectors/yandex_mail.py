"""Яндекс.Почта: треды по заголовкам Message-ID / In-Reply-To / References.

Моё письмо — то, что лежит в папке отправленных (флаг \\Sent), помеченное — с флагом \\Flagged.
Спам, корзину и черновики не читаем. Корень треда — первый из References, иначе In-Reply-To,
иначе само письмо; почтовик, который пишет только In-Reply-To, рвёт длинную переписку на части.
"""
from __future__ import annotations

from typing import Dict, List, Set, Tuple

from .mail import MailConnector
from .mailparse import MailMessage, thread_root

SKIP_FLAGS = {"\\Junk", "\\Spam", "\\Trash", "\\Drafts", "\\Noselect"}


class YandexMailConnector(MailConnector):
    name = "yandex_mail"
    source = "yandex_mail"
    title = "Яндекс.Почта"
    about = "переписка в Яндекс.Почте"
    host = "imap.yandex.ru"
    auth_hint = " и включён ли IMAP в настройках почты («Почтовые программы»)"

    def candidates(self, box, pos: dict) -> Tuple[Set[str], dict]:
        self.folders = [f for f in box.folders() if not SKIP_FLAGS & set(f.flags)]
        self.sent = {f.name for f in self.folders if "\\Sent" in f.flags}
        known = pos.get("folders") or {}
        known_flagged = set(pos.get("flagged") or [])
        keys: Set[str] = set()
        flagged_now: Set[str] = set()
        after: Dict[str, dict] = {}
        for folder in self.folders:
            validity, uidnext = box.select(folder.name)
            old = known.get(folder.name) or {}
            fresh = old.get("validity") != validity or "uid" not in old
            sent = folder.name in self.sent
            if fresh:
                # Первый проход папки: мои треды начинаются с моих писем, чужие ответы найдутся при сборке.
                new = set(box.uids_since(self.since)) if sent else set()
            else:
                new = set(box.uids_after(int(old["uid"])))
            flagged = set(box.uids_since(self.since, flagged=True))
            for uid, head in box.headers(new | flagged).items():
                root = thread_root(head)
                if not root:
                    continue
                if uid in flagged:
                    flagged_now.add(root)
                    if root not in known_flagged:
                        keys.add(root)
                # Моё новое письмо или чей-то ответ — тред проверим целиком. Письмо без In-Reply-To и
                # References — начало чужого треда, моей переписки там ещё нет: новые рассылки ничего не стоят.
                if uid in new and (sent or head.in_reply_to or head.references):
                    keys.add(root)
            after[folder.name] = {"validity": validity, "uid": uidnext - 1}
        return keys, {"folders": after, "flagged": sorted(flagged_now)}

    def thread(self, box, root: str) -> List[MailMessage]:
        found: Dict[str, List[int]] = {}
        for folder in self.folders:
            box.select(folder.name)
            uids = box.uids_in_thread(root)
            if uids:
                found[folder.name] = uids
        if not found:
            return []
        if not self.sent & set(found):
            # Моего письма нет — нужна пометка. Сначала только флаги: тело чужой переписки качать незачем.
            flagged = False
            for name, uids in found.items():
                box.select(name)
                if any("\\Flagged" in f.flags for f in box.fetch(uids, body=False)):
                    flagged = True
                    break
            if not flagged:
                return []
        messages: List[MailMessage] = []
        by_id: Dict[str, MailMessage] = {}
        for name in sorted(found):
            box.select(name)
            for fetched in box.fetch(found[name]):
                m = self.message(fetched, name in self.sent)
                if m.message_id and m.message_id in by_id:  # копия письма в другой папке
                    by_id[m.message_id].mine |= m.mine
                    by_id[m.message_id].flagged |= m.flagged
                    continue
                if m.message_id:
                    by_id[m.message_id] = m
                messages.append(m)
        return messages

    def external_id(self, key: str) -> str:
        return f"root:{key}"
