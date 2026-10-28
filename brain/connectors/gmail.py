"""Gmail: треды и поиск силами самого Gmail — расширения IMAP X-GM-THRID и X-GM-RAW.

Моё письмо — с меткой \\Sent, помеченное — с флагом \\Flagged. Работаем в «Вся почта»: имя папки
локализовано, поэтому ищем её по флагу \\All. Спама и корзины там нет.
"""
from __future__ import annotations

from typing import List, Optional, Set, Tuple

from .mail import MailConnector
from .mailbox import gmail_date
from .mailparse import MailMessage


class GmailConnector(MailConnector):
    name = "gmail"
    source = "gmail"
    title = "Gmail"
    about = "переписка в Gmail"
    host = "imap.gmail.com"
    gmail = True

    def candidates(self, box, pos: dict) -> Tuple[Set[str], dict]:
        folder = next((f for f in box.folders() if "\\All" in f.flags), None)
        if folder is None:
            raise RuntimeError("Gmail: не нашлась папка «Вся почта» (флаг \\All)")
        validity, uidnext = box.select(folder.name)
        after = gmail_date(self.since)
        starred = set(box.gmail_threads(box.gmail_search(f"is:starred after:{after}")).values())
        if pos.get("validity") != validity or "uid" not in pos:
            # Первый проход: Gmail сам находит мои и помеченные письма — весь ящик читать не нужно.
            keys = set(box.gmail_threads(box.gmail_search(f"(in:sent OR is:starred) after:{after}")).values())
        else:
            # Новые письма пересобирают свой тред, только если он мой; плюс новые звёздочки на старых письмах.
            touched = set(box.gmail_threads(box.uids_after(int(pos["uid"]))).values())
            keys = {t for t in touched if box.gmail_search("in:sent OR is:starred", thread=t)}
            keys |= starred - set(pos.get("starred") or [])
        return keys, {"validity": validity, "uid": uidnext - 1, "starred": sorted(starred)}

    def thread(self, box, key: str) -> List[MailMessage]:
        return [self.message(f, "\\Sent" in f.labels) for f in box.fetch(box.gmail_thread_uids(key))]

    def external_id(self, key: str) -> str:
        return f"thr:{key}"

    def location(self, key: str) -> Optional[str]:
        return f"https://mail.google.com/mail/#all/{int(key):x}"  # так Gmail адресует тред в вебе
