"""Тесты почты: разбор писем, IMAP-слой, коннекторы Gmail и Яндекса на поддельном ящике.

Запускаются вместе со всеми: python3 -m unittest discover -s tests (из корня проекта)."""
from __future__ import annotations

import imaplib
import sys
import unittest
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brain import keychain  # noqa: E402
from brain.redact import redact  # noqa: E402
from brain.connectors import mailparse  # noqa: E402
from brain.connectors import mailbox  # noqa: E402


class KeychainTests(unittest.TestCase):
    def test_reads_password(self):
        command = [sys.executable, "-c", "print('секрет')"]
        self.assertEqual(keychain.password("brain-mail", "me@x.ru", command), "секрет")

    def test_missing_password_explains_how_to_add(self):
        with self.assertRaises(RuntimeError) as ctx:
            keychain.password("brain-mail", "me@x.ru", [sys.executable, "-c", "import sys; sys.exit(44)"])
        self.assertIn("security add-generic-password -s brain-mail -a me@x.ru -w", str(ctx.exception))


class CardMaskTests(unittest.TestCase):
    def test_cards_masked_orders_and_phones_kept(self):
        text = ("Карта 4111 1111 1111 1111, вторая 5500-0000-0000-0004, третья 4111111111111111. "
                "Заказ 1234567890123, телефон +7 (999) 123-45-67.")
        out = redact(text)
        self.assertEqual(out.count("[карта скрыта]"), 3)
        for kept in ("1234567890123", "+7 (999) 123-45-67"):
            self.assertIn(kept, out)


def eml(msg_id, sender, to, subject, text=None, html=None, date="Thu, 01 Oct 2026 10:00:00 +0300",
        in_reply_to=None, references=(), attachments=()):
    """Письмо в байтах, как его отдаёт IMAP."""
    m = EmailMessage()
    m["Message-ID"] = f"<{msg_id}>"
    m["From"] = sender
    m["To"] = to
    m["Subject"] = subject
    m["Date"] = date
    if in_reply_to:
        m["In-Reply-To"] = f"<{in_reply_to}>"
    if references:
        m["References"] = " ".join(f"<{r}>" for r in references)
    if text is not None:
        m.set_content(text)
        if html is not None:
            m.add_alternative(html, subtype="html")
    elif html is not None:
        m.set_content(html, subtype="html")
    else:
        m.set_content("")
    for name in attachments:
        m.add_attachment(b"data", maintype="application", subtype="octet-stream", filename=name)
    return m.as_bytes()


