"""Почта: общий алгоритм для IMAP-ящиков.

Что считать «моей перепиской» и как собрать тред, решают стратегии (gmail.py, yandex_mail.py);
здесь — пароль, вход, пачки, курсор и запись треда. Курсор нового прохода пишется только с последней
пачкой, промежуточные несут прежний: оборвался прогон — следующий повторит проход, уже записанные
треды отсечёт хеш, модель повторно не вызовется.
"""
from __future__ import annotations

import imaplib
import json
from datetime import datetime, timedelta, timezone
from typing import Iterator, List, Optional, Set, Tuple

from .. import keychain
from .base import ApiConnector, Batch, Record
from .mailbox import AuthFailed, Fetched, Mailbox
from .mailparse import MailMessage, parse, render_thread

THREAD_BATCH = 20
KEYCHAIN_SERVICE = "brain-mail"


class MailConnector(ApiConnector):
    default_every_minutes = 15
    empty_hint = ("Пока пусто. Включи ящик в config.json и положи пароль приложения в связку ключей "
                  "(README, раздел «Почта»).")
    host = ""
    gmail = False   # нужны ли расширения Gmail в FETCH
    auth_hint = ""  # что ещё проверить, если сервер не пускает

    def open_mailbox(self, address: str, secret: str):
        """Вход в ящик; тесты подменяют этот метод поддельным ящиком."""
        return Mailbox(self.settings.get("host") or self.host, address, secret, gmail=self.gmail).open()

    @property
    def since(self) -> datetime:
        return datetime.now(timezone.utc) - timedelta(days=self.backfill_days)

    def fetch(self, cursor: Optional[str]) -> Iterator[Batch]:
        address = self.settings.get("address") or ""
        if not address:
            raise RuntimeError(f"{self.title}: в config.json не задан address")
        try:
            secret = keychain.password(self.settings.get("keychain_service") or KEYCHAIN_SERVICE, address,
                                       self.settings.get("password_command"))
            box = self.open_mailbox(address, secret)
        except AuthFailed:
            raise RuntimeError(f"{self.title}: почта не пускает — проверь пароль приложения{self.auth_hint}") from None
        except RuntimeError as exc:
            raise RuntimeError(f"{self.title}: {exc}") from None
        except OSError as exc:
            raise RuntimeError(f"{self.title}: нет связи ({exc})") from None
        except imaplib.IMAP4.error as exc:  # сервер оборвал разговор ещё до входа
            raise RuntimeError(f"{self.title}: ошибка IMAP ({exc})") from None
        try:
            yield from self._pass(box, cursor)
        except OSError as exc:  # нет сети, таймаут, обрыв соединения
            raise RuntimeError(f"{self.title}: нет связи ({exc})") from None
        except imaplib.IMAP4.error as exc:
            raise RuntimeError(f"{self.title}: ошибка IMAP ({exc})") from None
        finally:
            box.close()
            self.close()

    def _pass(self, box, cursor: Optional[str]) -> Iterator[Batch]:
        previous = cursor or "{}"  # «{}» читается как «курсора нет»
        try:
            pos = json.loads(previous)
        except ValueError:
            pos = {}
        keys, after = self.candidates(box, pos if isinstance(pos, dict) else {})
        batch: List[Record] = []
        written: List[str] = []
        for key in sorted(keys):
            messages = self.thread(box, key)
            if not any(m.mine or m.flagged for m in messages):
                self.done([key])
                continue  # не моя переписка: ни моего письма, ни пометки
            batch.append(self.record(key, messages))
            written.append(key)
            if len(batch) >= THREAD_BATCH:
                yield Batch(batch, previous)
                self.done(written)  # сюда возвращаемся, когда sync уже записал пачку
                batch, written = [], []
        yield Batch(batch, json.dumps(after, sort_keys=True))
        self.done(written)

    def record(self, key: str, messages: List[MailMessage]) -> Record:
        title, body = render_thread(messages)
        dates = sorted(m.date for m in messages if m.date)
        return Record(
            source=self.source,
            external_id=self.external_id(key),
            title=title,
            body=body,
            updated_at=dates[-1] if dates else "1970-01-01T00:00:00Z",
            started_at=dates[0] if dates else None,
            location=self.location(key),
            kind="email",
        )

    @staticmethod
    def message(fetched: Fetched, mine: bool) -> MailMessage:
        m = parse(fetched.raw, fetched.internal_date)
        m.mine = mine
        m.flagged = "\\Flagged" in fetched.flags
        return m

    # Стратегия ящика.

    def candidates(self, box, pos: dict) -> Tuple[Set[str], dict]:
        """Треды-кандидаты этого прохода и курсор, который запишется после него."""
        raise NotImplementedError

    def thread(self, box, key: str) -> List[MailMessage]:
        raise NotImplementedError

    def external_id(self, key: str) -> str:
        raise NotImplementedError

    def location(self, key: str) -> Optional[str]:
        return None

    def done(self, keys: List[str]) -> None:
        """Треды записаны (или оказались чужими) — стратегия может забыть, что их надо пересобрать."""

    def close(self) -> None:
        """Конец прогона: стратегия освобождает своё (например, соединение с индексом)."""
