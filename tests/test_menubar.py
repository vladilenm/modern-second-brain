"""Проверки нативного меню macOS на безопасных снимках состояния."""
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("xcrun"), "нужны macOS и Swift")
class MenubarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.tmp = Path(cls.temp.name)
        source = Path(__file__).resolve().parents[1] / "menubar"
        bundle = cls.tmp / "BrainBar.app" / "Contents"
        (bundle / "MacOS").mkdir(parents=True)
        shutil.copyfile(source / "Info.plist", bundle / "Info.plist")
        cls.binary = bundle / "MacOS" / "BrainBar"
        subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-parse-as-library",
                        "-module-cache-path", str(cls.tmp / "cache"), "-o", str(cls.binary),
                        str(source / "BrainBar.swift"), str(source / "MenuViews.swift"),
                        str(source / "ModelSettings.swift")],
                       check=True, capture_output=True, timeout=120)

    def setUp(self):
        self.config_path = self.tmp / "config.json"
        self.config_path.write_text(json.dumps({"llm": {"base_url": "https://api.openai.com/v1",
                                                       "model": "test-model", "api_key": "test-secret"}}))
        self.status = {
            "state": "idle", "last_sync_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pending": 0, "sync_errors": [],
            "sources": {"kimi": {"records": 10, "summarized": 10}},
            "sources_meta": [{"source": "kimi", "connector": "kimi", "title": "Kimi Code"}],
            "recent": [{"id": 1, "source": "kimi", "title": "Тестовая карточка"}],
        }

    def dump(self, state=None):
        path = self.tmp / "status.json"
        path.write_text(json.dumps(self.status if state is None else state))
        result = subprocess.run([str(self.binary), "--dump", "--status", str(path)],
                                env={**os.environ, "BRAIN_CONFIG": str(self.config_path), "BRAIN_LLM_API_KEY": ""},
                                check=True, capture_output=True, text=True, timeout=15)
        self.assertIn("значок:", result.stdout, "AppKit не запустился: тесту нужен доступ к сессии macOS")
        return result.stdout

    def test_idle_prioritizes_actions_and_groups_details(self):
        menu = self.dump()
        self.assertIn("Всё синхронизировано", menu)
        self.assertIn("предупреждение: false", menu)
        self.assertIn("10 записей · 1 источник", menu)
        self.assertLess(menu.index("Открыть базу"), menu.index("Источники"))
        self.assertIn("Настройки  ▸\n    OpenAI · test-model\n    Модель и провайдер…", menu)
        self.assertIn("    Запускать при входе", menu)
        self.assertIn("    Открыть журнал", menu)
        self.assertNotIn("Синхронизировать сейчас [недоступно]", menu)
        self.assertNotIn("test-secret", menu)

    def test_active_model_is_distinct_from_next_run_choice(self):
        self.status.update(state="summarize", pid=os.getpid(),
                           processor={"provider": "SimpleClaw", "model": "old-model"})
        menu = self.dump()
        self.assertIn("модель: SimpleClaw · old-model", menu)
        self.assertIn("Следующий запуск: OpenAI · test-model", menu)
        self.status["state"] = "idle"
        self.assertIn("модель: OpenAI · test-model", self.dump())

    def test_source_error_is_visible_even_when_run_finished(self):
        self.status["sync_errors"] = [{"source": "kimi", "error": "Не прочитан журнал"}]
        menu = self.dump()
        self.assertIn("Есть ошибки синхронизации", menu)
        self.assertIn("предупреждение: true", menu)
        self.assertIn("Kimi Code. Подробности — в журнале.", menu)
        self.assertIn("\nОткрыть журнал ошибок\n", menu)
        self.assertNotIn("Всё синхронизировано", menu)

    def test_running_sync_disables_duplicate_run(self):
        self.status.update(state="sync", pid=os.getpid())
        menu = self.dump()
        self.assertIn("Обновляю базу", menu)
        self.assertIn("Синхронизация идёт… [недоступно]", menu)

    def test_progress_and_interrupted_process(self):
        self.status.update(state="summarize", pid=os.getpid(), progress={"done": 3, "total": 10, "current": "Карточка"})
        menu = self.dump()
        self.assertIn("Создаю карточки · 3 из 10", menu)
        self.assertIn("прогресс: 30%", menu)
        self.status["pid"] = 2147483647
        menu = self.dump()
        self.assertIn("Синхронизация прервалась", menu)
        self.assertIn("предупреждение: true", menu)
        self.assertIn("\nСинхронизировать сейчас\n", menu)

    def test_empty_pending_and_stale_states(self):
        self.assertIn("Готов к первой синхронизации", self.dump({}))
        self.status["pending"] = 5
        menu = self.dump()
        self.assertIn("В очереди 5 карточек", menu)
        self.assertNotIn("Всё синхронизировано", menu)
        self.status["last_sync_at"] = "2020-01-01T00:00:00Z"
        menu = self.dump()
        self.assertIn("Давно не обновлялось", menu)
        self.assertIn("предупреждение: true", menu)


if __name__ == "__main__":
    unittest.main()
