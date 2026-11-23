"""Мастер настройки и всё, на чём он стоит: секреты, запись конфига, ввод, автозапуск, команды CLI."""
from __future__ import annotations

import curses
import io
import json
import os
import plistlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brain import autostart, cli, config, connectors, secret_store, ui, wizard  # noqa: E402
from brain.connectors.base import Connector, Field  # noqa: E402
from brain.connectors.chatgpt_export import ChatGPTExportConnector  # noqa: E402
from brain.connectors.claude_code import ClaudeCodeConnector  # noqa: E402
from test_brain import make_cfg, write_jsonl  # noqa: E402


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


class SetupContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = make_cfg(self.tmp)

    def test_file_source_counts_conversations(self):
        conn = ClaudeCodeConnector(self.cfg)
        self.assertIsNone(conn.detect())
        with self.assertRaises(RuntimeError) as ctx:
            conn.check()
        self.assertIn("ничего не найдено", str(ctx.exception))
        for name in ("a", "b"):
            write_jsonl(self.tmp / "claude" / "proj" / f"{name}.jsonl", [{"type": "user"}])
        self.assertEqual(conn.detect(), "2 разговора")
        self.assertEqual(conn.check(), "2 разговора")

    def test_chatgpt_counts_archives_in_inbox(self):
        conn = ChatGPTExportConnector(self.cfg)
        self.assertIsNone(conn.detect())
        (self.tmp / "inbox").mkdir()
        (self.tmp / "inbox" / "export.zip").write_bytes(b"")
        self.assertEqual(conn.detect(), "1 архив")
        self.assertIn("Export data", conn.setup_help)

    def test_secret_ref_defaults_and_account_from_field(self):
        class Fake(Connector):
            name = "fake"
            secret_service = "brain-fake"
        token = Field("token", "Токен", kind="secret")
        mail = Field("password", "Пароль", kind="secret", account_from="address")
        self.cfg["sources"]["fake"] = {"address": "me@x.ru"}
        self.assertEqual(Fake(self.cfg).secret_ref(token), ("brain-fake", "default"))
        self.assertEqual(Fake(self.cfg).secret_ref(mail), ("brain-fake", "me@x.ru"))
        self.cfg["sources"]["fake"].update(keychain_service="other", keychain_account="work")
        self.assertEqual(Fake(self.cfg).secret_ref(token), ("other", "work"))

    def test_plain_connector_needs_no_setup(self):
        self.assertIsNone(Connector(self.cfg).detect())
        self.assertEqual(Connector(self.cfg).check(), "настройка не требуется")


def text_ui(*lines, secrets=()):
    answers = iter(secrets)
    out = io.StringIO()
    return ui.TextUi(io.StringIO("".join(line + "\n" for line in lines)), out, secret=lambda _: next(answers)), out


class TextUiTests(unittest.TestCase):
    def test_ask_keeps_default_on_enter(self):
        term, _ = text_ui("", "новое")
        self.assertEqual(term.ask("Адрес", default="a@b.c"), "a@b.c")
        self.assertEqual(term.ask("Адрес", default="a@b.c"), "новое")

    def test_confirm_understands_russian_and_english(self):
        term, out = text_ui("", "н", "y", "может", "да")
        self.assertTrue(term.confirm("Включить?", True))
        self.assertFalse(term.confirm("Включить?", True))
        self.assertTrue(term.confirm("Включить?", False))
        self.assertTrue(term.confirm("Включить?", False))
        self.assertIn("Ответь «д» или «н»", out.getvalue())

    def test_choose_returns_index(self):
        term, _ = text_ui("7", "2", "")
        self.assertEqual(term.choose("Что?", ["а", "б", "в"]), 1)
        self.assertEqual(term.choose("Что?", ["а", "б", "в"], default=2), 2)

    def test_checklist_toggles_numbers_until_enter(self):
        term, out = text_ui("2 3", "3", "")
        chosen = term.checklist("Источники", [("A", "найдено"), ("B", ""), ("C", "")], {0})
        self.assertEqual(chosen, [0, 1])
        self.assertIn("[x]  1. A", out.getvalue())

    def test_secret_goes_through_secret_reader(self):
        term, out = text_ui(secrets=["  tok-1 "])
        self.assertEqual(term.ask_secret("Токен"), "tok-1")
        self.assertNotIn("tok-1", out.getvalue())

    def test_end_of_input_stops(self):
        term, _ = text_ui()
        with self.assertRaises(EOFError):
            term.ask("Адрес")