class MailParseTests(unittest.TestCase):
    def test_plain_message(self):
        m = mailparse.parse(eml("a1@x.ru", "Анна Петрова <Anna@X.ru>", "me@gmail.com", "Договор",
                                text="Пришлите договор."))
        self.assertEqual((m.message_id, m.sender, m.subject), ("a1@x.ru", ("Анна Петрова", "anna@x.ru"), "Договор"))
        self.assertEqual(m.date, "2026-10-01T07:00:00Z")
        self.assertEqual(m.recipients, [("", "me@gmail.com")])
        self.assertEqual(m.text, "Пришлите договор.")

    def test_html_only(self):
        markup = ("<html><head><style>p {color: red}</style></head><body><p>Привет!</p>"
                  "<p>Счёт&nbsp;на&nbsp;оплату</p><blockquote>старое письмо</blockquote>"
                  "<script>track()</script></body></html>")
        m = mailparse.parse(eml("h1@x", "a@x.ru", "me@x.ru", "Счёт", html=markup))
        self.assertIn("Привет!", m.text)
        self.assertIn("Счёт на оплату", m.text)
        for noise in ("color", "track()", "старое письмо"):
            self.assertNotIn(noise, m.text)

    def test_quotes_and_signature_are_cut(self):
        cases = {
            "en": "Да, подходит.\n\nOn Thu, Oct 1, 2026 at 10:00 AM Anna Petrova <\nanna@x.ru> wrote:\n> Подходит?",
            "ru": "Да, подходит.\n\n1 окт. 2026 г., в 10:00, Анна <anna@x.ru> написал(а):\n> Подходит?",
            "gmail_ru": "Да, подходит.\n\nчт, 1 окт. 2026 г. в 10:00, Анна <anna@x.ru>:\n\n> Подходит?",
            "outlook": "Да, подходит.\n\nОт: Анна Петрова\nОтправлено: 1 октября 2026 г. 10:00\nТема: Договор\n\nПодходит?",
            "original": "Да, подходит.\n-----Original Message-----\nFrom: Anna\nПодходит?",
            "signature": "Да, подходит.\n-- \nВладилен\n+7 999",
            "quoted_inline": "> Подходит?\nДа, подходит.",
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.assertEqual(mailparse.strip_quotes(text), "Да, подходит.")

    def test_forward_is_kept(self):
        text = ("Смотри ниже.\n\n---------- Forwarded message ---------\nFrom: Anna <anna@x.ru>\n"
                "Date: Thu, Oct 1, 2026 at 10:00 AM\nSubject: Договор\n\nТекст договора.")
        out = mailparse.strip_quotes(text)
        self.assertIn("From: Anna <anna@x.ru>", out)
        self.assertIn("Текст договора.", out)

    def test_subject_root_and_attachments(self):
        self.assertEqual(mailparse.normalize_subject("Re: Fwd: RE[2]: Ответ: Договор"), "Договор")
        self.assertEqual(mailparse.normalize_subject("  "), "(без темы)")
        reply = mailparse.parse(eml("a3@x", "a@x.ru", "me@x.ru", "Re: Договор", text="ок", in_reply_to="a2@x",
                                    references=("a1@x", "a2@x"), attachments=("договор.pdf",)))
        self.assertEqual(mailparse.thread_root(reply), "a1@x")
        self.assertEqual(reply.attachments, ["договор.pdf"])
        lone = mailparse.parse(eml("b1@x", "a@x.ru", "me@x.ru", "Привет", text="x"))
        self.assertEqual(mailparse.thread_root(lone), "b1@x")

    def test_broken_message_survives(self):
        raw = (b"Message-ID: <bad@x>\r\nFrom: a@x.ru\r\nContent-Type: text/plain; charset=x-unknown\r\n"
               b"Date: not a date\r\n\r\n\xff\xfe\xfd")
        m = mailparse.parse(raw, fallback_date="2026-10-02T00:00:00Z")
        self.assertEqual((m.message_id, m.date), ("bad@x", "2026-10-02T00:00:00Z"))
        self.assertTrue(m.text)

    def test_render_thread(self):
        first = mailparse.parse(eml("a1@x", "Анна <anna@x.ru>", "me@gmail.com", "Договор", text="Пришлите договор."))
        mine = mailparse.parse(eml("a2@x", "me@gmail.com", "Анна <anna@x.ru>", "Re: Договор", text="Отправил.",
                                   date="Thu, 01 Oct 2026 11:30:00 +0300", attachments=("договор.pdf",)))
        mine.mine = True
        title, body = mailparse.render_thread([mine, first])
        self.assertEqual(title, "Договор")
        self.assertEqual(body, (
            "Переписка: Договор\n"
            "Участники: Анна <anna@x.ru>, я <me@gmail.com>\n"
            "Писем: 2\n\n"
            "Анна <anna@x.ru>, 2026-10-01 07:00:\nПришлите договор.\n\n"
            "я, 2026-10-01 08:30:\nОтправил.\n[вложения: договор.pdf]"))


RAW = b"Message-ID: <m1@x>\r\nSubject: hi\r\n\r\nbody"


class FakeIMAP:
    """Подставной imaplib: отдаёт заранее заготовленные ответы и запоминает команды."""

    def __init__(self, replies=None):
        self.replies = replies or {}
        self.calls = []

    def login(self, user, password):
        self.calls.append(("login", user))
        if password != "app-pass":
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        return "OK", [b"LOGIN completed"]

    def list(self):
        return "OK", self.replies.get("LIST", [])

    def status(self, name, items):
        self.calls.append(("STATUS", name, items))
        return "OK", [b'"INBOX" (UIDVALIDITY 7 UIDNEXT 42)']

    def select(self, name, readonly=False):
        self.calls.append(("SELECT", name, readonly))
        return "OK", [b"3"]

    def uid(self, command, *args):
        self.calls.append((command,) + args)
        return "OK", self.replies.get(command, [b""])

    def logout(self):
        self.calls.append(("logout",))
        return "BYE", [b""]


def open_box(replies=None, gmail=False):
    imap = FakeIMAP(replies)
    box = mailbox.Mailbox("imap.example", "me@x.ru", "app-pass", gmail=gmail, connect=lambda: imap)
    return box.open(), imap


class MailboxTests(unittest.TestCase):
    def test_folders_and_select(self):
        box, imap = open_box({"LIST": [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren \\Sent) "/" "Sent"',
            b'(\\HasNoChildren \\Junk) "/" Spam',
            b'(\\All \\HasNoChildren) "/" "[Gmail]/All Mail"',
            b'(\\HasNoChildren) "|" "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"',
        ]})
        folders = box.folders()
        self.assertEqual([f.name for f in folders],
                         ["INBOX", "Sent", "Spam", "[Gmail]/All Mail", "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"])
        self.assertEqual(folders[1].flags, ["\\HasNoChildren", "\\Sent"])
        self.assertEqual(box.select("[Gmail]/All Mail"), (7, 42))
        self.assertIn(("SELECT", '"[Gmail]/All Mail"', True), imap.calls)

    def test_fetch_parsing_gmail(self):
        meta = (b'1 (X-GM-THRID 1869712353157532782 X-GM-LABELS ("\\\\Important" "\\\\Sent" Work) UID 11 '
                b'FLAGS (\\Seen \\Flagged) INTERNALDATE "27-Oct-2026 10:00:00 +0300" BODY[]<0> {%d}' % len(RAW))
        box, imap = open_box({"FETCH": [(meta, RAW), b")", b"2 (UID 12 FLAGS (\\Seen))"]}, gmail=True)
        first, second = box.fetch([12, 11])
        self.assertEqual((first.uid, first.thread, first.raw), (11, "1869712353157532782", RAW))
        self.assertEqual(first.labels, ["\\Important", "\\Sent", "Work"])
        self.assertEqual(first.flags, ["\\Seen", "\\Flagged"])
        self.assertEqual(first.internal_date, "2026-10-27T07:00:00Z")
        self.assertEqual((second.uid, second.raw, second.flags), (12, b"", ["\\Seen"]))
        command = imap.calls[-1]
        self.assertEqual(command[:2], ("FETCH", "11,12"))
        self.assertIn("X-GM-LABELS", command[2])
        self.assertIn("BODY.PEEK[]<0.1048576>", command[2])

    def test_search_commands(self):
        box, imap = open_box({"SEARCH": [b"3 5 7"]}, gmail=True)
        self.assertEqual(box.uids_after(5), [7])
        self.assertEqual(imap.calls[-1], ("SEARCH", "UID", "6:*"))
        box.gmail_search("in:sent OR is:starred", thread="123")
        self.assertEqual(imap.calls[-1], ("SEARCH", "X-GM-THRID", "123", "X-GM-RAW", '"in:sent OR is:starred"'))
        box.uids_since(datetime(2024, 10, 7), flagged=True)
        self.assertEqual(imap.calls[-1], ("SEARCH", "FLAGGED", "SINCE", "07-Oct-2024"))
        box.uids_in_thread("a1@x.ru")
        self.assertEqual(imap.calls[-1], ("SEARCH", "OR", "OR", "HEADER", "Message-ID", '"a1@x.ru"',
                                          "HEADER", "References", '"a1@x.ru"', "HEADER", "In-Reply-To", '"a1@x.ru"'))
        self.assertEqual(mailbox.gmail_date(datetime(2024, 10, 7)), "2024/10/07")

    def test_wrong_password(self):
        imap = FakeIMAP()
        box = mailbox.Mailbox("imap.example", "me@x.ru", "не тот", connect=lambda: imap)
        with self.assertRaises(mailbox.AuthFailed):
            box.open()


if __name__ == "__main__":
    unittest.main()
