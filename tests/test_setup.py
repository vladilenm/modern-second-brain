"""Мастер настройки и всё, на чём он стоит: секреты, запись конфига, ввод, автозапуск, команды CLI."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brain import config, secret_store  # noqa: E402


def fake_command(tmp: Path, body: str) -> Path:
    """Исполняемый sh-скрипт вместо системной программы."""
    path = tmp / "fake"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


class SecretStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_put_sends_secret_through_stdin_not_arguments(self):
        fake = fake_command(self.tmp, f'echo "$@" > "{self.tmp}/args"\ncat > "{self.tmp}/stdin"\n')
        secret_store.put("brain-x", "me@x.ru", 'p"a\\ss', command=[str(fake), "-i"])
        self.assertEqual((self.tmp / "args").read_text().strip(), "-i")
        self.assertEqual((self.tmp / "stdin").read_text(),
                         'add-generic-password -U -s "brain-x" -a "me@x.ru" -w "p\\"a\\\\ss"\n')

    def test_put_rejects_empty_and_non_ascii(self):
        for bad in ("", "   ", "пароль", "a\nb"):
            with self.assertRaises(ValueError):
                secret_store.put("brain-x", "me", bad, command=["/usr/bin/false"])

    def test_put_reports_keychain_refusal(self):
        fake = fake_command(self.tmp, 'cat >/dev/null\necho "User interaction is not allowed." >&2\nexit 1\n')
        with self.assertRaises(RuntimeError) as ctx:
            secret_store.put("brain-x", "me", "token", command=[str(fake)])
        self.assertIn("not allowed", str(ctx.exception))

    def test_get_and_exists(self):
        self.assertEqual(secret_store.get("brain-x", "me", [sys.executable, "-c", "print('t-1')"]), "t-1")
        self.assertTrue(secret_store.exists("brain-x", "me", [sys.executable, "-c", "pass"]))
        self.assertFalse(secret_store.exists("brain-x", "me", [sys.executable, "-c", "raise SystemExit(44)"]))

    def test_missing_secret_explains_how_to_add(self):
        with self.assertRaises(RuntimeError) as ctx:
            secret_store.get("brain-mail", "me@x.ru", [sys.executable, "-c", "raise SystemExit(44)"])
        self.assertIn("security add-generic-password -s brain-mail -a me@x.ru -w", str(ctx.exception))


class ConfigWriteTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "config.json"
        env = mock.patch.dict(os.environ, {"BRAIN_CONFIG": str(self.path)})
        env.start()
        self.addCleanup(env.stop)

    def test_save_sources_keeps_everything_else(self):
        self.path.write_text(json.dumps({"llm": {"model": "m"},
                                         "sources": {"gmail": {"address": "a@b.c", "every_minutes": 5}}}))
        config.save_sources({"gmail": {"enabled": True}, "telegram": {"channels": ["x"], "enabled": True}})
        stored = json.loads(self.path.read_text())
        self.assertEqual(stored["llm"], {"model": "m"})
        self.assertEqual(stored["sources"]["gmail"], {"address": "a@b.c", "every_minutes": 5, "enabled": True})
        self.assertEqual(stored["sources"]["telegram"], {"channels": ["x"], "enabled": True})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["config.json"])  # временных файлов не осталось

    def test_missing_file_is_created_and_read_raw_is_empty_before(self):
        self.assertEqual(config.read_raw(), {})
        config.save_sources({"kimi": {"enabled": False}})
        self.assertEqual(config.read_raw(), {"sources": {"kimi": {"enabled": False}}})

    def test_read_raw_rejects_non_object(self):
        self.path.write_text("[]")
        with self.assertRaises(ValueError):
            config.read_raw()


if __name__ == "__main__":
    unittest.main()
