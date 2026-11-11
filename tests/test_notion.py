"""Notion: настоящий HTTP-клиент, временный сервер и SQLite, без доступа к аккаунту."""
import copy
import http.server
import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from brain import config, connectors, db, search, summarize, sync
from brain.redact import MASK, redact

if importlib.util.find_spec("brain.connectors.notion"):
    from brain.connectors import notion
else:
    notion = None

P1 = "11111111-1111-4111-8111-111111111111"
P2 = "22222222-2222-4222-8222-222222222222"
P3 = "33333333-3333-4333-8333-333333333333"
D1 = "44444444-4444-4444-8444-444444444444"
DS = "55555555-5555-4555-8555-555555555555"
B1 = "66666666-6666-4666-8666-666666666666"
TOKEN = "ntn_" + "x" * 42


class NotionSecretTests(unittest.TestCase):
    def test_notion_tokens_are_hidden_without_a_label(self):
        self.assertEqual(redact("Ключи " + TOKEN + " secret_" + "a" * 43), "Ключи " + MASK + " " + MASK)

    def test_text_and_boolean_formula_values(self):
        self.assertEqual(notion.property_text({"type": "formula", "formula": {"type": "string", "string": "Проект"}}), "Проект")
        self.assertEqual(notion.property_text({"type": "formula", "formula": {"type": "boolean", "boolean": False}}), "нет")

    def test_rollup_singleton_text_values(self):
        prop = {"type": "rollup", "rollup": {"type": "array", "array": [
            {"type": "title", "title": {"plain_text": "Проект"}},
            {"type": "people", "people": {"name": "Владилен"}}]}}
        self.assertEqual(notion.property_text(prop), "Проект; Владилен")


