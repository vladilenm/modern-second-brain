"""Тесты: python3 -m unittest discover -s tests (из корня проекта)."""
from __future__ import annotations

import copy
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brain import config, db, status, summarize  # noqa: E402
from brain import search as search_mod  # noqa: E402
from brain import sync as sync_mod  # noqa: E402
from brain.connectors.base import Record  # noqa: E402
from brain.connectors.chatgpt_export import ChatGPTExportConnector  # noqa: E402
from brain.connectors.claude_code import ClaudeCodeConnector  # noqa: E402
from brain.connectors.codex import CodexConnector  # noqa: E402


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
    return cfg


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
        finally:
            server.shutdown()
            server.server_close()


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
            {"jsonrpc": "2.0", "id": 5, "method": "nope"},
        ]
        env = dict(os.environ, BRAIN_DB=str(dbp), BRAIN_CONFIG=str(tmp / "none.json"), PYTHONPATH=str(ROOT))
        proc = subprocess.run([sys.executable, "-m", "brain.cli", "mcp"], env=env, timeout=30,
                              input="\n".join(json.dumps(m) for m in msgs).encode(), capture_output=True)
        replies = {r["id"]: r for r in map(json.loads, proc.stdout.decode().splitlines())}
        self.assertEqual(sorted(replies), [1, 2, 3, 4, 5])
        self.assertEqual(replies[1]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual([t["name"] for t in replies[2]["result"]["tools"]], ["search", "get_record"])
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in replies[2]["result"]["tools"]))
        self.assertIn("Второй мозг", replies[3]["result"]["content"][0]["text"])
        self.assertIn("настроим поиск BM25", replies[4]["result"]["content"][0]["text"])
        self.assertEqual(replies[5]["error"]["code"], -32601)

    def test_missing_db_is_not_created(self):
        from brain import mcp_server
        cfg = make_cfg(Path(tempfile.mkdtemp()))
        cfg["db_path"] = str(Path(cfg["db_path"]).parent / "moved" / "brain.db")
        reply = mcp_server.call_tool("search", {"query": "поиск"}, cfg)
        self.assertTrue(reply["isError"])
        self.assertFalse(Path(cfg["db_path"]).parent.exists())


if __name__ == "__main__":
    unittest.main()
