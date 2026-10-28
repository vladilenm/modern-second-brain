"""Тесты почты: разбор писем, IMAP-слой, коннекторы Gmail и Яндекса на поддельном ящике.

Запускаются вместе со всеми: python3 -m unittest discover -s tests (из корня проекта)."""
from __future__ import annotations

import imaplib
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brain import keychain  # noqa: E402
from brain.redact import redact  # noqa: E402
from brain.connectors import mailparse  # noqa: E402
from brain.connectors import mailbox  # noqa: E402
from brain import db  # noqa: E402
from brain import sync as sync_mod  # noqa: E402
from brain.connectors import mail  # noqa: E402
from brain.connectors.gmail import GmailConnector  # noqa: E402
from brain.connectors.mailbox import AuthFailed, Fetched, Folder  # noqa: E402
from brain.timeutil import to_iso  # noqa: E402
from test_brain import make_cfg  # noqa: E402


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


class FakeMail:
    """Письмо в поддельном ящике."""

    def __init__(self, folder, uid, raw, flags=(), labels=(), thread="", date="2026-10-01T07:00:00Z"):
        self.folder, self.uid, self.raw = folder, uid, raw
        self.flags, self.labels, self.thread, self.date = list(flags), list(labels), thread, date


class FakeMailbox:
    """Поддельный ящик с интерфейсом Mailbox: письма в памяти, поиск — по смыслу, а не по синтаксису IMAP."""

    def __init__(self, mails, folders, validity=1):
        self.mails, self.folder_list, self.validity = mails, folders, validity
        self.selected = None
        self.selects, self.searches, self.calls = [], [], []
        self.closed = False

    def folders(self):
        return self.folder_list

    def select(self, name):
        self.selected = name
        self.selects.append(name)
        uids = [m.uid for m in self.mails if m.folder == name]
        return self.validity, (max(uids) + 1 if uids else 1)

    def _here(self):
        return [m for m in self.mails if m.folder == self.selected]

    def uids_after(self, uid):
        return sorted(m.uid for m in self._here() if m.uid > uid)

    def uids_since(self, day, flagged=False):
        floor = to_iso(day.timestamp())
        return sorted(m.uid for m in self._here() if m.date >= floor and (not flagged or "\\Flagged" in m.flags))

    def uids_in_thread(self, root):
        out = []
        for m in self._here():
            p = mailparse.parse(m.raw)
            if root in (p.message_id, p.in_reply_to) or root in p.references:
                out.append(m.uid)
        return sorted(out)

    def gmail_search(self, query, thread=""):
        self.searches.append(query)
        after = re.search(r"after:(\d{4})/(\d\d)/(\d\d)", query)
        floor = "-".join(after.groups()) if after else ""
        return sorted(m.uid for m in self._here()
                      if (not thread or m.thread == thread) and m.date[:10] >= floor
                      and (("in:sent" in query and "\\Sent" in m.labels)
                           or ("is:starred" in query and "\\Flagged" in m.flags)))

    def gmail_thread_uids(self, thread):
        return sorted(m.uid for m in self._here() if m.thread == thread)

    def gmail_threads(self, uids):
        wanted = set(uids)
        return {m.uid: m.thread for m in self._here() if m.uid in wanted}

    def headers(self, uids):
        wanted = set(uids)
        return {m.uid: mailparse.parse(m.raw) for m in self._here() if m.uid in wanted}

    def fetch(self, uids, body=True):
        wanted = set(uids)
        self.calls.append(("fetch", self.selected, tuple(sorted(wanted)), body))
        return [Fetched(uid=m.uid, raw=m.raw if body else b"", flags=list(m.flags), labels=list(m.labels),
                        thread=m.thread, internal_date=m.date) for m in self._here() if m.uid in wanted]

    def close(self):
        self.closed = True


ALL_MAIL = "[Gmail]/All Mail"


