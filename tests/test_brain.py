"""Тесты: python3 -m unittest discover -s tests (из корня проекта)."""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timedelta, timezone
import http.server
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)

from brain import config, db, status, summarize  # noqa: E402
from brain import connectors  # noqa: E402
from brain import search as search_mod  # noqa: E402
from brain import sync as sync_mod  # noqa: E402
from brain.connectors import base as base_mod  # noqa: E402
from brain.connectors.base import ApiConnector, Batch, Record  # noqa: E402
from brain.connectors.chatgpt_export import ChatGPTExportConnector  # noqa: E402
from brain.connectors.claude_code import ClaudeCodeConnector  # noqa: E402
from brain.connectors.codex import CodexConnector  # noqa: E402
from brain.connectors.github import Fatal, GitHubConnector, month_start  # noqa: E402


def write_jsonl(path: Path, items) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n", encoding="utf-8")
    return path


def make_cfg(tmp: Path) -> dict:
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["db_path"] = str(tmp / "brain.db")
    cfg["inbox"] = str(tmp / "inbox")
    cfg["sources"]["claude_code"]["roots"] = [str(tmp / "claude")]
    cfg["sources"]["codex"]["roots"] = [str(tmp / "codex")]
    cfg["sources"]["kimi"]["roots"] = [str(tmp / "kimi" / "sessions")]
    cfg["llm"]["min_chars"] = 0  # старые тесты пересказа — на коротких текстах
    cfg["sources"]["github"]["enabled"] = False  # в настоящий GitHub тесты не ходят
    return cfg


def seed(conn, source, ext, title, updated, project=None, started=None, atoms=None, summary=None) -> int:
    """Запись с готовой карточкой: атомы и пересказ кладутся напрямую, без модели."""
    db.upsert(conn, Record(source, ext, title, "текст записи " + title, updated, started_at=started, project=project))
    rid = conn.execute("SELECT id FROM records WHERE source = ? AND external_id = ?", (source, ext)).fetchone()[0]
    conn.execute("UPDATE records SET atoms = ?, summary = ? WHERE id = ?", ("\n".join(atoms or []) or None, summary, rid))
    return rid


def seed_context(conn) -> None:
    """Шесть записей: проект из папки и репозитория, второй проект, запись без проекта, старая запись."""
    seed(conn, "claude_code", "r1", "Лендинг клуба", "2026-11-11T10:00:00Z", project="~/p/club-landing",
         started="2026-11-11T09:00:00Z", summary="Обсудили лендинг",
         atoms=[f"[decision] Решение {k}" for k in range(1, 7)] + ["[error] Сборка падала", "[command] npm run build"])
    seed(conn, "codex", "r2", "Шрифты лендинга", "2026-11-10T10:00:00Z", project="~/p/club-landing",
         started="2026-10-01T10:00:00Z", atoms=["[todo] Проверить шрифт"])
    seed(conn, "github", "r3", "PR без знаний", "2026-11-09T10:00:00Z", project="vladilenm/club_landing")
    seed(conn, "codex", "r4", "Вебхук бота", "2026-11-11T08:00:00Z", project="~/dev/club-bot",
         atoms=["[fix] Починили вебхук"])
    seed(conn, "notion", "r5", "Заметка о цене", "2026-11-11T11:00:00Z", atoms=["[decision] Цена обсуждается позже"])
    seed(conn, "codex", "r6", "Старый бот", "2026-10-01T10:00:00Z", project="~/dev/club-bot",
         atoms=["[decision] Старое решение"])


