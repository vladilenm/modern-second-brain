"""Тесты почты: разбор писем, IMAP-слой, коннекторы Gmail и Яндекса на поддельном ящике.

Запускаются вместе со всеми: python3 -m unittest discover -s tests (из корня проекта)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brain import keychain  # noqa: E402
from brain.redact import redact  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