def page(pid=P1, title="Поиск по заметкам", edited="2026-10-31T10:00:00.000Z"):
    return {"object": "page", "id": pid, "url": "https://www.notion.so/" + pid,
            "created_time": "2024-01-01T00:00:00.000Z", "last_edited_time": edited,
            "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]}}}


def listed(items, cursor=None):
    return {"object": "list", "results": items, "has_more": bool(cursor), "next_cursor": cursor}


class NotionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(notion, "Нужен коннектор brain.connectors.notion")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.cfg["db_path"] = str(Path(self.tmp.name) / "brain.db")
        self.routes = {}
        self.calls = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                parsed = urllib.parse.urlsplit(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                payload = json.loads(body) if body else {}
                owner.calls.append((self.command, parsed.path, payload, dict(self.headers)))
                route = owner.routes.get((self.command, parsed.path))
                if callable(route):
                    route = route(payload, urllib.parse.parse_qs(parsed.query))
                if route is None:
                    route = (404, {}, {"code": "object_not_found"})
                code, headers, result = route if isinstance(route, tuple) else (200, {}, route)
                raw = json.dumps(result).encode()
                self.send_response(code)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(self.server.shutdown)
        settings = self.cfg["sources"].setdefault("notion", {})
        settings.update(enabled=True, api_base="http://127.0.0.1:%s/v1" % self.server.server_port)
        self.conn = db.connect(self.cfg["db_path"])
        self.addCleanup(self.conn.close)
        patcher = mock.patch.dict(connectors.ALL, {"notion": notion.NotionConnector})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch("brain.keychain.password", return_value=TOKEN)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(notion, "REQUEST_INTERVAL", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.routes["POST", "/v1/search"] = listed([page()])
        self.install_page(page(), "Текст заметки: полнотекстовый поиск работает.")

    def install_page(self, item, text):
        pid = item["id"]
        self.routes["GET", "/v1/pages/" + pid] = item
        self.routes["GET", "/v1/pages/" + pid + "/markdown"] = {
            "object": "page_markdown", "id": pid, "markdown": text,
            "truncated": False, "unknown_block_ids": []}
        self.routes["GET", "/v1/blocks/" + pid + "/children"] = listed([])

    def run_sync(self):
        return sync.sync(self.cfg, self.conn, only="notion", log=lambda _: None)["notion"]

    def error(self):
        return self.conn.execute("SELECT last_error FROM sync_state WHERE source='notion'").fetchone()[0]

    def test_import_record_properties_links_and_search(self):
        self.routes["POST", "/v1/search"] = listed([dict(page(), properties={
            **page()["properties"], "Status": {"type": "status", "status": {"name": "В работе"}},
            "Tags": {"type": "multi_select", "multi_select": [{"name": "Знания"}]}})])
        self.assertEqual(self.run_sync()["new"], 1)
        row = self.conn.execute("SELECT * FROM records").fetchone()
        self.assertEqual((row["source"], row["kind"], row["external_id"]), ("notion", "document", P1))
        self.assertIn("полнотекстовый поиск", row["body"])
        self.assertIn("В работе", row["body"])
        self.assertIn("Знания", row["body"])
        self.assertEqual(row["location"], page()["url"])
        self.assertTrue(search.search(self.conn, "полнотекстовый", source="notion"))
        for _, path, _, headers in self.calls:
            self.assertEqual(headers["Authorization"], "Bearer " + TOKEN)
            self.assertEqual(headers["Notion-Version"], "2026-03-11")

    def test_incremental_and_new_old_page(self):
        self.run_sync()
        self.calls.clear()
        self.assertEqual(self.run_sync()["new"], 0)
        self.assertFalse(any(path.endswith("/markdown") for _, path, *_ in self.calls))
        changed = page(edited="2026-11-02T10:00:00.000Z")
        old = page(P2, "Старая открытая заметка", "2020-01-01T00:00:00.000Z")
        self.install_page(changed, "Теперь обновлено.")
        self.install_page(old, "Старую заметку открыли интеграции только сейчас.")
        self.routes["POST", "/v1/search"] = listed([changed, old])
        result = self.run_sync()
        self.assertEqual((result["new"], result["changed"], result["errors"]), (1, 1, 0))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0], 2)

    def test_nested_pages_databases_and_pagination(self):
        child = page(P2, "Вложенная заметка")
        row = page(P3, "Запись базы")
        self.install_page(child, "Текст вложенной страницы.")
        self.install_page(row, "Текст записи базы.")
        self.routes["POST", "/v1/search"] = lambda p, _: listed([page()], "next") if not p.get("start_cursor") else listed([])
        self.routes["GET", "/v1/blocks/" + P1 + "/children"] = listed([
            {"id": B1, "type": "toggle", "has_children": True, "toggle": {}}])
        self.routes["GET", "/v1/blocks/" + B1 + "/children"] = lambda _, q: (
            listed([{"id": P2, "type": "child_page", "has_children": True}], "children-next")
            if not q.get("start_cursor") else listed([{"id": D1, "type": "child_database"}]))
        self.routes["GET", "/v1/databases/" + D1] = {"id": D1, "data_sources": [{"id": DS}]}
        self.routes["POST", "/v1/data_sources/" + DS + "/query"] = lambda p, _: (
            listed([row], "rows-next") if not p.get("start_cursor") else listed([]))
        self.assertEqual(self.run_sync()["new"], 3)
        self.assertIsNone(self.error())
        self.assertEqual(len([p for m, p, *_ in self.calls if m == "POST" and p.endswith("/query")]), 2)
        # Поиск больше не возвращает страницы, но уже найденные страницы и базы продолжают обновляться.
        self.routes["POST", "/v1/search"] = listed([])
        changed = page(P3, "Запись базы", "2026-11-02T12:00:00.000Z")
        self.install_page(changed, "Новые данные в строке базы.")
        self.routes["POST", "/v1/data_sources/" + DS + "/query"] = listed([changed])
        self.assertEqual(self.run_sync()["changed"], 1)

    def test_truncated_markdown_loads_subtrees(self):
        self.routes["GET", "/v1/pages/" + P1 + "/markdown"] = {
            "markdown": "Начало", "truncated": True, "unknown_block_ids": [B1]}
        self.routes["GET", "/v1/pages/" + B1 + "/markdown"] = {
            "markdown": "Продолжение заметки", "truncated": False, "unknown_block_ids": []}
        self.assertEqual(self.run_sync()["errors"], 0)
        self.assertIn("Продолжение заметки", self.conn.execute("SELECT body FROM records").fetchone()[0])

    def test_link_to_page_self_reference_uses_block_api(self):
        self.routes["GET", "/v1/pages/" + P1 + "/markdown"] = {
            "markdown": "Начало", "truncated": True, "unknown_block_ids": [B1]}
        self.routes["GET", "/v1/pages/" + B1 + "/markdown"] = {
            "markdown": "<unknown/>", "truncated": True, "unknown_block_ids": [B1]}
        self.routes["GET", "/v1/blocks/" + B1] = {
            "type": "link_to_page", "link_to_page": {"type": "page_id", "page_id": P2}}
        self.assertEqual(self.run_sync()["errors"], 0)
        self.assertIn("https://www.notion.so/" + P2, self.conn.execute("SELECT body FROM records").fetchone()[0])
        self.assertIn(P1, json.loads(db.get_cursor(self.conn, "notion"))["pages"])

    def test_unexportable_self_reference_stays_partial_without_stopping_sync(self):
        self.routes["GET", "/v1/pages/" + P1 + "/markdown"] = {
            "markdown": "<unknown/>", "truncated": True, "unknown_block_ids": [P1]}
        self.routes["GET", "/v1/blocks/" + P1] = {"type": "unsupported"}
        self.assertEqual(self.run_sync()["errors"], 0)
        self.assertIn(P1, json.loads(db.get_cursor(self.conn, "notion"))["partial_pages"])

    def test_page_budget_preserves_children_missing_from_search(self):
        self.cfg["sources"]["notion"]["max_pages_per_run"] = 1
        self.install_page(page(P2), "Вложенная страница, которой нет в поиске.")
        self.routes["GET", "/v1/blocks/" + P1 + "/children"] = listed([
            {"id": P2, "type": "child_page", "has_children": True}])
        self.assertEqual(self.run_sync()["new"], 1)
        self.assertEqual(self.run_sync()["new"], 1)
        self.assertEqual(self.run_sync()["new"], 0)

    def test_incomplete_markdown_is_not_marked_imported(self):
        self.routes["GET", "/v1/pages/" + P1 + "/markdown"] = {
            "markdown": "Обрыв", "truncated": True, "unknown_block_ids": []}
        self.assertEqual(self.run_sync()["errors"], 1)
        self.assertIsNone(db.get_cursor(self.conn, "notion"))

    def test_unavailable_subtree_is_explicit_and_does_not_hide_page(self):
        self.routes["GET", "/v1/pages/" + P1 + "/markdown"] = {
            "markdown": "Доступная часть", "truncated": True, "unknown_block_ids": [B1]}
        self.assertEqual(self.run_sync()["new"], 1)
        self.assertIn("недоступен", self.conn.execute("SELECT body FROM records").fetchone()[0])

    def test_subtree_access_change_retries_unchanged_parent(self):
        self.routes["GET", "/v1/pages/" + P1 + "/markdown"] = {
            "markdown": "Доступная часть", "truncated": True, "unknown_block_ids": [B1]}
        self.assertEqual(self.run_sync()["new"], 1)
        self.routes["GET", "/v1/pages/" + B1 + "/markdown"] = {
            "markdown": "Теперь доступная часть", "truncated": False, "unknown_block_ids": []}
        self.assertEqual(self.run_sync()["changed"], 1)

    def test_partial_page_signed_file_refresh_does_not_queue_another_summary(self):
        route = ("GET", "/v1/pages/" + P1 + "/markdown")

        def markdown(signature, text="Текст заметки", filename="image.png"):
            url = ("https://prod-files-secure.s3.us-west-2.amazonaws.com/workspace/asset/" + filename
                   + "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date=" + signature
                   + "&X-Amz-Security-Token=" + signature + "&X-Amz-Signature=" + signature)
            return {"markdown": text + '\n<img src="' + url + '"/>',
                    "truncated": True, "unknown_block_ids": [B1]}

        self.routes[route] = markdown("first")
        self.assertEqual(self.run_sync()["new"], 1)
        self.conn.execute("UPDATE records SET summarized_hash=content_hash, summarized_at='2026-11-02T12:00:00Z'")
        self.conn.commit()
        before = dict(self.conn.execute("SELECT * FROM records").fetchone())
        self.routes[route] = markdown("refreshed")
        self.assertEqual(self.run_sync()["changed"], 0)
        self.assertEqual(summarize.pending_count(self.conn), 0)
        self.assertEqual(dict(self.conn.execute("SELECT * FROM records").fetchone()), before)
        # Частичную страницу по-прежнему перечитываем, чтобы не потерять открывшиеся блоки.
        self.assertIn(P1, json.loads(db.get_cursor(self.conn, "notion"))["partial_pages"])
        self.routes[route] = markdown("refreshed", text="Настоящая правка заметки")
        self.assertEqual(self.run_sync()["changed"], 1)
        self.assertEqual(summarize.pending_count(self.conn), 1)
        self.routes[route] = markdown("refreshed", text="Настоящая правка заметки", filename="replacement.png")
        self.assertEqual(self.run_sync()["changed"], 1)

    def test_signed_file_urls_keep_identity_and_other_link_parameters(self):
        for base in (
            "https://prod-files-secure.s3.us-west-2.amazonaws.com/workspace/asset/file.pdf",
            "https://s3.us-west-2.amazonaws.com/secure.notion-static.com/asset/file.pdf",
            "https://secure.notion-static.com/asset/file.pdf",
        ):
            with self.subTest(base=base):
                signed = base + "?page=2&X-Amz-Date=first&X-Amz-Security-Token=temporary&X-Amz-Signature=sig#section"
                text = '[Документ](' + signed + ')\n<img src="' + signed + '"/>'
                rec = notion.NotionConnector._record(page(), text)
                self.assertEqual(rec.body, text.replace(signed, base + "?page=2#section"))
        external = ("https://example.com/file.pdf?X-Amz-Date=first&version=1\n"
                    "https://www.youtube.com/watch?v=video&t=12\n"
                    "https://prod-files-secure.s3.us-west-2.amazonaws.com/asset/file.pdf?version=1")
        self.assertEqual(notion.NotionConnector._record(page(), external).body, external)

    def test_signed_file_normalization_handles_already_redacted_local_records(self):
        base = "https://prod-files-secure.s3.us-west-2.amazonaws.com/workspace/asset/image.png"
        signed = base + "?X-Amz-Date=first&X-Amz-Security-Token=" + "a" * 40 + "&X-Amz-Signature=sig"
        existing = redact('<img src="' + signed + '" alt="Снимок"/>')
        self.assertIn(MASK, existing)
        self.assertEqual(notion.NotionConnector._record(page(), existing).body,
                         '<img src="' + base + '" alt="Снимок"/>')

    def test_partial_page_retries_do_not_starve_new_pages(self):
        self.cfg["sources"]["notion"]["max_pages_per_run"] = 1
        self.routes["POST", "/v1/search"] = listed([page(), page(P2)])
        self.install_page(page(P2), "Следующая доступная страница.")
        self.routes["GET", "/v1/pages/" + P1 + "/markdown"] = {
            "markdown": "Частичная заметка", "truncated": True, "unknown_block_ids": [B1]}
        self.assertEqual(self.run_sync()["new"], 1)
        self.assertEqual(self.run_sync()["new"], 1)

    def test_fresh_database_metadata_overrides_stale_search_result(self):
        self.run_sync()
        changed = page(edited="2026-11-02T13:00:00.000Z")
        self.install_page(changed, "Изменение раньше попало в базу, чем в поиск.")
        self.routes["POST", "/v1/search"] = listed([page(), {"object": "data_source", "id": DS}])
        self.routes["POST", "/v1/data_sources/" + DS + "/query"] = listed([changed])
        self.assertEqual(self.run_sync()["changed"], 1)

    def test_unavailable_nested_block_does_not_drop_accessible_page(self):
        self.routes["GET", "/v1/blocks/" + P1 + "/children"] = listed([
            {"id": B1, "type": "synced_block", "has_children": True}])
        self.assertEqual(self.run_sync()["new"], 1)

    def test_nested_block_access_change_retries_discovery(self):
        self.routes["GET", "/v1/blocks/" + P1 + "/children"] = listed([
            {"id": B1, "type": "synced_block", "has_children": True}])
        self.assertEqual(self.run_sync()["new"], 1)
        self.routes["GET", "/v1/blocks/" + B1 + "/children"] = listed([
            {"id": P2, "type": "child_page"}])
        self.install_page(page(P2), "Вложенная заметка после открытия синхронизированного блока.")
        self.assertEqual(self.run_sync()["new"], 1)

    def test_incomplete_search_is_an_error(self):
        self.routes["POST", "/v1/search"] = dict(listed([page()]), request_status={
            "type": "incomplete", "incomplete_reason": "query_result_limit_reached"})
        self.assertEqual(self.run_sync()["errors"], 1)

    def test_newly_shared_child_is_retried_with_unchanged_parent(self):
        self.routes["GET", "/v1/blocks/" + P1 + "/children"] = listed([
            {"id": P2, "type": "child_page", "has_children": True}])
        self.assertEqual(self.run_sync()["new"], 1)
        self.install_page(page(P2), "Теперь доступен старый дочерний документ.")
        self.assertEqual(self.run_sync()["new"], 1)

    def test_long_text_property_is_read_with_pagination(self):
        item = page()
        item["properties"]["Notes"] = {"id": "long%3Atext", "type": "rich_text",
                                       "rich_text": [{"plain_text": "Обрыв"}] * 25}
        self.routes["POST", "/v1/search"] = listed([item])
        self.routes["GET", "/v1/pages/" + P1 + "/properties/long%3Atext"] = lambda _, q: (
            listed([{"type": "rich_text", "rich_text": {"plain_text": "Первая часть "}}], "next-prop")
            if not q.get("start_cursor") else listed([{"type": "rich_text", "rich_text": {"plain_text": "последняя часть"}}]))
        self.assertEqual(self.run_sync()["new"], 1)
        self.assertIn("Первая часть последняя часть", self.conn.execute("SELECT body FROM records").fetchone()[0])

    def test_failure_keeps_batches_and_retries_failed_page(self):
        self.routes["POST", "/v1/search"] = listed([page(), page(P2)])
        self.routes["GET", "/v1/pages/" + P2 + "/markdown"] = (503, {}, {"message": TOKEN})
        with mock.patch.object(notion, "PAGE_BATCH", 1):
            result = self.run_sync()
        self.assertEqual((result["new"], result["errors"]), (1, 1))
        self.assertEqual(set(json.loads(db.get_cursor(self.conn, "notion"))["pages"]), {P1})
        self.assertNotIn(TOKEN, self.error())
        self.install_page(page(P2), "Дочитали после сбоя.")
        self.assertEqual(self.run_sync()["new"], 1)

    def test_rate_limit_retry_and_secret_safe_errors(self):
        attempts = []

        def throttle(payload, query):
            attempts.append(1)
            return (429, {"Retry-After": "2"}, {}) if len(attempts) == 1 else listed([page()])

        self.routes["POST", "/v1/search"] = throttle
        with mock.patch.object(notion.time, "sleep") as sleep:
            self.assertEqual(self.run_sync()["errors"], 0)
        self.assertIn(mock.call(2), sleep.call_args_list)
        for code in (401, 403):
            self.routes["POST", "/v1/search"] = (code, {}, {"message": TOKEN})
            self.assertEqual(self.run_sync()["errors"], 1)
            self.assertNotIn(TOKEN, self.error())
            self.assertIn(str(code), self.error())

    def test_redirect_does_not_forward_credentials(self):
        self.routes["POST", "/v1/search"] = (307, {"Location": "/v1/leak"}, {})
        self.assertEqual(self.run_sync()["errors"], 1)
        self.assertFalse(any(path == "/v1/leak" for _, path, *_ in self.calls))


if __name__ == "__main__":
    unittest.main()