def claude_session():
    return [
        {"type": "custom-title", "customTitle": "Второй мозг", "sessionId": "s1"},
        {"type": "user", "cwd": "/tmp/proj", "timestamp": "2026-10-20T10:00:00.123Z",
         "message": {"role": "user", "content": "Как настроить поиск?\n<system-reminder>секрет</system-reminder>"}},
        {"type": "user", "isMeta": True, "timestamp": "2026-10-20T10:00:01Z",
         "message": {"role": "user", "content": [{"type": "text", "text": "Base directory for this skill"}]}},
        {"type": "assistant", "timestamp": "2026-10-20T10:00:05Z",
         "message": {"role": "assistant", "content": [
             {"type": "thinking", "thinking": "думаю"},
             {"type": "text", "text": "Возьмём SQLite FTS5."},
             {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "user", "timestamp": "2026-10-20T10:00:06Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "content": "вывод ls"}]}},
        {"type": "user", "timestamp": "2026-10-20T10:00:07Z",
         "message": {"role": "user", "content": "<task-notification>\nфон</task-notification>"}},
        {"type": "assistant", "isSidechain": True, "timestamp": "2026-10-20T10:00:08Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "побочная ветка"}]}},
    ]


def codex_session(sid: str):
    return [
        {"type": "session_meta", "timestamp": "2026-10-24T21:44:16Z",
         "payload": {"id": sid, "cwd": "/tmp/c", "thread_source": "user"}},
        {"type": "response_item", "timestamp": "2026-10-24T21:44:17Z",
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Собери превью"}]}},
        {"type": "response_item", "timestamp": "2026-10-24T21:45:00Z",
         "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Готово."}]}},
    ]


def compress(path: Path) -> Path:
    """Как Codex: rollout-….jsonl заменяется на rollout-….jsonl.zst."""
    try:
        zstd = base_mod.zstd_path()
    except RuntimeError:
        raise unittest.SkipTest("нет zstd")
    subprocess.run([zstd, "-q", "--rm", str(path)], check=True)
    return path.with_name(path.name + ".zst")


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = make_cfg(self.tmp)

    def test_claude_code_keeps_only_dialog(self):
        path = write_jsonl(self.tmp / "claude" / "-tmp-proj" / "s1.jsonl", claude_session())
        [rec] = ClaudeCodeConnector(self.cfg).parse(path)
        self.assertEqual(rec.title, "Второй мозг")
        self.assertEqual(rec.project, "/tmp/proj")
        self.assertEqual(rec.started_at, "2026-10-20T10:00:00Z")
        self.assertEqual(rec.updated_at, "2026-10-20T10:00:05Z")
        self.assertIn("Пользователь: Как настроить поиск?", rec.body)
        self.assertIn("Claude: Возьмём SQLite FTS5.", rec.body)
        for noise in ("секрет", "Base directory", "вывод ls", "фон", "думаю", "побочная ветка"):
            self.assertNotIn(noise, rec.body)

    def test_claude_code_without_user_words_is_skipped(self):
        path = write_jsonl(self.tmp / "claude" / "p" / "s2.jsonl", [
            {"type": "user", "timestamp": "2026-10-20T10:00:00Z",
             "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}}])
        self.assertEqual(ClaudeCodeConnector(self.cfg).parse(path), [])

    def test_codex_real_session_and_guardian(self):
        real = write_jsonl(self.tmp / "codex" / "rollout-a.jsonl", [
            {"type": "session_meta", "timestamp": "2026-10-24T21:44:16Z",
             "payload": {"id": "abc", "cwd": "/tmp/c", "thread_source": "user"}},
            {"type": "response_item", "timestamp": "2026-10-24T21:44:17Z",
             "payload": {"type": "message", "role": "user", "content": [
                 {"type": "input_text", "text": "<environment_context>…</environment_context>"},
                 {"type": "input_text", "text": "# AGENTS.md instructions for /tmp/c"},
                 {"type": "input_text", "text": "\n# Files mentioned by the user:\n\n## brief.txt: /tmp/brief.txt\n\n"
                                                "## My request:\nСобери превью ролика"}]}},
            {"type": "response_item", "timestamp": "2026-10-24T21:45:00Z",
             "payload": {"type": "message", "role": "assistant",
                         "content": [{"type": "output_text", "text": "Готово, превью в папке."}]}},
        ])
        [rec] = CodexConnector(self.cfg).parse(real)
        self.assertEqual((rec.external_id, rec.title, rec.project), ("abc", "Собери превью ролика", "/tmp/c"))
        self.assertNotIn("environment_context", rec.body)
        self.assertNotIn("AGENTS.md", rec.body)
        self.assertIn("Codex: Готово, превью в папке.", rec.body)

        guardian = write_jsonl(self.tmp / "codex" / "rollout-b.jsonl", [
            {"type": "session_meta", "payload": {"id": "g", "thread_source": "guardian_review"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "проверь"}]}}])
        self.assertEqual(CodexConnector(self.cfg).parse(guardian), [])

    def test_codex_legacy_session_without_thread_source(self):
        def session(name, source):
            return write_jsonl(self.tmp / "codex" / name, [
                {"type": "session_meta", "payload": {"id": name, "source": source}},
                {"type": "response_item", "timestamp": "2026-05-06T09:41:21Z",
                 "payload": {"type": "message", "role": "user",
                             "content": [{"type": "input_text", "text": "Почини сборку"}]}}])
        [rec] = CodexConnector(self.cfg).parse(session("rollout-old.jsonl", "vscode"))
        self.assertEqual(rec.title, "Почини сборку")
        agent = session("rollout-agent.jsonl", {"subagent": {"thread_spawn": {"agent_role": "explorer"}}})
        self.assertEqual(CodexConnector(self.cfg).parse(agent), [])

    def test_codex_edited_message_branch(self):
        thread, branch = "01a110c0-8ed8-71b2-994b-572c1e400486", "01a11406-71eb-73b2-9edc-8e903a4ca268"

        def says(n, role, text):
            kind = "input_text" if role == "user" else "output_text"
            return {"ordinal": n, "type": "response_item", "timestamp": f"2026-10-06T10:0{n}:00Z",
                    "payload": {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}}
        meta = {"id": thread, "cwd": "/tmp/c", "thread_source": "user"}
        main = write_jsonl(self.tmp / "codex" / "2026" / "10" / "06" / f"rollout-2026-10-06T13-26-57-{thread}.jsonl", [
            {"ordinal": 0, "type": "session_meta", "payload": meta},
            says(1, "user", "Разбери чат"), says(2, "assistant", "Разобрал."),
            says(3, "user", "Сделай таблицу"), says(4, "assistant", "Старая таблица")])
        # Пользователь поправил второй вопрос: ветка продолжает историю с третьей строки.
        edited = write_jsonl(self.tmp / "codex" / "2026" / "10" / "07" /
                             f"rollout-2026-10-07T04-42-09-{thread}_{branch}.jsonl", [
            {"ordinal": 3, "type": "session_meta",
             "payload": dict(meta, history_base={"thread_id": thread, "end_ordinal_exclusive": 3})},
            says(4, "user", "Сделай список"), says(5, "assistant", "Новый список")])
        connector = CodexConnector(self.cfg)
        [rec] = connector.parse(main)
        self.assertEqual(rec.body, "Пользователь: Разбери чат\n\nCodex: Разобрал.\n\n"
                                   "Пользователь: Сделай список\n\nCodex: Новый список")
        self.assertEqual((rec.external_id, rec.title, rec.started_at), (thread, "Разбери чат", "2026-10-06T10:01:00Z"))
        self.assertTrue(rec.location.endswith(edited.name))
        self.assertEqual(connector.parse(edited), [rec])

    def test_codex_reads_compressed_sessions(self):
        plain = write_jsonl(self.tmp / "codex" / "2026" / "rollout-z.jsonl", codex_session("z"))
        packed = compress(plain)
        self.assertEqual([p.name for p in CodexConnector(self.cfg).files()], ["rollout-z.jsonl.zst"])
        [rec] = CodexConnector(self.cfg).parse(packed)
        self.assertEqual((rec.external_id, rec.title), ("z", "Собери превью"))
        self.assertIn("Codex: Готово.", rec.body)

        broken = self.tmp / "codex" / "rollout-bad.jsonl.zst"
        broken.write_bytes(b"\x28\xb5\x2f\xfd not zstd")
        with self.assertRaises(RuntimeError):
            CodexConnector(self.cfg).parse(broken)

    def test_chatgpt_export_takes_active_branch(self):
        conv = {
            "id": "c1", "title": "Цены на курс", "create_time": 1761000000, "update_time": 1761003600,
            "current_node": "a2",
            "mapping": {
                "root": {"message": None, "parent": None},
                "u1": {"parent": "root", "message": {"author": {"role": "user"},
                       "content": {"content_type": "text", "parts": ["Сколько стоит курс?"]}}},
                "a1": {"parent": "u1", "message": {"author": {"role": "assistant"},
                       "content": {"content_type": "text", "parts": ["Старый ответ"]}}},
                "a2": {"parent": "u1", "message": {"author": {"role": "assistant"},
                       "content": {"content_type": "text", "parts": ["49 000 рублей"]}}},
            },
        }
        inbox = self.tmp / "inbox"
        inbox.mkdir()
        with zipfile.ZipFile(inbox / "export.zip", "w") as zf:
            zf.writestr("conversations.json", json.dumps([conv], ensure_ascii=False))
        [rec] = ChatGPTExportConnector(self.cfg).parse(inbox / "export.zip")
        self.assertEqual((rec.source, rec.external_id, rec.title), ("chatgpt", "c1", "Цены на курс"))
        self.assertIn("ChatGPT: 49 000 рублей", rec.body)
        self.assertNotIn("Старый ответ", rec.body)


class RegistryTests(unittest.TestCase):
    def test_every_connector_is_described(self):
        reg = connectors.registry()
        self.assertEqual([r["source"] for r in reg],
                         ["claude_code", "codex", "chatgpt", "github", "gmail", "yandex_mail", "kimi", "notion", "youtube"])
        self.assertEqual([r["connector"] for r in reg],
                         ["claude_code", "codex", "chatgpt_export", "github", "gmail", "yandex_mail", "kimi", "notion", "youtube"])
        for r in reg:
            self.assertTrue(r["title"] and r["about"], r)
        types = {r["source"]: r["type"] for r in reg}
        self.assertEqual((types["chatgpt"], types["github"]), ("file", "api"))
        self.assertTrue(dict((r["source"], r["hint"]) for r in reg)["chatgpt"])

    def test_names_come_from_registry(self):
        from brain import mcp_server, render
        self.assertEqual(render.source_name("chatgpt"), "ChatGPT")
        self.assertEqual(render.source_name("неизвестный"), "неизвестный")
        [search_tool] = [t for t in mcp_server.TOOLS if t["name"] == "search"]
        self.assertEqual(search_tool["inputSchema"]["properties"]["source"]["enum"], list(connectors.source_names()))
        for r in connectors.registry():
            self.assertIn(r["about"], mcp_server.INSTRUCTIONS)

    def test_file_roots(self):
        cfg = make_cfg(Path(tempfile.mkdtemp()))
        self.assertEqual(ClaudeCodeConnector(cfg).roots(), cfg["sources"]["claude_code"]["roots"])
        self.assertEqual(ChatGPTExportConnector(cfg).roots(), [cfg["inbox"]])
        self.assertFalse(ChatGPTExportConnector.one_record_per_file)


class StoreAndSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = make_cfg(self.tmp)
        self.conn = db.connect(self.cfg["db_path"])

    def rec(self, ext, body, updated, title="t"):
        return Record(source="codex", external_id=ext, title=title, body=body, updated_at=updated)

    def test_upsert_statuses(self):
        r = self.rec("1", "текст", "2026-10-01T00:00:00Z")
        self.assertEqual(db.upsert(self.conn, r), "new")
        self.assertEqual(db.upsert(self.conn, r), "same")
        r.body = "другой текст"
        self.assertEqual(db.upsert(self.conn, r), "changed")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM records_fts").fetchone()[0], 1)

    def test_word_forms_case_and_recency(self):
        db.upsert(self.conn, self.rec("old", "Ошибка в клубном боте: цена 30 000", "2024-01-01T00:00:00Z"))
        db.upsert(self.conn, self.rec("new", "Ошибка в клубном боте: цена 49 000", "2026-10-25T00:00:00Z"))
        db.upsert(self.conn, self.rec("other", "Про монтаж видео", "2026-10-26T00:00:00Z"))
        results = search_mod.search(self.conn, "ошибки клубного бота", limit=5)
        self.assertEqual([r["updated_at"][:4] for r in results], ["2026", "2024"])
        self.assertIn("49 000", results[0]["snippet"])
        self.assertEqual(search_mod.query_terms("бота 2026 FTS5"), ["бота", "бот", "2026", "fts5"])
        self.assertTrue(search_mod.search(self.conn, "ОШИБКА"))
        self.assertEqual(search_mod.search(self.conn, "в и"), [])

    def test_secrets_never_reach_the_index(self):
        fake = "sc_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3"
        body = (f"Ключ: {fake}\napi_key = 'abcdefghijklmnop1234'\n"
                "Authorization: Bearer abc.def-ghijklmnopqrstu\nобычный текст про токены")
        db.upsert(self.conn, self.rec("s", body, "2026-10-27T00:00:00Z"))
        stored = self.conn.execute("SELECT body FROM records").fetchone()[0]
        for secret in (fake, "abcdefghijklmnop1234", "ghijklmnopqrstu"):
            self.assertNotIn(secret, stored)
        self.assertIn("Ключ: [секрет скрыт]", stored)
        self.assertIn("обычный текст про токены", stored)
        hits = self.conn.execute("SELECT COUNT(*) FROM records_fts WHERE records_fts MATCH ?", ('"E5f6G7h8"',))
        self.assertEqual(hits.fetchone()[0], 0)

    def test_exact_form_beats_stem_match(self):
        db.upsert(self.conn, self.rec("a", "Закрыли ключ мозаикой", "2026-10-25T00:00:00Z"))
        db.upsert(self.conn, self.rec("b", "Строим второй мозг", "2026-10-20T00:00:00Z"))
        self.assertIn("мозг", search_mod.search(self.conn, "мозг")[0]["snippet"])

    def test_exact_phrase_ranks_first(self):
        db.upsert(self.conn, self.rec("a", "Второй прогон теста. " * 5 + "Мозг устал.", "2026-10-25T00:00:00Z"))
        db.upsert(self.conn, self.rec("b", "Собираем второй мозг на SQLite", "2026-10-20T00:00:00Z"))
        self.assertIn("второй мозг", search_mod.search(self.conn, "второй мозг")[0]["snippet"])

    def test_kind_is_stored(self):
        db.upsert(self.conn, Record("github", "pr:vm/b#1", "t", "текст", "2026-10-01T00:00:00Z", kind="discussion"))
        db.upsert(self.conn, self.rec("1", "текст", "2026-10-01T00:00:00Z"))
        kinds = dict(self.conn.execute("SELECT source, kind FROM records"))
        self.assertEqual(kinds, {"github": "discussion", "codex": "dialog"})

    def test_cursor_roundtrip(self):
        self.assertIsNone(db.get_cursor(self.conn, "github"))
        db.set_cursor(self.conn, "github", "a")
        db.set_cursor(self.conn, "github", "b")
        self.assertEqual(db.get_cursor(self.conn, "github"), "b")
        db.reset_cursor(self.conn, "github")
        self.assertIsNone(db.get_cursor(self.conn, "github"))


class MigrationTests(unittest.TestCase):
    def test_old_db_gets_kind_column(self):
        path = Path(tempfile.mkdtemp()) / "old.db"
        old = sqlite3.connect(str(path))
        old.executescript("""
            CREATE TABLE records (
                id INTEGER PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL, location TEXT, project TEXT,
                started_at TEXT, updated_at TEXT NOT NULL, content_hash TEXT NOT NULL, ingested_at TEXT NOT NULL,
                ai_title TEXT, summary TEXT, questions TEXT, keywords TEXT, atoms TEXT,
                summarized_hash TEXT, summarized_at TEXT, summary_error TEXT, UNIQUE (source, external_id));
            INSERT INTO records (source, external_id, body, updated_at, content_hash, ingested_at)
            VALUES ('codex', '1', 'текст', '2026-10-01T00:00:00Z', 'h', '2026-10-01T00:00:00Z');
        """)
        old.commit()
        old.close()
        conn = db.connect(str(path))
        self.assertEqual(conn.execute("SELECT kind FROM records").fetchone()[0], "dialog")
        conn.close()
        db.connect(str(path)).close()  # повторное подключение не пытается добавить колонку второй раз


class TopicsTests(unittest.TestCase):
    def test_normalize_merges_word_forms(self):
        from brain import topics
        self.assertEqual({topics.normalize(w) for w in ("Рилс", "рилса", "рилсу", "рилсы")}, {"рилс"})
        self.assertEqual(topics.normalize("клуба"), topics.normalize("клубу"))
        self.assertEqual(topics.normalize("Skills"), topics.normalize("skill"))
        self.assertEqual(topics.normalize("Claude  Code"), "claude code")
        self.assertEqual(topics.normalize("next.js"), "next.js")

    def test_normalize_drops_noise(self):
        from brain import topics
        for noise in ("2026-10-19", "19 октября 2026", "октябрь 2026", "1080×1920", "final.mp4", "lessons.md", "ок", "",
                      "/Users/vladilen/Downloads/Telegram Desktop/x", "~/brain/inbox"):
            self.assertIsNone(topics.normalize(noise), noise)

    def test_contacts_months_and_placeholders_are_not_topics(self):
        from brain import topics
        for noise in ("i@example.ru", "@nick", "https://t.me/nick", "октябрь", "мая", "main"):
            self.assertIsNone(topics.normalize(noise), noise)
        self.assertEqual(topics.normalize("@vitejs/plugin-react"), "@vitejs/plugin-react")
        self.assertEqual(topics.record_topics(None, "Без названия"), [])
        self.assertEqual([k for k, _ in topics.record_topics(None, "owner/mac-draw — коммиты за ноябрь 2026")], ["mac-draw"])

    def test_title_words_become_topics_only_if_known_as_keywords(self):
        from brain import topics
        rows = [(1, "вебинар, воронка", ""), (2, "", "Какие вебинары создать"), (3, None, "Основной вебинар")]
        t = topics.build(rows)
        self.assertEqual(t.keys[2], ["вебинар"])
        self.assertEqual(t.keys[3], ["вебинар"])
        self.assertEqual(topics.build([(2, "", "Монтаж рилса")]).keys[2], ["монтаж", "рилс"])
        self.assertEqual(topics.build(rows, stop=["Вебинары"]).keys[1], ["воронк"])

    def test_record_topics_falls_back_to_title(self):
        from brain import topics
        self.assertEqual([k for k, _ in topics.record_topics("Рилс, рилса, 2026-10-19", "x")], ["рилс"])
        self.assertEqual([k for k, _ in topics.record_topics(None, "Монтаж рилса для клуба")], ["монтаж", "рилс", "клуб"])

    def _rows(self):
        # 20 записей-фона, чтобы сработал порог «не больше 25%» для общей темы «claude code».
        rows = [(i, f"claude code, фон{i}", "") for i in range(100, 120)]
        rows += [
            (1, "claude code, рилс, субтитры, монтаж", ""),
            (2, "claude code, рилса, субтитры, монтаж", ""),
            (3, "claude code, postgresql", ""),
        ]
        return rows

    def test_build_links_records_with_shared_rare_topics(self):
        from brain import topics
        t = topics.build(self._rows())
        self.assertIn(2, [o for o, _ in t.neighbors[1]])
        self.assertEqual(t.neighbors[3], [])  # общая только «claude code» — она в 92% записей
        self.assertIn((1, 2), [(a, b) for a, b, _ in t.pairs])
        for a, b, w in t.pairs:
            self.assertLess(a, b)
            self.assertTrue(topics.MIN_SIM <= w <= 1)
        self.assertEqual(len(t.pairs), len({(a, b) for a, b, _ in t.pairs}))
        self.assertTrue(t.shown[1] and all(isinstance(s, str) for s in t.shown[1]))

    def test_build_caps_own_neighbors(self):
        from brain import topics
        rows = [(i, "рилс, субтитры, монтаж", "") for i in range(1, 7)]
        rows += [(i, f"postgresql, фон{i}", "") for i in range(10, 14)]  # иначе idf общих тем равен нулю
        t = topics.build(rows)
        for i in range(1, 7):
            self.assertLessEqual(len(t.neighbors[i]), topics.MAX_NEIGHBORS)
        union = {(min(i, o), max(i, o)) for i, _, _ in rows for o, _ in t.neighbors[i]}
        self.assertEqual(union, {(a, b) for a, b, _ in t.pairs})

    def test_project_topics_prefer_repeated(self):
        from brain import topics
        t = topics.build([(1, "рилс, субтитры, редкое1", ""), (2, "рилсы, субтитры, редкое2", ""),
                          (3, "рилс, монтаж", ""), (4, "postgresql, монтаж", "")])
        self.assertEqual(set(t.project_topics([1, 2, 3])[:2]), {"рилс", "субтитры"})
        self.assertNotIn("редкое1", t.project_topics([1, 2, 3])[:2])
        self.assertNotIn("рилс", t.project_topics([1, 2, 3], exclude={"рилс"}))


class DashboardTests(unittest.TestCase):
    def test_page_embeds_data_safely(self):
        from brain import dashboard
        cfg = make_cfg(Path(tempfile.mkdtemp()))
        conn = db.connect(cfg["db_path"])
        db.upsert(conn, Record("codex", "1", "Опасное </script><b>название", "текст", "2026-10-20T00:00:00Z"))
        db.upsert(conn, Record("claude_code", "2", "Обычный", "текст", "2026-10-21T00:00:00Z", project="~/p"))
        path = dashboard.build(conn, cfg)
        html = path.read_text(encoding="utf-8")
        self.assertEqual(html.count("</script>"), 1)
        self.assertNotIn("/*__DATA__*/", html)
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        start = html.index("const DATA = ") + len("const DATA = ")
        data = json.loads(html[start:html.index(";\n", start)].replace("<\\/", "</"))
        self.assertEqual([r["title"] for r in data["records"]], ["Обычный", "Опасное </script><b>название"])
        self.assertEqual(data["state"], "none")
        self.assertFalse(data["records"][0]["card"])
        self.assertEqual([m["source"] for m in data["sources_meta"]], list(connectors.source_names()))
        self.assertEqual(sorted(data["files_seen"]), ["claude_code", "codex", "kimi"])
        self.assertEqual(data["records"][0]["pkey"], "p")
        self.assertEqual(data["projects"]["p"]["paths"], ["~/p"])
        self.assertIn("edges", data["graph"])
        self.assertTrue(all("topics" in r for r in data["records"]))
        self.assertIn('id="graph"', html)
        self.assertEqual(sorted(data["sync"]), sorted(connectors.source_names()))


    def test_assemble_refuses_closing_script_in_js(self):
        from brain import dashboard
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp)
            (web / "index.html").write_text("<style>/*__CSS__*/</style><script>const DATA = /*__DATA__*/null;\n/*__JS__*/</script>")
            (web / "style.css").write_text("")
            for name in dashboard.SCRIPTS:
                (web / name).write_text("const x = '</script>';" if name == "app.js" else "")
            with self.assertRaises(ValueError):
                dashboard.assemble(web)


class ProjectKeyTests(unittest.TestCase):
    def test_folder_and_repo_with_same_name_merge(self):
        from brain.dashboard import project_key
        self.assertEqual(project_key("~/Documents/vscode/club-landing")[0], project_key("vladilenm/club_landing")[0])
        self.assertEqual(project_key("~/Documents/vscode/club-landing"), ("club-landing", "club-landing"))
        self.assertEqual(project_key("~/Library/Application Support/Claude/scratch-workspaces/a/b/scratch-2026-10-21-f6"),
                         ("scratch", "Черновики"))
        self.assertEqual(project_key(None), ("", "без проекта"))

    def test_tool_folders_are_not_projects(self):
        from brain.projects import project_key
        for path in ("~/Documents/Codex/2026-11-06/5", "~/Documents/Codex/2026-08-21/new-chat",
                     "/Users/someone/Documents/Codex/2026-08-21/new-chat",
                     "~/.codex/.chatgpt-projects/g-p-6ac06d681a1881918cf22375ec047475",
                     "~/Documents/kimi/tasks/2026-11-01/10-43-38-02f8c257",
                     "/private/tmp/claude-501/x/scratchpad/name-demo", "~", "~/Desktop"):
            self.assertEqual(project_key(path), ("", "без проекта"), path)
        # Папка внутри ~/Documents/Codex глубже одного уровня и обычные папки остаются проектами.
        self.assertEqual(project_key("~/Documents/ChatGPT/club_bot")[0], "club-bot")
        self.assertEqual(project_key("~/Documents/Codex/2026-11-06/site/app")[0], "app")
        self.assertEqual(project_key("~/dev/client/.claude/worktrees/eager-varahamihira-7b868a"), ("client", "client"))
        self.assertEqual(project_key("/Users/someone/dev/client"), ("client", "client"))

    def test_aliases_and_ignore_from_config(self):
        from brain import projects
        self.addCleanup(projects.configure, None)
        projects.configure({"project_aliases": {"modern_second_brain": "brain", "Idea 2 Content": "idea-to-content"},
                            "project_ignore": ["~/bench-runs/*", "tmp-*"]})
        self.assertEqual(projects.project_key("vladilenm/modern-second-brain"), ("brain", "brain"))
        self.assertEqual(projects.project_key("~/brain"), ("brain", "brain"))
        self.assertEqual(projects.project_key("vladilenm/idea-2-content")[0], "idea-to-content")
        self.assertEqual(projects.project_key("~/bench-runs/kimi"), ("", "без проекта"))
        self.assertEqual(projects.project_key("~/dev/tmp-probe"), ("", "без проекта"))
        self.assertEqual(projects.project_key("~/dev/keep")[0], "keep")
        projects.configure(None)
        self.assertEqual(projects.project_key("vladilenm/modern-second-brain")[0], "modern-second-brain")


class SyncTests(unittest.TestCase):
    def test_idle_window_and_cursor(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = make_cfg(tmp)
        conn = db.connect(cfg["db_path"])
        path = write_jsonl(tmp / "claude" / "-tmp-proj" / "s1.jsonl", claude_session())
        quiet = lambda *_: None  # noqa: E731
        self.assertEqual(sync_mod.sync(cfg, conn, only="claude_code", log=quiet)["claude_code"]["waiting"], 1)
        old = time.time() - 3600
        os.utime(path, (old, old))
        self.assertEqual(sync_mod.sync(cfg, conn, only="claude_code", log=quiet)["claude_code"]["new"], 1)
        again = sync_mod.sync(cfg, conn, only="claude_code", log=quiet)["claude_code"]
        self.assertEqual((again["new"], again["changed"]), (0, 0))
        state = conn.execute("SELECT * FROM sync_state WHERE source = 'claude_code'").fetchone()
        self.assertIsNone(state["last_error"])
        self.assertIsNotNone(state["last_success_at"])

    def test_codex_session_compressed_after_sync(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = make_cfg(tmp)
        conn = db.connect(cfg["db_path"])
        quiet = lambda *_: None  # noqa: E731
        old = time.time() - 3600
        plain = write_jsonl(tmp / "codex" / "rollout-s.jsonl", codex_session("s"))
        os.utime(plain, (old, old))
        self.assertEqual(sync_mod.sync(cfg, conn, only="codex", log=quiet)["codex"]["new"], 1)

        packed = compress(plain)
        os.utime(packed, (old, old))
        totals = sync_mod.sync(cfg, conn, only="codex", log=quiet)["codex"]
        self.assertEqual((totals["new"], totals["changed"], totals["errors"]), (0, 0, 0))
        [location] = [r[0] for r in conn.execute("SELECT location FROM records WHERE source = 'codex'")]
        self.assertTrue(location.endswith("rollout-s.jsonl.zst"))
        self.assertEqual([r[0] for r in conn.execute("SELECT path FROM file_state")], [str(packed)])


class ExcludeTests(unittest.TestCase):
    def test_excluded_project_is_skipped_and_purged(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = make_cfg(tmp)
        cfg["exclude_projects"] = ["~/x/trade"]
        conn = db.connect(cfg["db_path"])
        db.upsert(conn, Record("claude_code", "old", "t", "старое", "2026-10-01T00:00:00Z", project="~/x/trade"))
        db.upsert(conn, Record("claude_code", "keep", "t", "сосед", "2026-10-01T00:00:00Z", project="~/x/trade-bot"))
        items = claude_session()
        items[1]["cwd"] = str(Path.home() / "x" / "trade" / "worker")
        path = write_jsonl(tmp / "claude" / "-x-trade" / "s1.jsonl", items)
        old = time.time() - 3600
        os.utime(path, (old, old))
        totals = sync_mod.sync(cfg, conn, only="claude_code", log=lambda *_: None)["claude_code"]
        self.assertEqual((totals["new"], totals["excluded"]), (0, 1))
        self.assertEqual([r[0] for r in conn.execute("SELECT external_id FROM records")], ["keep"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM records_fts").fetchone()[0], 1)


class FakeApi(ApiConnector):
    """API-источник для тестов: отдаёт заранее заданные пачки после курсора."""
    name = source = "fake"
    title = "Фейк"
    about = "тестовые записи"
    pages: list = []    # [(records, cursor) или исключение]
    cursors: list = []  # с какими курсорами звали fetch

    def fetch(self, cursor):
        FakeApi.cursors.append(cursor)
        for item in FakeApi.pages:
            if isinstance(item, Exception):
                raise item
            records, position = item
            if cursor is None or position > cursor:
                yield Batch(records, position)

    def excludes(self, project):
        return project == "скрытый"


class ApiSyncTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg(Path(tempfile.mkdtemp()))
        self.cfg["sources"]["fake"] = {"enabled": True, "every_minutes": 60}
        self.conn = db.connect(self.cfg["db_path"])
        connectors.ALL["fake"] = FakeApi
        self.addCleanup(connectors.ALL.pop, "fake")
        FakeApi.pages, FakeApi.cursors = [], []

    def rec(self, ext, project=None):
        return Record("fake", ext, f"запись {ext}", f"текст {ext}", "2026-10-01T00:00:00Z",
                      project=project, kind="document")

    def run_sync(self, **kw):
        return sync_mod.sync(self.cfg, self.conn, log=lambda *_: None, **kw).get("fake")

    def test_cursor_moves_only_with_saved_batch(self):
        FakeApi.pages = [([self.rec("1")], "1"), ([self.rec("2")], "2"), RuntimeError("сеть упала")]
        totals = self.run_sync(only="fake")
        self.assertEqual((totals["new"], totals["errors"]), (2, 1))
        self.assertEqual(db.get_cursor(self.conn, "fake"), "2")
        state = self.conn.execute("SELECT last_error FROM sync_state WHERE source = 'fake'").fetchone()
        self.assertEqual(state["last_error"], "сеть упала")
        FakeApi.pages = [([self.rec("1")], "1"), ([self.rec("2")], "2"), ([self.rec("3")], "3")]
        totals = self.run_sync(only="fake")
        self.assertEqual(FakeApi.cursors[-1], "2")
        self.assertEqual((totals["new"], totals["errors"]), (1, 0))
        kind = self.conn.execute("SELECT kind FROM records WHERE external_id = '3'").fetchone()[0]
        self.assertEqual(kind, "document")

    def test_broken_batch_leaves_nothing_behind(self):
        bad = self.rec("плохая")
        bad.body = None  # upsert упадёт на этой записи
        FakeApi.pages = [([self.rec("1")], "1"), ([self.rec("2"), bad], "2")]
        totals = self.run_sync(only="fake")
        self.assertEqual([r[0] for r in self.conn.execute("SELECT external_id FROM records")], ["1"])
        self.assertEqual(db.get_cursor(self.conn, "fake"), "1")
        self.assertEqual((totals["new"], totals["errors"]), (1, 1))

    def test_schedule_skips_until_due(self):
        FakeApi.pages = [([self.rec("1")], "1")]
        self.assertEqual(self.run_sync()["new"], 1)
        self.assertIsNone(self.run_sync())                 # рано: прошло меньше часа
        self.assertIsNotNone(self.run_sync(only="fake"))   # --source запускает сразу
        self.conn.execute("UPDATE sync_state SET last_run_at = '2000-01-01T00:00:00Z' WHERE source = 'fake'")
        self.assertIsNotNone(self.run_sync())

    def test_failed_run_retries_next_sync(self):
        FakeApi.pages = [RuntimeError("нет связи")]
        self.assertEqual(self.run_sync()["errors"], 1)
        FakeApi.pages = [([self.rec("1")], "1")]
        self.assertEqual(self.run_sync()["new"], 1)  # ошибка не занимает час до следующего запуска
        self.assertIsNone(self.run_sync())           # после успеха — снова по расписанию

    def test_rescan_starts_from_scratch(self):
        FakeApi.pages = [([self.rec("1")], "1")]
        self.run_sync()
        totals = self.run_sync(rescan=True)  # и расписание не мешает
        self.assertIsNone(FakeApi.cursors[-1])
        self.assertEqual((totals["new"], totals["changed"]), (0, 0))

    def test_connector_exclusion_skips_and_purges(self):
        db.upsert(self.conn, self.rec("старая", project="скрытый"))
        FakeApi.pages = [([self.rec("1", project="скрытый"), self.rec("2", project="открытый")], "1")]
        totals = self.run_sync(only="fake")
        self.assertEqual(totals["excluded"], 1)
        self.assertEqual([r[0] for r in self.conn.execute("SELECT external_id FROM records")], ["2"])


class Pages(list):
    """Ответ из нескольких страниц: FakeGitHub отдаёт их по ?page=N со ссылкой rel="next"."""


class FakeGitHub(http.server.BaseHTTPRequestHandler):
    """Фейковый REST API GitHub: ответ по пути запроса; число — код ошибки; нет пути — 404."""
    routes: dict = {}
    hits: list = []  # (путь, параметры, Authorization)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        FakeGitHub.hits.append((path, params, self.headers.get("Authorization")))
        answer = FakeGitHub.routes.get(path)
        link = None
        if isinstance(answer, Pages):
            page = int(params.get("page", ["1"])[0])
            if page < len(answer):
                link = f'<http://127.0.0.1:{self.server.server_port}{path}?page={page + 1}>; rel="next"'
            answer = answer[page - 1]
        if answer is None:
            self.reply(404, {"message": "Not Found"})
        elif isinstance(answer, int):
            self.reply(answer, {"message": "ошибка"})
        else:
            self.reply(200, answer, link)

    def reply(self, code, data, link=None):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if link:
            self.send_header("Link", link)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


GH_REPO_URL = "https://api.github.com/repos/vm/brain"


def gh_issue():
    return {"number": 5, "title": "Где хранить базу", "state": "closed", "user": {"login": "vm"},
            "created_at": "2026-10-01T09:00:00Z", "updated_at": "2026-10-02T10:00:00Z",
            "body": "Варианты: Postgres или SQLite", "html_url": "https://github.com/vm/brain/issues/5",
            "repository_url": GH_REPO_URL}


def gh_pr():
    return {"number": 7, "title": "Поиск BM25", "state": "closed", "user": {"login": "vm"},
            "created_at": "2026-10-10T09:00:00Z", "updated_at": "2026-10-12T00:00:00Z", "body": "",
            "html_url": "https://github.com/vm/brain/pull/7", "repository_url": GH_REPO_URL,
            "pull_request": {"merged_at": "2026-10-12T00:00:00Z"}}


def gh_discussion_routes():
    return {
        "/user": {"login": "vm"},
        "/user/repos": [],
        "/search/issues": Pages([{"total_count": 2, "items": [gh_issue()]},
                                 {"total_count": 2, "items": [gh_pr()]}]),
        "/repos/vm/brain/issues/5/comments": [
            {"user": {"login": "anna"}, "created_at": "2026-10-02T10:00:00Z", "body": "Давайте на SQLite"}],
        "/repos/vm/brain/issues/7/comments": [],
        "/repos/vm/brain/pulls/7/reviews": [
            {"user": {"login": "anna"}, "state": "CHANGES_REQUESTED", "submitted_at": "2026-10-11T09:00:00Z",
             "body": "Нужны тесты"},
            {"user": {"login": "anna"}, "state": "COMMENTED", "submitted_at": "2026-10-11T09:01:00Z", "body": ""}],
        "/repos/vm/brain/pulls/7/comments": [
            {"user": None, "created_at": "2026-10-11T09:00:30Z", "path": "brain/db.py", "body": "Тут нужен индекс"}],
    }


class GitHubCase(unittest.TestCase):
    """Общая обвязка: фейковый GitHub на свободном порту и конфиг, который ходит только в него."""

    def setUp(self):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), FakeGitHub)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        FakeGitHub.routes, FakeGitHub.hits = gh_discussion_routes(), []
        self.cfg = make_cfg(Path(tempfile.mkdtemp()))
        self.cfg["sources"]["github"].update(
            enabled=True, api_base=f"http://127.0.0.1:{self.server.server_port}",
            token_command=[sys.executable, "-c", "print('test-token')"])
        self.conn = db.connect(self.cfg["db_path"])

    def fetch(self, cursor=None):
        return list(GitHubConnector(self.cfg).fetch(cursor))

    def search_queries(self):
        return [p["q"][0] for path, p, _ in FakeGitHub.hits if path == "/search/issues" and "q" in p]


class GitHubDiscussionTests(GitHubCase):
    def test_discussions_become_records(self):
        batches = self.fetch()
        records = {r.external_id: r for b in batches for r in b.records}
        self.assertEqual(sorted(records), ["issue:vm/brain#5", "pr:vm/brain#7"])
        issue, pr = records["issue:vm/brain#5"], records["pr:vm/brain#7"]
        self.assertEqual((issue.kind, issue.project, issue.title),
                         ("discussion", "vm/brain", "vm/brain#5: Где хранить базу"))
        self.assertEqual((issue.started_at, issue.updated_at), ("2026-10-01T09:00:00Z", "2026-10-02T10:00:00Z"))
        self.assertEqual(issue.location, "https://github.com/vm/brain/issues/5")
        for text in ("Issue vm/brain#5: Где хранить базу", "Статус: закрыт", "Варианты: Postgres или SQLite",
                     "anna (2026-10-02): Давайте на SQLite"):
            self.assertIn(text, issue.body)
        for text in ("PR vm/brain#7: Поиск BM25", "Статус: влит", "anna (2026-10-11), ревью «нужны правки»: Нужны тесты",
                     "ghost (2026-10-11) к brain/db.py: Тут нужен индекс"):
            self.assertIn(text, pr.body)
        self.assertLess(pr.body.index("Нужны тесты"), pr.body.index("Тут нужен индекс"))  # по времени
        self.assertNotIn("ревью:", pr.body)  # пустое ревью-обёртка пропущено
        self.assertEqual(json.loads(batches[-1].cursor)["issues"], "2026-10-12T00:00:00Z")
        self.assertTrue(all(auth == "Bearer test-token" for _, _, auth in FakeGitHub.hits))
        [query] = self.search_queries()
        self.assertTrue(query.startswith("involves:vm updated:>="))

    def test_cursor_feeds_search_query(self):
        self.fetch(json.dumps({"issues": "2026-10-02T10:00:00Z", "repos": "2026-10-02T10:00:00Z"}))
        self.assertEqual(self.search_queries(), ["involves:vm updated:>=2026-10-02T10:00:00Z"])

    def test_excluded_repo_discussions_are_skipped(self):
        self.cfg["sources"]["github"]["exclude_repos"] = ["VM/br*"]
        batches = self.fetch()
        self.assertEqual([r for b in batches for r in b.records], [])
        self.assertNotIn("/repos/vm/brain/issues/5/comments", [p for p, _, _ in FakeGitHub.hits])
        self.assertEqual(json.loads(batches[-1].cursor)["issues"], "2026-10-12T00:00:00Z")  # курсор сохранён

    def test_missing_discussion_is_skipped(self):
        del FakeGitHub.routes["/repos/vm/brain/issues/5/comments"]  # 404: обсуждение или репозиторий пропали
        batches = self.fetch()
        self.assertEqual(sorted(r.external_id for b in batches for r in b.records), ["pr:vm/brain#7"])
        self.assertEqual(json.loads(batches[-1].cursor)["issues"], "2026-10-12T00:00:00Z")

    def test_no_access(self):
        FakeGitHub.routes["/user"] = 401
        with self.assertRaisesRegex(RuntimeError, "gh auth login"):
            self.fetch()
        self.cfg["sources"]["github"]["token_command"] = [sys.executable, "-c", "import sys; sys.exit(1)"]
        with self.assertRaisesRegex(RuntimeError, "gh auth login"):
            self.fetch()

    def test_rate_limit(self):
        FakeGitHub.routes["/search/issues"] = 429
        with self.assertRaisesRegex(RuntimeError, "исчерпан лимит"):
            self.fetch()


def gh_repo(name="vm/brain", **extra):
    repo = {"full_name": name, "fork": False, "private": True, "pushed_at": "2026-10-20T12:00:00Z",
            "created_at": "2025-01-01T00:00:00Z", "html_url": f"https://github.com/{name}",
            "description": "Второй мозг", "topics": ["sqlite", "mcp"]}
    repo.update(extra)
    return repo


def gh_commit(date, sha, message):
    return {"sha": sha + "0" * (40 - len(sha)), "commit": {"message": message, "committer": {"date": date}}}


def gh_repo_routes():
    readme = base64.b64encode("# Brain\n\nЗапуск: bin/brain sync".encode()).decode()
    return {
        "/user/repos": Pages([[gh_repo("vm/fork", fork=True), gh_repo()],
                              [gh_repo("vm/trade-bot", pushed_at="2026-10-21T00:00:00Z"),
                               gh_repo("vm/empty", pushed_at="2026-10-19T00:00:00Z")]]),
        "/repos/vm/brain/commits": [
            gh_commit("2026-10-05T10:00:00Z", "bbb2222", "Поиск BM25\n\nВеса колонок: карточка важнее текста"),
            gh_commit("2026-09-30T23:00:00Z", "aaa1111",
                      "Старт базы\n\nCo-Authored-By: Claude <noreply@anthropic.com>")],
        "/repos/vm/brain/readme": {"content": readme, "encoding": "base64"},
        "/repos/vm/trade-bot/commits": [gh_commit("2026-10-21T00:00:00Z", "ccc3333", "Сделка")],
        "/repos/vm/empty/commits": 409,
    }


class GitHubRepoTests(GitHubCase):
    def setUp(self):
        super().setUp()
        FakeGitHub.routes.update(gh_repo_routes())
        self.cfg["sources"]["github"]["exclude_repos"] = ["vm/trade-*"]

    def sync(self):
        return sync_mod.sync(self.cfg, self.conn, only="github", log=lambda *_: None)["github"]

    def test_commit_months_and_readme(self):
        batches = self.fetch()
        records = {r.external_id: r for b in batches for r in b.records}
        self.assertEqual(sorted(k for k in records if not k.startswith(("pr:", "issue:"))),
                         ["commits:vm/brain:2026-09", "commits:vm/brain:2026-10", "readme:vm/brain"])
        october = records["commits:vm/brain:2026-10"]
        self.assertEqual((october.kind, october.title, october.project),
                         ("changelog", "vm/brain — коммиты за октябрь 2026", "vm/brain"))
        self.assertEqual(october.body, "2026-10-05 bbb2222 Поиск BM25\nВеса колонок: карточка важнее текста")
        self.assertEqual(records["commits:vm/brain:2026-09"].body, "2026-09-30 aaa1111 Старт базы")
        readme = records["readme:vm/brain"]
        self.assertEqual((readme.kind, readme.updated_at), ("document", "2026-10-20T12:00:00Z"))
        for text in ("Репозиторий vm/brain (приватный)", "Второй мозг", "Темы: sqlite, mcp", "Запуск: bin/brain sync"):
            self.assertIn(text, readme.body)
        self.assertEqual(json.loads(batches[-1].cursor)["repos"], "2026-10-20T12:00:00Z")
        paths = [p for p, _, _ in FakeGitHub.hits]
        self.assertNotIn("/repos/vm/fork/commits", paths)
        self.assertNotIn("/repos/vm/trade-bot/commits", paths)

    def test_sync_is_incremental(self):
        first = self.sync()
        self.assertEqual((first["new"], first["errors"]), (5, 0))  # 2 обсуждения, 2 месяца, README
        again = self.sync()
        self.assertEqual((again["new"], again["changed"]), (0, 0))
        FakeGitHub.routes["/repos/vm/brain/commits"].insert(
            0, gh_commit("2026-10-25T08:00:00Z", "ddd4444", "Панель базы"))
        FakeGitHub.routes["/user/repos"][0][1]["pushed_at"] = "2026-10-25T08:00:00Z"
        FakeGitHub.hits.clear()
        third = self.sync()
        self.assertEqual((third["new"], third["changed"]), (0, 1))  # пересобран только октябрь
        [since] = [p["since"][0] for path, p, _ in FakeGitHub.hits if path == "/repos/vm/brain/commits"]
        self.assertEqual(since, "2026-09-01T00:00:00Z")  # месяц до курсора: конец месяца мог запушиться позже
        self.assertEqual([p["author"] for path, p, _ in FakeGitHub.hits if path == "/repos/vm/brain/commits"],
                         [["vm"]])
        body = self.conn.execute(
            "SELECT body FROM records WHERE external_id = 'commits:vm/brain:2026-10'").fetchone()[0]
        self.assertIn("ddd4444 Панель базы", body)

    def test_excluded_repo_records_are_purged(self):
        db.upsert(self.conn, Record("github", "readme:vm/trade-bot", "vm/trade-bot — README", "старое",
                                    "2026-10-01T00:00:00Z", project="vm/trade-bot", kind="document"))
        self.conn.commit()
        self.sync()
        self.assertIsNone(self.conn.execute("SELECT id FROM records WHERE project = 'vm/trade-bot'").fetchone())

    def test_failure_keeps_saved_discussions(self):
        FakeGitHub.routes["/repos/vm/brain/commits"] = 500
        totals = self.sync()
        self.assertEqual((totals["new"], totals["errors"]), (2, 1))
        cursor = json.loads(db.get_cursor(self.conn, "github"))
        self.assertEqual(cursor["issues"], "2026-10-12T00:00:00Z")
        self.assertEqual(cursor["repos"], "2026-10-19T00:00:00Z")  # vm/empty пройден, на vm/brain остановились
        error = self.conn.execute("SELECT last_error FROM sync_state WHERE source = 'github'").fetchone()[0]
        self.assertIn("HTTP 500", error)
        self.assertIn("vm/brain", error)  # в ошибке сказано, какой репозиторий мешает
        self.assertIn("exclude_repos", error)

    def test_discussion_failure_still_collects_repos(self):
        FakeGitHub.routes["/repos/vm/brain/pulls/7/reviews"] = 500  # PR #7 стабильно не читается
        totals = self.sync()
        # Коммиты и README забраны, ошибка обсуждения показана в конце; пачка с PR #7 и issue #5 не сохранена.
        self.assertEqual((totals["errors"], totals["new"]), (1, 3))
        cursor = json.loads(db.get_cursor(self.conn, "github"))
        self.assertEqual(cursor["repos"], "2026-10-20T12:00:00Z")
        self.assertNotIn("repos_since", cursor)
        self.assertLess(cursor["issues"], "2026-10-01T00:00:00Z")  # курсор не перепрыгнул несохранённые обсуждения
        error = self.conn.execute("SELECT last_error FROM sync_state WHERE source = 'github'").fetchone()[0]
        self.assertIn("vm/brain#7", error)
        self.assertIn("exclude_repos", error)

    def test_month_start(self):
        self.assertEqual(month_start("2026-10-20T12:00:00Z"), "2026-10-01T00:00:00Z")
        self.assertEqual(month_start("2026-10-20T12:00:00Z", back=1), "2026-09-01T00:00:00Z")
        self.assertEqual(month_start("2026-01-15T00:00:00Z", back=1), "2025-12-01T00:00:00Z")

    def test_interrupted_pass_keeps_month_floor(self):
        self.sync()  # полный прогон: курсор repos = 2026-10-20, граница месяца снята
        FakeGitHub.routes["/user/repos"] = [gh_repo("vm/aaa", pushed_at="2026-11-01T00:00:00Z"),
                                            gh_repo(pushed_at="2026-11-02T00:00:00Z")]
        FakeGitHub.routes["/repos/vm/aaa/commits"] = [gh_commit("2026-11-01T10:00:00Z", "eee5555", "Ноябрь")]
        FakeGitHub.routes["/repos/vm/brain/commits"] = 500  # прогон оборвётся на втором репозитории
        self.assertEqual(self.sync()["errors"], 1)
        cursor = json.loads(db.get_cursor(self.conn, "github"))
        self.assertEqual((cursor["repos"], cursor["repos_since"]),
                         ("2026-11-01T00:00:00Z", "2026-09-01T00:00:00Z"))
        FakeGitHub.routes["/repos/vm/brain/commits"] = gh_repo_routes()["/repos/vm/brain/commits"]
        FakeGitHub.hits.clear()
        self.assertEqual(self.sync()["errors"], 0)
        [since] = [p["since"][0] for path, p, _ in FakeGitHub.hits if path == "/repos/vm/brain/commits"]
        self.assertEqual(since, "2026-09-01T00:00:00Z")  # коммиты с границы месяца догнаны, а не потеряны
        self.assertNotIn("repos_since", json.loads(db.get_cursor(self.conn, "github")))

    def test_no_connection(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]  # порт свободен: на нём никто не слушает
        self.cfg["sources"]["github"]["api_base"] = f"http://127.0.0.1:{port}"
        with self.assertRaisesRegex(RuntimeError, "нет связи") as ctx:
            self.fetch()
        self.assertIsInstance(ctx.exception, Fatal)  # обрыв связи не вина репозитория: без подсказки про exclude_repos


class FakeLLM(http.server.BaseHTTPRequestHandler):
    requests = []

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLLM.requests.append((self.headers.get("Authorization"), payload))
        card = {"title": "Настройка поиска", "summary": "Выбрали SQLite FTS5 вместо векторов.",
                "questions": ["Почему не векторная база?"], "keywords": ["кракозябра", "FTS5"],
                "atoms": [{"kind": "decision", "text": "BM25 вместо эмбеддингов"}]}
        content = "```json\n" + json.dumps(card, ensure_ascii=False) + "\n```"
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class SummarizeTests(unittest.TestCase):
    def test_card_is_stored_and_searchable(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), FakeLLM)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            tmp = Path(tempfile.mkdtemp())
            cfg = make_cfg(tmp)
            cfg["llm"].update(base_url=f"http://127.0.0.1:{server.server_port}/v1", model="m", api_key="k")
            conn = db.connect(cfg["db_path"])
            db.upsert(conn, Record("codex", "1", "t", "обычный текст", "2026-10-20T00:00:00Z"))
            self.assertEqual(summarize.summarize_pending(conn, cfg, log=lambda *_: None), (1, 0))
            self.assertEqual(FakeLLM.requests[-1][0], "Bearer k")
            self.assertEqual(FakeLLM.requests[-1][1]["response_format"], {"type": "json_object"})
            [hit] = search_mod.search(conn, "кракозябра")
            self.assertEqual(hit["title"], "Настройка поиска")
            row = conn.execute("SELECT * FROM records").fetchone()
            self.assertEqual(row["atoms"], "[decision] BM25 вместо эмбеддингов")
            self.assertEqual(summarize.pending_count(conn), 0)
        finally:
            server.shutdown()
            server.server_close()

    def test_not_configured_is_skipped(self):
        cfg = make_cfg(Path(tempfile.mkdtemp()))
        conn = db.connect(cfg["db_path"])
        self.assertIsNone(summarize.summarize_pending(conn, cfg, log=lambda *_: None))

    def test_status_file_tracks_progress(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), FakeLLM)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            cfg = make_cfg(Path(tempfile.mkdtemp()))
            cfg["llm"].update(base_url=f"http://127.0.0.1:{server.server_port}/v1", model="m")
            conn = db.connect(cfg["db_path"])
            db.upsert(conn, Record("codex", "1", "первый", "текст", "2026-10-20T00:00:00Z"))
            db.upsert(conn, Record("codex", "2", "второй", "текст", "2026-10-21T00:00:00Z"))
            rep = status.Reporter(cfg, conn)
            seen = []

            def spy(done, total, current):
                rep.progress(done, total, current)
                seen.append(status.read(status.status_path(cfg))["progress"])

            summarize.summarize_pending(conn, cfg, log=lambda *_: None, on_progress=spy)
            rep.finish()
            self.assertEqual([(p["done"], p["total"], p["current"]) for p in seen], [(0, 2, "второй"), (1, 2, "первый")])
            final = status.read(status.status_path(cfg))
            self.assertEqual((final["state"], final["progress"], final["pending"], final["pid"]),
                             ("idle", None, 0, os.getpid()))
            self.assertEqual(final["sources"]["codex"], {"records": 2, "summarized": 2,
                                                         "latest": "2026-10-21T00:00:00Z"})
            self.assertEqual([r["title"] for r in final["recent"]], ["Настройка поиска"] * 2)
            self.assertEqual([m["source"] for m in final["sources_meta"]], list(connectors.source_names()))
        finally:
            server.shutdown()
            server.server_close()

    def test_prompt_follows_kind_and_short_records_skip_model(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), FakeLLM)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            cfg = make_cfg(Path(tempfile.mkdtemp()))
            cfg["llm"].update(base_url=f"http://127.0.0.1:{server.server_port}/v1", model="m", min_chars=300)
            conn = db.connect(cfg["db_path"])
            db.upsert(conn, Record("github", "pr:vm/b#7", "vm/b#7: Поиск", "Обсуждение поиска. " * 30,
                                   "2026-10-20T00:00:00Z", kind="discussion"))
            db.upsert(conn, Record("github", "pr:vm/b#8", "vm/b#8: Пусто", "PR без описания",
                                   "2026-10-21T00:00:00Z", kind="discussion"))
            before = len(FakeLLM.requests)
            self.assertEqual(summarize.summarize_pending(conn, cfg, log=lambda *_: None), (1, 0))
            self.assertEqual(len(FakeLLM.requests) - before, 1)
            system, user = [m["content"] for m in FakeLLM.requests[-1][1]["messages"]]
            self.assertIn("pull request", system)
            self.assertIn("Обсуждение:\n", user)
            self.assertEqual(summarize.pending_count(conn), 0)
            short = conn.execute("SELECT summary, summarized_at FROM records WHERE external_id = 'pr:vm/b#8'").fetchone()
            self.assertEqual((short["summary"], short["summarized_at"]), (None, None))
            self.assertTrue(search_mod.search(conn, "описания"))
        finally:
            server.shutdown()
            server.server_close()

    def test_source_with_summarize_off_skips_model(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), FakeLLM)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            cfg = make_cfg(Path(tempfile.mkdtemp()))
            cfg["llm"].update(base_url=f"http://127.0.0.1:{server.server_port}/v1", model="m")
            cfg["sources"]["chatgpt_export"]["summarize"] = False  # ключ настроек ≠ ключу записей «chatgpt»
            conn = db.connect(cfg["db_path"])
            db.upsert(conn, Record("chatgpt", "c1", "Чат", "Длинный чат про отпуск. " * 20, "2026-10-20T00:00:00Z"))
            db.upsert(conn, Record("codex", "x1", "Код", "Длинный разговор про код. " * 20, "2026-10-21T00:00:00Z"))
            before = len(FakeLLM.requests)
            self.assertEqual(summarize.summarize_pending(conn, cfg, log=lambda *_: None), (1, 0))
            self.assertEqual(len(FakeLLM.requests) - before, 1)
            chat = conn.execute("SELECT summary, summarized_at FROM records WHERE source = 'chatgpt'").fetchone()
            self.assertEqual((chat["summary"], chat["summarized_at"]), (None, None))
            self.assertEqual(summarize.pending_count(conn), 0)
        finally:
            server.shutdown()
            server.server_close()

    def test_every_kind_has_prompt(self):
        self.assertEqual(sorted(summarize.KINDS), ["changelog", "dialog", "discussion", "document", "email"])
        for kind, spec in summarize.KINDS.items():
            prompt = summarize.system_prompt(kind)
            self.assertIn(spec["what"], prompt)
            self.assertIn('"title"', prompt)
        self.assertEqual(summarize.system_prompt("неизвестный"), summarize.system_prompt("dialog"))


class ProjectsTests(unittest.TestCase):
    def setUp(self):
        from brain import projects
        self.projects = projects
        self.conn = db.connect(":memory:")
        seed_context(self.conn)

    def test_listing_merges_folder_and_repo(self):
        items = self.projects.listing(self.conn)
        self.assertEqual([(p["key"], p["count"], p["latest"]) for p in items],
                         [("", 1, "2026-11-11T11:00:00Z"), ("club-landing", 3, "2026-11-11T10:00:00Z"),
                          ("club-bot", 2, "2026-11-11T08:00:00Z")])
        landing = items[1]
        self.assertEqual(landing["name"], "club-landing")
        self.assertEqual(landing["paths"], ["vladilenm/club_landing", "~/p/club-landing"])

    def test_find_by_key_path_and_substring(self):
        for name in ("club-landing", "club_landing", "Club Landing", "~/p/club-landing", "vladilenm/club_landing", "landing"):
            self.assertEqual(self.projects.find(self.conn, name)["key"], "club-landing", name)
        self.assertEqual(self.projects.find(self.conn, "bot")["key"], "club-bot")

    def test_find_reports_candidates_and_misses(self):
        with self.assertRaises(self.projects.ProjectError) as several:
            self.projects.find(self.conn, "club")
        self.assertIn("несколько", str(several.exception))
        self.assertIn("club-landing", str(several.exception))
        self.assertIn("club-bot", str(several.exception))
        with self.assertRaises(self.projects.ProjectError) as missing:
            self.projects.find(self.conn, "нет такого")
        self.assertIn("не найден", str(missing.exception))
        with self.assertRaises(self.projects.ProjectError):
            self.projects.find(self.conn, "  ")

    def test_dashboard_uses_the_same_key(self):
        from brain import dashboard
        self.assertIs(dashboard.project_key, self.projects.project_key)


class RecentTests(unittest.TestCase):
    def setUp(self):
        from brain import context
        self.context = context
        self.conn = db.connect(":memory:")
        seed_context(self.conn)

    def test_groups_by_project_with_main_knowledge_first(self):
        text = self.context.recent(self.conn, days=3, now=NOW)
        self.assertTrue(text.startswith("Что нового за 3 дня: 5 записей, 2 проекта\n\n"), text)
        self.assertLess(text.index("## club-landing"), text.index("## club-bot"))
        self.assertLess(text.index("## club-bot"), text.index("## Без проекта"))
        self.assertIn("## club-landing — 3 записи · 6 решений · 1 задача · 1 ошибка\n- решение: Решение 1 [id 1]", text)
        self.assertEqual(text.count("- решение: Решение "), 5)
        self.assertIn("…ещё 3 знания, команды и факты: 1", text)
        self.assertIn("Без знаний: «PR без знаний» [id 3]", text)
        self.assertIn("## club-bot — 1 запись · 1 исправление\n- исправление: Починили вебхук [id 4]", text)
        self.assertIn("## Без проекта — 1 запись · 1 решение\n- решение: Цена обсуждается позже [id 5]", text)
        self.assertNotIn("Старое решение", text)

    def test_project_filter_gives_everything(self):
        text = self.context.recent(self.conn, days=3, project="club_landing", now=NOW)
        self.assertTrue(text.startswith("Что нового за 3 дня в проекте club-landing: 3 записи\n\n"), text)
        self.assertEqual(text.count("- решение: Решение "), 6)
        self.assertIn("- задача: Проверить шрифт [id 2, продолжение]", text)
        self.assertIn("- команда: npm run build [id 1]", text)
        self.assertNotIn("club-bot", text)
        self.assertNotIn("…ещё", text)

    def test_source_filter(self):
        text = self.context.recent(self.conn, days=3, source="codex", now=NOW)
        self.assertIn("Проверить шрифт", text)
        self.assertIn("Починили вебхук", text)
        self.assertNotIn("Цена обсуждается", text)
        self.assertNotIn("Решение 1", text)

    def test_empty_period_and_bad_days(self):
        later = NOW + timedelta(days=40)
        self.assertEqual(self.context.recent(self.conn, days=1, now=later),
                         "За 1 день записей не было. Последняя запись в базе — 2026-11-11.")
        for bad in (0, 31, "много"):
            with self.assertRaises(ValueError):
                self.context.recent(self.conn, days=bad, now=NOW)
        from brain import projects
        with self.assertRaises(projects.ProjectError):
            self.context.recent(self.conn, days=3, project="нет такого", now=NOW)

    def test_shows_ten_projects_and_counts_the_rest(self):
        conn = db.connect(":memory:")
        for i in range(12):
            seed(conn, "codex", f"p{i}", f"Запись {i}", "2026-11-11T10:00:00Z", project=f"~/p/proj-{i:02d}",
                 atoms=["[decision] Решение"] * (12 - i))
        seed(conn, "notion", "n", "Без проекта", "2026-11-11T10:00:00Z", atoms=["[fact] Факт"])
        text = self.context.recent(conn, days=3, now=NOW)
        self.assertEqual(text.count("\n## proj-"), 10)
        self.assertIn("## Без проекта", text)
        self.assertTrue(text.endswith("…ещё 2 проекта: proj-10 (1 запись), proj-11 (1 запись). Сузь параметром project."), text)


class ProjectCardTests(unittest.TestCase):
    def setUp(self):
        from brain import context
        self.context = context
        self.conn = db.connect(":memory:")
        seed_context(self.conn)

    def test_list_without_name(self):
        text = self.context.project(self.conn)
        self.assertEqual(text, "Проектов: 2, записей без проекта: 1\n\n"
                               "club-landing — 3 записи, последняя 2026-11-11\n"
                               "club-bot — 2 записи, последняя 2026-11-11")

    def test_list_is_capped(self):
        conn = db.connect(":memory:")
        for i in range(53):
            seed(conn, "codex", f"p{i}", "Запись", f"2026-11-{i % 9 + 1:02d}T10:00:00Z", project=f"~/p/proj-{i:02d}")
        text = self.context.project(conn)
        self.assertTrue(text.startswith("Проектов: 53, записей без проекта: 0\n\n"))
        self.assertEqual(text.count(" — 1 запись, последняя "), 50)
        self.assertTrue(text.endswith("\n…ещё 3 проекта"))

    def test_card(self):
        text = self.context.project(self.conn, "club_landing")
        self.assertTrue(text.startswith(
            "Проект club-landing\n"
            "Пути: vladilenm/club_landing; ~/p/club-landing\n"
            "Записей: 3 (Claude Code 1, Codex 1, GitHub 1) · 2026-10-01 — 2026-11-11\n\n"
            "Последние записи:\n"
            "[id 1] Лендинг клуба · Claude Code · 2026-11-11\n"
            "  Обсудили лендинг\n"
            "[id 2] Шрифты лендинга · Codex · 2026-11-10\n"
            "[id 3] PR без знаний · GitHub · 2026-11-09\n\n"
            "Последние решения:\n"
            "- Решение 1 [id 1, 2026-11-11]\n"), text)
        self.assertEqual(text.count("- Решение "), 6)
        self.assertTrue(text.endswith("Последние задачи (статус выполнения в базе не хранится):\n"
                                      "- Проверить шрифт [id 2, 2026-11-10]"), text)

    def test_card_skips_empty_sections_and_reports_unknown(self):
        text = self.context.project(self.conn, "club-bot")
        self.assertIn("Последние решения:\n- Старое решение [id 6, 2026-10-01]", text)
        self.assertNotIn("Последние задачи", text)
        from brain import projects
        with self.assertRaises(projects.ProjectError):
            self.context.project(self.conn, "нет такого")


class SearchFilterTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        seed(self.conn, "codex", "s1", "Вебхук лендинга", "2026-11-10T10:00:00Z", project="~/p/club-landing")
        seed(self.conn, "codex", "s2", "Вебхук бота", "2026-11-11T10:00:00Z", project="~/dev/club-bot")
        seed(self.conn, "codex", "s3", "Вебхук старый", "2026-06-01T10:00:00Z", project="~/dev/club-bot")
        seed(self.conn, "notion", "s4", "Вебхук в заметке", "2026-11-11T09:00:00Z")

    def ids(self, **kw):
        return sorted(r["id"] for r in search_mod.search(self.conn, "вебхук", **kw))

    def test_filters_narrow_before_ranking(self):
        self.assertEqual(self.ids(), [1, 2, 3, 4])
        self.assertEqual(self.ids(project_paths=["~/dev/club-bot"]), [2, 3])
        self.assertEqual(self.ids(days=30, now=NOW), [1, 2, 4])
        self.assertEqual(self.ids(project_paths=["~/dev/club-bot"], days=30, now=NOW), [2])
        self.assertEqual(self.ids(project_paths=[]), [])
        self.assertEqual(self.ids(source="notion", days=30, now=NOW), [4])

    def test_filters_do_not_change_ranking(self):
        base = search_mod.search(self.conn, "вебхук")
        wide = search_mod.search(self.conn, "вебхук", days=100000,
                                 project_paths=["~/p/club-landing", "~/dev/club-bot"])
        # Оценка нормируется на лучший результат выдачи, поэтому сравниваем порядок, а не числа.
        self.assertEqual([r["id"] for r in wide], [r["id"] for r in base if r["project"]])


class HealthTests(unittest.TestCase):
    def setUp(self):
        from brain import health
        self.health = health
        self.conn = db.connect(":memory:")

    def fill(self, source, days_ago):
        for i, d in enumerate(days_ago):
            stamp = (NOW - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")
            seed(self.conn, source, f"{source}{i}", "Запись", stamp)
        # ingested_at ставит upsert «сейчас»: в тесте выравниваем его по дате записи.
        self.conn.execute("UPDATE records SET ingested_at = updated_at WHERE source = ?", (source,))

    def by_source(self):
        return {s["source"]: s for s in self.health.sources(self.conn, now=NOW)}

    def test_daily_source_gone_quiet(self):
        self.fill("codex", range(10, 30))          # каждый день, потом 10 дней тишины
        got = self.by_source()["codex"]
        self.assertEqual((got["silent_days"], got["usual_days"], got["quiet"]), (10, 1, True))
        self.assertEqual([s["source"] for s in self.health.quiet(self.conn, now=NOW)], ["codex"])

    def test_rare_source_is_not_alarming(self):
        self.fill("gmail", [12, 30, 50, 71, 90, 110, 131])   # раз в три недели, сейчас 12 дней тишины
        got = self.by_source()["gmail"]
        self.assertEqual((got["silent_days"], got["usual_days"], got["quiet"]), (12, 21, False))

    def test_short_silence_and_short_history_give_no_verdict(self):
        self.fill("codex", range(2, 30))           # два дня тишины — ещё рано
        self.fill("kimi", [10, 11, 12])            # три активных дня — ритм непонятен
        got = self.by_source()
        self.assertFalse(got["codex"]["quiet"])
        self.assertEqual((got["kimi"]["usual_days"], got["kimi"]["quiet"]), (None, False))

    def test_status_and_dashboard_carry_the_verdict(self):
        from brain import dashboard
        self.fill("codex", range(10, 30))
        # Здесь время настоящее, а не NOW: тишина с тех пор только выросла.
        self.assertEqual([q["source"] for q in status.snapshot(self.conn)["quiet"]], ["codex"])
        data = dashboard.collect(self.conn, make_cfg(Path(tempfile.mkdtemp())))
        self.assertTrue(data["silence"]["codex"]["quiet"])


class FakeYouTube(http.server.BaseHTTPRequestHandler):
    """Фейковый YouTube Data API: ответ выбирает функция по пути и параметрам; (код, причина) — ошибка."""
    answer = None
    hits: list = []  # (путь, параметры, ключ из заголовка)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
        FakeYouTube.hits.append((path, params, self.headers.get("X-Goog-Api-Key")))
        result = FakeYouTube.answer(path, params)
        code = 200
        if isinstance(result, tuple):
            code, reason = result
            result = {"error": {"code": code, "errors": [{"reason": reason}]}}
        body = json.dumps(result).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def yt_video(vid, published, views="12345", likes="540", comments="87", title=None):
    return {"id": vid, "snippet": {"title": title or f"Видео {vid}", "description": f"Описание {vid}",
                                   "publishedAt": published, "channelTitle": "Канал"},
            "statistics": {"viewCount": views, "likeCount": likes, "commentCount": comments},
            "contentDetails": {"duration": "PT18M42S"}}


def yt_thread(text, likes=0, author="viewer", replies=()):
    return {"snippet": {"topLevelComment": {"snippet": {"textDisplay": text, "likeCount": likes,
                                                         "authorChannelId": {"value": author}}}},
            "replies": {"comments": [{"snippet": {"textDisplay": t, "authorChannelId": {"value": a}}} for t, a in replies]}}


class YouTubeTests(unittest.TestCase):
    OWNER = "UC" + "a" * 22

    def setUp(self):
        from brain.connectors.youtube import YouTubeConnector
        self.server = http.server.HTTPServer(("127.0.0.1", 0), FakeYouTube)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        FakeYouTube.hits = []
        self.videos = {"new": yt_video("new", "2026-11-10T09:00:00Z"), "mid": yt_video("mid", "2026-10-01T09:00:00Z"),
                       "old": yt_video("old", "2025-01-01T09:00:00Z")}
        self.threads = {"new": [yt_thread("Как подключить MCP?", 41, replies=[("Смотри описание", self.OWNER), ("@some.viewer-1 Спасибо!", "v2")]),
                                yt_thread("Отличный разбор")]}
        self.disabled, self.quota_on = {"old"}, set()
        FakeYouTube.answer = self.route
        self.cfg = make_cfg(Path(tempfile.mkdtemp()))
        self.cfg["sources"]["youtube"].update(
            enabled=True, channels=["@mine"], api_base=f"http://127.0.0.1:{self.server.server_port}",
            key_command=[sys.executable, "-c", "print('test-key')"])
        self.connector = YouTubeConnector
        self.conn = db.connect(self.cfg["db_path"])

    def route(self, path, params):
        if path == "/channels":
            if params.get("forHandle") != "@mine":
                return {"items": []}
            return {"items": [{"id": self.OWNER, "snippet": {"title": "Мой канал"},
                               "contentDetails": {"relatedPlaylists": {"uploads": "UUmine"}}}]}
        if path == "/playlistItems":
            ids = list(self.videos)
            if params.get("pageToken") == "p2":
                return {"items": [{"contentDetails": {"videoId": i}} for i in ids[2:]]}
            return {"items": [{"contentDetails": {"videoId": i}} for i in ids[:2]], "nextPageToken": "p2"}
        if path == "/videos":
            return {"items": [self.videos[i] for i in params["id"].split(",")]}
        if path == "/commentThreads":
            vid = params["videoId"]
            if vid in self.quota_on:
                return (403, "quotaExceeded")
            if vid in self.disabled:
                return (403, "commentsDisabled")
            return {"items": self.threads.get(vid, [])}
        return (404, "notFound")

    def fetch(self, cursor=None, now=NOW, **settings):
        self.cfg["sources"]["youtube"].update(settings)
        connector = self.connector(self.cfg)
        connector.now = now
        return list(connector.fetch(cursor))

    def test_record_text_and_key_in_header(self):
        [batch] = self.fetch()
        by_id = {r.external_id: r for r in batch.records}
        self.assertEqual([r.external_id for r in batch.records], ["new", "mid", "old"])  # от новых к старым
        new = by_id["new"]
        self.assertEqual((new.source, new.kind, new.title, new.project), ("youtube", "discussion", "Видео new", None))
        self.assertEqual((new.updated_at, new.started_at), ("2026-11-10T09:00:00Z", "2026-11-10T09:00:00Z"))
        self.assertEqual(new.location, "https://www.youtube.com/watch?v=new")
        self.assertEqual(new.body, "Канал: Мой канал\n"
                                   "Опубликовано: 2026-11-10 · 18:42\n"
                                   "Статистика: около 12 тыс. просмотров · около 540 лайков · 87 комментариев\n"
                                   "\nОписание:\nОписание new\n"
                                   "\nКомментарии зрителей (сначала самые заметные):\n"
                                   "— Как подключить MCP? (лайков: 41)\n"
                                   "  ↳ автор канала: Смотри описание\n"
                                   "  ↳ Спасибо!\n"
                                   "— Отличный разбор")
        self.assertTrue(by_id["old"].body.endswith("Комментарии под видео отключены."))
        from brain.connectors.youtube import counted
        likes = ("лайк", "лайка", "лайков")
        self.assertEqual([counted(n, likes) for n in (1, 3, 11, 63, 4321)],
                         ["1 лайк", "3 лайка", "11 лайков", "63 лайка", "около 4,3 тыс. лайков"])
        self.assertNotIn("Комментарии", by_id["mid"].body)
        self.assertNotIn("viewer", new.body)  # имён зрителей в записи нет
        self.assertTrue(all(key == "test-key" for _, _, key in FakeYouTube.hits))
        self.assertTrue(all("key" not in params for _, params, _ in FakeYouTube.hits))
        self.assertEqual(sorted(json.loads(batch.cursor)["videos"]), ["mid", "new", "old"])

    def test_small_stat_change_keeps_the_record(self):
        [first] = self.fetch()
        self.videos["new"] = yt_video("new", "2026-11-10T09:00:00Z", views="12399", likes="544")
        [second] = self.fetch()
        self.assertEqual(first.records[0].body, second.records[0].body)

    def test_refresh_schedule_by_age(self):
        [batch] = self.fetch()
        ids = lambda days: [r.external_id for b in self.fetch(batch.cursor, now=NOW + timedelta(days=days)) for r in b.records]
        self.assertEqual(ids(0.5), [])
        self.assertEqual(ids(1), ["new"])            # свежее видео — раз в сутки
        self.assertEqual(ids(7), ["new", "mid"])     # до 90 дней — раз в неделю
        self.assertEqual(sorted(ids(90)), ["mid", "new", "old"])

    def test_limit_per_run_continues_from_cursor(self):
        [first] = self.fetch(max_videos_per_run=2)
        self.assertEqual([r.external_id for r in first.records], ["new", "mid"])
        [second] = self.fetch(first.cursor, max_videos_per_run=2)
        self.assertEqual([r.external_id for r in second.records], ["old"])

    def test_quota_keeps_what_is_done(self):
        from brain.connectors.youtube import Fatal
        self.quota_on = {"mid"}
        got = []
        with self.assertRaises(Fatal) as err:
            for batch in self.connector(self.cfg).fetch(None):
                got.append(batch)
        self.assertIn("квота", str(err.exception))
        self.assertEqual([r.external_id for r in got[0].records], ["new"])
        self.assertEqual(list(json.loads(got[0].cursor)["videos"]), ["new"])

    def test_missing_channel_key_and_channels(self):
        batches = []
        with self.assertRaises(RuntimeError) as err:
            self.cfg["sources"]["youtube"]["channels"] = ["@mine", "@nobody"]
            for batch in self.connector(self.cfg).fetch(None):
                batches.append(batch)
        self.assertIn("канал «@nobody» не найден", str(err.exception))
        self.assertEqual(len(batches[0].records), 3)  # найденный канал обработан
        self.cfg["sources"]["youtube"]["channels"] = []
        with self.assertRaises(RuntimeError) as err:
            list(self.connector(self.cfg).fetch(None))
        self.assertIn("не указаны каналы", str(err.exception))
        self.cfg["sources"]["youtube"].update(channels=["@mine"], key_command=[sys.executable, "-c", "raise SystemExit(1)"])
        with self.assertRaises(RuntimeError) as err:
            list(self.connector(self.cfg).fetch(None))
        self.assertIn("security add-generic-password -s brain-youtube -a default -w", str(err.exception))

    def test_channel_spec_and_sync(self):
        from brain.connectors.youtube import channel_query
        self.assertEqual(channel_query("https://www.youtube.com/@mine/videos"), {"forHandle": "@mine"})
        self.assertEqual(channel_query(f"https://youtube.com/channel/{self.OWNER}"), {"id": self.OWNER})
        with self.assertRaises(RuntimeError):
            channel_query("просто слова")
        totals = sync_mod.sync(self.cfg, self.conn, only="youtube", log=lambda *_: None)
        self.assertEqual((totals["youtube"]["new"], totals["youtube"]["errors"]), (3, 0))
        [hit] = search_mod.search(self.conn, "подключить MCP")
        self.assertEqual((hit["source"], hit["title"]), ("youtube", "Видео new"))
        totals = sync_mod.sync(self.cfg, self.conn, only="youtube", log=lambda *_: None)
        self.assertEqual((totals["youtube"]["new"], totals["youtube"]["changed"]), (0, 0))


class MCPTests(unittest.TestCase):
    def test_stdio_roundtrip(self):
        tmp = Path(tempfile.mkdtemp())
        dbp = tmp / "brain.db"
        conn = db.connect(str(dbp))
        db.upsert(conn, Record("claude_code", "s1", "Второй мозг", "Пользователь: настроим поиск BM25",
                               "2026-10-27T00:00:00Z", project="~/brain"))
        conn.commit()
        conn.close()
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search", "arguments": {"query": "поиск"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "get_record", "arguments": {"id": 1}}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
             "params": {"name": "project", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 5, "method": "nope"},
        ]
        env = dict(os.environ, BRAIN_DB=str(dbp), BRAIN_CONFIG=str(tmp / "none.json"), PYTHONPATH=str(ROOT))
        proc = subprocess.run([sys.executable, "-m", "brain.cli", "mcp"], env=env, timeout=30,
                              input="\n".join(json.dumps(m) for m in msgs).encode(), capture_output=True)
        replies = {r["id"]: r for r in map(json.loads, proc.stdout.decode().splitlines())}
        self.assertEqual(sorted(replies), [1, 2, 3, 4, 5, 6])
        self.assertEqual(replies[1]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual([t["name"] for t in replies[2]["result"]["tools"]], ["search", "get_record", "recent", "project"])
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in replies[2]["result"]["tools"]))
        self.assertIn("Второй мозг", replies[3]["result"]["content"][0]["text"])
        self.assertIn("настроим поиск BM25", replies[4]["result"]["content"][0]["text"])
        self.assertEqual(replies[5]["error"]["code"], -32601)
        self.assertEqual(replies[6]["result"]["content"][0]["text"],
                         "Проектов: 1, записей без проекта: 0\n\nbrain — 1 запись, последняя 2026-10-27")

    def make_db(self):
        from brain.timeutil import now_iso
        cfg = make_cfg(Path(tempfile.mkdtemp()))
        conn = db.connect(cfg["db_path"])
        seed(conn, "codex", "m1", "Вебхук бота", now_iso(), project="~/dev/club-bot", atoms=["[fix] Починили вебхук"])
        seed(conn, "codex", "m2", "Вебхук лендинга", now_iso(), project="~/p/club-landing")
        conn.commit()
        conn.close()
        return cfg

    def test_context_tools(self):
        from brain import mcp_server
        cfg = self.make_db()
        text = lambda name, args: mcp_server.call_tool(name, args, cfg)["content"][0]["text"]
        self.assertTrue(text("recent", {}).startswith("Что нового за 3 дня: 2 записи, 2 проекта"))
        self.assertIn("в проекте club-bot: 1 запись", text("recent", {"days": 7, "project": "bot"}))
        self.assertTrue(text("project", {"name": "club_bot"}).startswith("Проект club-bot\n"))
        self.assertIn("Вебхук бота", text("search", {"query": "вебхук", "project": "bot", "days": 7}))
        self.assertNotIn("Вебхук лендинга", text("search", {"query": "вебхук", "project": "bot"}))

    def test_context_tools_report_bad_arguments(self):
        from brain import mcp_server
        cfg = self.make_db()
        for name, args, needle in (("recent", {"days": 99}, "days: допустимо от 1 до 30"),
                                   ("recent", {"project": "нет такого"}, "не найден"),
                                   ("project", {"name": "club"}, "несколько проектов"),
                                   ("search", {"query": "вебхук", "project": "club"}, "несколько проектов"),
                                   ("search", {"query": "вебхук", "days": 0}, "days: допустимо от 1")):
            reply = mcp_server.call_tool(name, args, cfg)
            self.assertTrue(reply["isError"], (name, args))
            self.assertIn(needle, reply["content"][0]["text"])

    def test_cli_prints_the_same_text(self):
        cfg = self.make_db()
        env = dict(os.environ, BRAIN_DB=cfg["db_path"], BRAIN_CONFIG=str(Path(cfg["db_path"]).parent / "none.json"),
                   PYTHONPATH=str(ROOT))
        run = lambda *args: subprocess.run([sys.executable, "-m", "brain.cli", *args], env=env, timeout=30,
                                           capture_output=True)
        self.assertTrue(run("recent").stdout.decode().startswith("Что нового за 3 дня: 2 записи, 2 проекта"))
        self.assertTrue(run("project", "bot").stdout.decode().startswith("Проект club-bot\n"))
        self.assertIn("Вебхук бота", run("search", "вебхук", "--project", "bot", "--days", "7").stdout.decode())
        missing = run("project", "нет такого")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("не найден", missing.stdout.decode())

    def test_missing_db_is_not_created(self):
        from brain import mcp_server
        cfg = make_cfg(Path(tempfile.mkdtemp()))
        cfg["db_path"] = str(Path(cfg["db_path"]).parent / "moved" / "brain.db")
        reply = mcp_server.call_tool("search", {"query": "поиск"}, cfg)
        self.assertTrue(reply["isError"])
        self.assertFalse(Path(cfg["db_path"]).parent.exists())


if __name__ == "__main__":
    unittest.main()
