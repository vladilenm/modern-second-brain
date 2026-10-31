"""Kimi: реальные формы событий, без личных данных и обращения к домашней истории."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from test_brain import make_cfg, write_jsonl
from brain import db, search, sync
from brain.connectors.kimi import KimiConnector


TS = 1793274572000


def prompt(text, ts=TS, origin="user", kind="turn.prompt"):
    return {"type": kind, "agentId": "main", "origin": {"kind": origin},
            "input": [{"type": "text", "text": text}], "time": ts}


def answer(text, ts=TS + 1000, kind="agent.message.appended"):
    return {"type": kind, "time": ts, "kind": "event", "message": {
        "message": {"role": "assistant", "content": [
            {"type": "think", "think": "скрытые рассуждения"}, {"type": "text", "text": text}],
            "toolCalls": [{"name": "Bash", "arguments": "секретная команда"}]},
        "meta": {"source": "llm", "messageId": "answer-" + str(ts)}}}


class KimiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.cfg = make_cfg(self.tmp)
        self.root = self.tmp / "kimi" / "sessions"
        self.cfg["sources"]["kimi"]["roots"] = [str(self.root)]
        self.connector = KimiConnector(self.cfg)

    def session(self, items, sid="s1", state=None):
        path = write_jsonl(self.root / "workspace" / sid / "agents" / "main" / "wire.jsonl", items)
        (path.parents[2] / "state.json").write_text(json.dumps(
            state if state is not None else {"cwd": "/tmp/project", "title": "Поиск в базе"}))
        return path

    def test_dialog_filters_mirrors_tools_thinking_and_injections(self):
        mirror = {"type": "agent.message.appended", "time": TS, "message": {
            "message": {"role": "user", "content": "Дубликат вопроса"}, "meta": {"source": "input"}}}
        path = self.session([
            {"type": "metadata", "protocol_version": "1.5", "created_at": TS},
            prompt('<system>служебный промпт</system><meta timestamp="now" />\nКак настроить поиск?'
                   '<active-viewer-context>служебная вкладка</active-viewer-context>'),
            mirror,
            {"type": "context.append_message", "message": {"role": "user", "content": "инъекция"}},
            prompt("служебный навык", origin="skill_activation", kind="turn.steer"),
            answer("Возьмём SQLite FTS5."),
            {"type": "agent.message.appended", "message": {
                "message": {"role": "tool", "content": "вывод инструмента"}}},
            prompt("И фильтр по проектам", TS + 2000, kind="turn.steer"),
            answer("Добавил.", TS + 3000, kind="human.agent.message.appended"),
            {"type": "context.apply_compaction", "summary": "служебный пересказ"},
        ])
        [rec] = self.connector.parse(path)
        self.assertEqual(rec.body, "Пользователь: Как настроить поиск?\n\nKimi: Возьмём SQLite FTS5."
                         "\n\nПользователь: И фильтр по проектам\n\nKimi: Добавил.")
        self.assertEqual((rec.source, rec.external_id, rec.project, rec.title),
                         ("kimi", "workspace/s1", "/tmp/project", "Поиск в базе"))
        self.assertEqual((rec.started_at, rec.updated_at), ("2026-10-29T11:49:32Z", "2026-10-29T11:49:35Z"))

    def test_discovery_skips_subagents_and_duplicate_roots(self):
        path = self.session([prompt("Главный разговор")])
        write_jsonl(path.parent.parent / "agent-0" / "wire.jsonl", [prompt("Подагент")])
        write_jsonl(path.parents[2] / "subagents" / "agent-1" / "context.jsonl", [{"role": "user", "content": "x"}])
        self.cfg["sources"]["kimi"]["roots"].append(str(self.root))
        self.assertEqual(list(self.connector.files()), [path])

    def test_service_sessions_and_sessions_without_user_words_are_skipped(self):
        for state in ({"custom": {"sessionKind": "conversation-title"}},
                      {"custom": {"sessionKind": "automation"}}):
            path = self.session([prompt("Сгенерируй заголовок"), answer("Заголовок")], state=state)
            self.assertEqual(self.connector.parse(path), [])
        self.assertEqual(self.connector.parse(self.session([answer("Самостоятельный ответ")])), [])
        self.assertEqual(self.connector.parse(self.session([prompt("title")], sid="ctitle-123", state={})), [])

    def test_index_supplies_project_and_first_question_supplies_title(self):
        path = self.session([prompt("Собери прототип")], state={})
        write_jsonl(self.root.parent / "session_index.jsonl", [
            {"sessionId": "s1", "sessionDir": str(path.parents[2]), "workDir": "/tmp/index-project"}])
        [rec] = self.connector.parse(path)
        self.assertEqual((rec.title, rec.project), ("Собери прототип", "/tmp/index-project"))

    def test_broken_json_line_does_not_drop_dialog(self):
        path = self.session([prompt("Привет"), answer("Готово")])
        with path.open("a") as out:
            out.write('{"type":')
        self.assertIn("Kimi: Готово", self.connector.parse(path)[0].body)

    def test_legacy_wire_preserves_history_across_compaction(self):
        def event(kind, payload, t):
            return {"timestamp": TS / 1000 + t, "message": {"type": kind, "payload": payload}}
        project = "/tmp/legacy"
        bucket = hashlib.md5(project.encode()).hexdigest()
        (self.root.parent).mkdir(parents=True)
        (self.root.parent / "kimi.json").write_text(json.dumps({"work_dirs": [{"path": project}]}))
        path = write_jsonl(self.root / bucket / "old" / "wire.jsonl", [
            event("TurnBegin", {"user_input": "Первый вопрос"}, 0),
            event("ContentPart", {"type": "think", "think": "скрыто"}, 1),
            event("ContentPart", {"type": "text", "text": "Первый "}, 2),
            event("ContentPart", {"type": "text", "text": "ответ"}, 3),
            event("TurnEnd", {}, 4),
            event("CompactionBegin", {}, 5),
            event("ContentPart", {"type": "text", "text": "служебное сжатие"}, 6),
            event("CompactionEnd", {}, 7),
            event("TurnBegin", {"user_input": [{"type": "text", "text": "Второй вопрос"}]}, 8),
            event("ContentPart", {"type": "text", "text": "Второй ответ"}, 9),
        ])
        write_jsonl(path.with_name("context.jsonl"), [{"role": "user", "content": "сжатый контекст"}])
        self.assertEqual(list(self.connector.files()), [path])
        [rec] = self.connector.parse(path)
        self.assertEqual(rec.project, project)
        self.assertEqual(rec.body, "Пользователь: Первый вопрос\n\nKimi: Первый ответ"
                         "\n\nПользователь: Второй вопрос\n\nKimi: Второй ответ")

    def test_legacy_context_fallback_ignores_internal_roles_and_blocks(self):
        path = write_jsonl(self.root / "old-project" / "old-session" / "context.jsonl", [
            {"role": "_system_prompt", "content": "система"},
            {"role": "_checkpoint", "id": 0},
            {"role": "user", "content": "Вопрос<system-reminder>инъекция</system-reminder>"},
            {"role": "tool", "content": "вывод"},
            {"role": "assistant", "content": [{"type": "think", "think": "скрыто"},
                                               {"type": "text", "text": "Ответ"}]},
        ])
        self.assertEqual(list(self.connector.files()), [path])
        [rec] = self.connector.parse(path)
        self.assertEqual(rec.body, "Пользователь: Вопрос\n\nKimi: Ответ")
        self.assertIsNotNone(rec.updated_at)

    def test_sync_waits_then_imports_updates_without_duplicates_and_excludes(self):
        path = self.session([prompt("Синхронизация поиска"), answer("Первая версия")])
        conn = db.connect(self.cfg["db_path"])
        self.addCleanup(conn.close)
        def run():
            return sync.sync(self.cfg, conn, only="kimi", log=lambda _: None)["kimi"]
        self.assertEqual(run()["waiting"], 1)
        old = time.time() - 3600
        os.utime(path, (old, old))
        self.assertEqual(run()["new"], 1)
        self.assertEqual(run()["new"], 0)
        self.assertEqual(len(search.search(conn, "Синхронизация", source="kimi")), 1)
        with path.open("a") as out:
            out.write(json.dumps(answer("Новая версия", TS + 9000)) + "\n")
        os.utime(path, (old + 1, old + 1))
        self.assertEqual(run()["changed"], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM records").fetchone()[0], 1)
        self.cfg["exclude_projects"] = ["/tmp/project"]
        run()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM records").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