class ChecklistTests(unittest.TestCase):
    def test_keys_move_toggle_and_finish(self):
        state = ui.Checklist([("A", ""), ("B", "x")], {0})
        self.assertFalse(state.press(curses.KEY_DOWN))
        self.assertFalse(state.press(ord(" ")))
        self.assertEqual(state.checked, {0, 1})
        self.assertFalse(state.press(curses.KEY_DOWN))  # по кругу наверх
        self.assertEqual(state.pos, 0)
        self.assertTrue(state.press(10))
        self.assertEqual(state.lines()[0], "> [x] A")


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.calls = self.tmp / "calls"
        fake = fake_command(self.tmp, f'echo "$@" >> "{self.calls}"\n[ "$1" = print ] && exit "${{FAKE_PRINT_RC:-1}}"\nexit 0\n')
        for name, value in (("LAUNCHCTL", str(fake)), ("AGENTS", self.tmp / "agents"), ("LOG", self.tmp / "logs" / "sync.log")):
            patcher = mock.patch.object(autostart, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def log(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_enable_writes_plist_with_this_installation_paths(self):
        autostart.enable(Path("/opt/brain"))
        data = plistlib.loads((self.tmp / "agents" / "local.brain.sync.plist").read_bytes())
        self.assertEqual(data["Label"], "local.brain.sync")
        self.assertEqual(data["ProgramArguments"], ["/opt/brain/bin/brain", "sync"])
        self.assertEqual(data["StandardOutPath"], str(self.tmp / "logs" / "sync.log"))
        self.assertEqual([d["Minute"] for d in data["StartCalendarInterval"]], [0, 15, 30, 45])
        uid = os.getuid()
        self.assertIn(f"bootstrap gui/{uid} {self.tmp}/agents/local.brain.sync.plist", self.log())

    def test_enable_removes_legacy_agent(self):
        legacy = self.tmp / "agents" / "com.vladilen.brain-sync.plist"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("старый")
        autostart.enable(Path("/opt/brain"))
        self.assertFalse(legacy.exists())
        self.assertIn(f"bootout gui/{os.getuid()}/com.vladilen.brain-sync", self.log())

    def test_disable_and_status(self):
        autostart.enable(Path("/opt/brain"))
        autostart.disable()
        self.assertFalse((self.tmp / "agents" / "local.brain.sync.plist").exists())
        self.assertEqual(autostart.status(), "автозапуск выключен")
        with mock.patch.dict(os.environ, {"FAKE_PRINT_RC": "0"}):
            self.assertTrue(autostart.enabled())
            self.assertIn("каждые 15 минут", autostart.status())

    def test_enable_reports_launchd_refusal(self):
        refuse = fake_command(self.tmp, '[ "$1" = bootstrap ] && { echo "Bootstrap failed: 5" >&2; exit 5; }\nexit 0\n')
        with mock.patch.object(autostart, "LAUNCHCTL", str(refuse)):
            with self.assertRaises(RuntimeError) as ctx:
                autostart.enable(Path("/opt/brain"))
        self.assertIn("launchctl bootstrap", str(ctx.exception))


class CliCommandTests(unittest.TestCase):
    def test_update_needs_git_checkout(self):
        out = io.StringIO()
        with mock.patch.object(config, "ROOT", Path(tempfile.mkdtemp())), redirect_stdout(out):
            self.assertEqual(cli.main(["update"]), 1)
        self.assertIn("не через git", out.getvalue())

    def test_autostart_status_command(self):
        out = io.StringIO()
        with mock.patch.object(autostart, "enabled", return_value=False), redirect_stdout(out):
            self.assertEqual(cli.main(["autostart", "status"]), 0)
        self.assertIn("выключен", out.getvalue())


class FakeFiles(Connector):
    name = source = "fake_files"
    title = "Файлы"

    def detect(self):
        return "3 разговора"

    def check(self):
        return "3 разговора"


class FakeToken(Connector):
    name = source = "fake_token"
    title = "Сервис"
    setup_help = "Возьми токен на example.com"
    secret_service = "brain-fake"
    setup_fields = (Field("channels", "Каналы", kind="list", required=True), Field("token", "Токен", kind="secret"))
    results: list = []  # что вернёт check по очереди: строка — успех, исключение — ошибка

    def check(self):
        result = FakeToken.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeActions:
    def __init__(self):
        self.calls = []

    def enable_autostart(self):
        self.calls.append("autostart")
        return "включён"

    def disable_autostart(self):
        self.calls.append("autostart off")
        return "выключен"

    def autostart_enabled(self):
        return False

    def build_menubar(self):
        self.calls.append("menubar")

    def first_sync(self):
        self.calls.append("sync")


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "config.json"
        self.secrets = {}
        for patcher in (
            mock.patch.dict(os.environ, {"BRAIN_CONFIG": str(self.path), "BRAIN_LLM_API_KEY": "",
                                         "BRAIN_DB": str(self.dir / "brain.db")}),
            mock.patch.dict(connectors.ALL, {"fake_files": FakeFiles, "fake_token": FakeToken}, clear=True),
            mock.patch.object(wizard.secret_store, "exists", lambda s, a: (s, a) in self.secrets),
            mock.patch.object(wizard.secret_store, "put",
                              lambda s, a, v: self.secrets.__setitem__((s, a), secret_store.check_secret(v))),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        FakeToken.results = []
        self.actions = FakeActions()

    def stored(self):
        return json.loads(self.path.read_text())["sources"]

    def test_first_run_full_pass(self):
        FakeToken.results = [RuntimeError("нет доступа"), "ок"]
        term, out = text_ui(
            "2", "",          # список: включить «Сервис», дальше
            "a, b",           # каналы
            "1",              # проверка упала → попробовать снова
            "",               # каналы — Enter оставляет «a, b»
            "н",              # токен уже сохранён — не заменять
            "",               # модель: пропустить (по умолчанию)
            "", "", "н",      # автозапуск — да, значок — нет, забрать сейчас — нет
            secrets=["tok-123"])
        self.assertEqual(wizard.run(term, actions=self.actions), 0)
        self.assertEqual(self.stored(), {"fake_files": {"enabled": True},
                                         "fake_token": {"channels": ["a", "b"], "enabled": True}})
        self.assertEqual(self.secrets, {("brain-fake", "default"): "tok-123"})
        self.assertEqual(self.actions.calls, ["autostart"])
        text = out.getvalue()
        self.assertIn("Возьми токен на example.com", text)
        self.assertIn("✗ нет доступа", text)
        self.assertIn("✓ ок", text)
        self.assertIn("claude mcp add -s user brain --", text)
        self.assertNotIn("tok-123", text)

    def test_unchecked_and_skipped_sources_are_disabled(self):
        self.path.write_text(json.dumps({"sources": {"fake_files": {"enabled": True}, "fake_token": {"enabled": True}}}))
        FakeToken.results = [RuntimeError("нет доступа")]
        term, _ = text_ui("1", "", "x", "2", "", "н", "", "н", secrets=["tok"])
        wizard.run(term, actions=self.actions)
        self.assertEqual(self.stored()["fake_files"], {"enabled": False})
        self.assertEqual(self.stored()["fake_token"], {"enabled": False})

    def test_force_enable_after_failed_check(self):
        FakeToken.results = [RuntimeError("нет сети")]
        term, _ = text_ui("x", "3", secrets=["tok"])
        self.assertEqual(wizard.run(term, only="fake_token", actions=self.actions), 0)
        self.assertEqual(self.stored()["fake_token"], {"channels": ["x"], "enabled": True})

    def test_required_field_is_asked_again(self):
        FakeToken.results = ["ок"]
        term, out = text_ui("", "y", secrets=["tok"])
        wizard.run(term, only="fake_token", actions=self.actions)
        self.assertIn("Без этого источник не заработает", out.getvalue())
        self.assertEqual(self.stored()["fake_token"]["channels"], ["y"])

    def test_bad_secret_is_asked_again(self):
        FakeToken.results = ["ок"]
        term, out = text_ui("x", secrets=["пароль", "tok"])
        wizard.run(term, only="fake_token", actions=self.actions)
        self.assertIn("не подошло", out.getvalue())
        self.assertEqual(self.secrets, {("brain-fake", "default"): "tok"})

    def test_unknown_source(self):
        term, out = text_ui()
        self.assertEqual(wizard.run(term, only="nope", actions=self.actions), 1)
        self.assertIn("fake_files", out.getvalue())

    def test_split_list(self):
        self.assertEqual(wizard.split_list(" a, b ,,c\nd "), ["a", "b", "c", "d"])


if __name__ == "__main__":
    unittest.main()