def gmail_mails():
    """«Вся почта»: моя переписка (тред 100), рассылка (200), помеченная рассылка (300), старая переписка (400)."""
    return [
        FakeMail(ALL_MAIL, 1, eml("a1@x.ru", "Анна <anna@x.ru>", "me@gmail.com", "Договор",
                                  text="Пришлите договор, пожалуйста."),
                 labels=["\\Inbox"], thread="100", date="2026-10-01T07:00:00Z"),
        FakeMail(ALL_MAIL, 2, eml("a2@gmail.com", "me@gmail.com", "Анна <anna@x.ru>", "Re: Договор",
                                  text="Отправил.\n\nчт, 1 окт. 2026 г. в 10:00, Анна <anna@x.ru>:\n"
                                       "> Пришлите договор, пожалуйста.",
                                  date="Thu, 01 Oct 2026 11:00:00 +0300", in_reply_to="a1@x.ru",
                                  references=("a1@x.ru",)),
                 labels=["\\Sent"], thread="100", date="2026-10-01T08:00:00Z"),
        FakeMail(ALL_MAIL, 3, eml("n1@substack.com", "news@substack.com", "me@gmail.com", "Дайджест",
                                  text="Новости недели."),
                 labels=["\\Inbox"], thread="200", date="2026-10-05T07:00:00Z"),
        FakeMail(ALL_MAIL, 4, eml("s1@substack.com", "dan@substack.com", "me@gmail.com", "Эссе",
                                  text="Длинное эссе."),
                 flags=["\\Flagged"], labels=["\\Inbox"], thread="300", date="2026-10-06T07:00:00Z"),
        FakeMail(ALL_MAIL, 5, eml("o1@x.ru", "me@gmail.com", "old@x.ru", "Давно", text="Старое письмо.",
                                  date="Sun, 01 Jan 2023 10:00:00 +0300"),
                 labels=["\\Sent"], thread="400", date="2023-01-01T07:00:00Z"),
    ]


class MailCase(unittest.TestCase):
    """Общая обвязка: конфиг с включённым ящиком, подставной пароль и поддельный ящик вместо IMAP."""
    connector = None
    source = ""
    address = ""

    def setUp(self):
        self.cfg = make_cfg(Path(tempfile.mkdtemp()))
        self.cfg["sources"][self.source].update(enabled=True, address=self.address,
                                                password_command=[sys.executable, "-c", "print('app-pass')"])
        self.conn = db.connect(self.cfg["db_path"])
        self.box = self.make_box()
        self.opened = []
        patcher = mock.patch.object(self.connector, "open_mailbox", lambda conn, address, secret: self._open(address, secret))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _open(self, address, secret):
        self.opened.append((address, secret))
        return self.box

    def sync(self):
        return sync_mod.sync(self.cfg, self.conn, only=self.source, log=lambda *_: None)[self.source]

    def records(self):
        return {r["external_id"]: r for r in self.conn.execute("SELECT * FROM records WHERE source = ?", (self.source,))}

    def last_error(self):
        return self.conn.execute("SELECT last_error FROM sync_state WHERE source = ?", (self.source,)).fetchone()[0]


class GmailTests(MailCase):
    connector = GmailConnector
    source = "gmail"
    address = "me@gmail.com"

    def make_box(self):
        return FakeMailbox(gmail_mails(), [Folder("INBOX", ["\\HasNoChildren"]), Folder(ALL_MAIL, ["\\All"])])

    def test_first_run_takes_my_and_starred_threads(self):
        totals = self.sync()
        self.assertEqual((totals["new"], totals["errors"]), (2, 0))
        records = self.records()
        self.assertEqual(sorted(records), ["thr:100", "thr:300"])
        mine = records["thr:100"]
        self.assertEqual((mine["kind"], mine["title"]), ("email", "Договор"))
        self.assertEqual(mine["location"], "https://mail.google.com/mail/#all/64")
        self.assertEqual(mine["body"].count("Пришлите договор"), 1)
        self.assertIn("я, 2026-10-01 08:00:\nОтправил.", mine["body"])
        self.assertEqual((mine["started_at"], mine["updated_at"]), ("2026-10-01T07:00:00Z", "2026-10-01T08:00:00Z"))
        self.assertEqual(json.loads(db.get_cursor(self.conn, "gmail")), {"starred": ["300"], "uid": 5, "validity": 1})
        self.assertEqual(self.opened, [("me@gmail.com", "app-pass")])
        self.assertTrue(self.box.closed)

    def test_reply_rebuilds_my_thread_and_new_newsletter_is_ignored(self):
        self.sync()
        self.box.mails += [
            FakeMail(ALL_MAIL, 6, eml("a3@x.ru", "Анна <anna@x.ru>", "me@gmail.com", "Re: Договор",
                                      text="Получила, спасибо.", date="Fri, 02 Oct 2026 10:00:00 +0300",
                                      in_reply_to="a2@gmail.com", references=("a1@x.ru", "a2@gmail.com")),
                     labels=["\\Inbox"], thread="100", date="2026-10-02T07:00:00Z"),
            FakeMail(ALL_MAIL, 7, eml("n2@substack.com", "news@substack.com", "me@gmail.com", "Дайджест 2",
                                      text="Ещё новости."),
                     labels=["\\Inbox"], thread="500", date="2026-10-07T07:00:00Z"),
        ]
        totals = self.sync()
        self.assertEqual((totals["new"], totals["changed"]), (0, 1))
        self.assertIn("Получила, спасибо.", self.records()["thr:100"]["body"])
        self.assertNotIn("thr:500", self.records())

    def test_new_star_on_old_letter_adds_its_thread(self):
        self.sync()
        self.box.mails[2].flags.append("\\Flagged")
        self.assertEqual(self.sync()["new"], 1)
        self.assertIn("thr:200", self.records())

    def test_interrupted_pass_is_repeated(self):
        original = GmailConnector.thread

        def flaky(connector, box, key):
            if key == "300":
                raise imaplib.IMAP4.abort("соединение оборвалось")
            return original(connector, box, key)

        with mock.patch.object(GmailConnector, "thread", flaky), mock.patch.object(mail, "THREAD_BATCH", 1):
            totals = self.sync()
        self.assertEqual((totals["new"], totals["errors"]), (1, 1))
        self.assertEqual(db.get_cursor(self.conn, "gmail"), "{}")
        self.assertIn("Gmail: ошибка IMAP", self.last_error())
        totals = self.sync()
        self.assertEqual((totals["new"], totals["changed"], totals["errors"]), (1, 0, 0))
        self.assertIn("uid", json.loads(db.get_cursor(self.conn, "gmail")))

    def test_uidvalidity_change_repeats_first_pass(self):
        self.sync()
        self.box.validity = 2
        self.box.searches.clear()
        totals = self.sync()
        self.assertEqual((totals["new"], totals["changed"], totals["errors"]), (0, 0, 0))
        self.assertTrue(any(q.startswith("(in:sent OR is:starred)") for q in self.box.searches))
        self.assertEqual(json.loads(db.get_cursor(self.conn, "gmail"))["validity"], 2)

    def test_errors_are_explained(self):
        self.cfg["sources"]["gmail"]["password_command"] = [sys.executable, "-c", "import sys; sys.exit(44)"]
        self.sync()
        self.assertIn("Gmail: нет пароля в связке ключей", self.last_error())
        self.assertIn("security add-generic-password -s brain-mail -a me@gmail.com -w", self.last_error())

        def refuse(connector, address, secret):
            raise AuthFailed("[AUTHENTICATIONFAILED]")

        self.cfg["sources"]["gmail"]["password_command"] = [sys.executable, "-c", "print('app-pass')"]
        with mock.patch.object(GmailConnector, "open_mailbox", refuse):
            self.sync()
        self.assertEqual(self.last_error(), "Gmail: почта не пускает — проверь пароль приложения")
        self.cfg["sources"]["gmail"]["address"] = ""
        self.sync()
        self.assertEqual(self.last_error(), "Gmail: в config.json не задан address")


if __name__ == "__main__":
    unittest.main()
