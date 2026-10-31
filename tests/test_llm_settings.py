"""Переключение модели без потери настроек и передачи чужого ключа провайдеру."""
import copy
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from brain import config, db, llm_settings, status


class ModelSettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "config.json"
        env = patch.dict(os.environ, {"BRAIN_CONFIG": str(self.path), "BRAIN_LLM_API_KEY": ""})
        env.start(); self.addCleanup(env.stop)
        self.raw = {"search": {"recency_weight": 0.7}, "llm": {
            "base_url": "https://simple-claw.ru/v1", "model": "old-model", "api_key": "first-secret",
            "timeout": 90, "batch_limit": 7, "json_mode": False,
        }}
        self.write()

    def write(self):
        self.path.write_text(json.dumps(self.raw))

    def test_safe_description_and_cli_never_return_keys(self):
        with patch.dict(os.environ, {"BRAIN_LLM_API_KEY": "env-secret"}):
            description = llm_settings.describe()
            current = description["providers"][0]
            self.assertTrue(current["has_api_key"])
            self.assertTrue(current["key_from_env"])
            output = subprocess.check_output([sys.executable, "-m", "brain.cli", "model", "show"], text=True)
            self.assertEqual(json.loads(output), description)
        self.assertNotIn("first-secret", output)
        self.assertNotIn("env-secret", output)
        self.assertEqual(description["current"]["provider"], "SimpleClaw")

    def test_same_provider_keeps_key_and_unrelated_settings(self):
        result = llm_settings.save({"base_url": "https://SIMPLE-CLAW.ru/v1/", "model": "new-model"})
        stored = json.loads(self.path.read_text())
        self.assertEqual(stored["search"], self.raw["search"])
        self.assertEqual(stored["llm"]["api_key"], "first-secret")
        self.assertEqual(stored["llm"]["batch_limit"], 7)
        self.assertEqual(stored["llm"]["timeout"], 90)
        self.assertFalse(stored["llm"]["json_mode"])
        self.assertEqual(config.load()["llm"]["model"], "new-model")
        self.assertEqual(result["current"]["model"], "new-model")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.path.parent.glob(".config.json.*.tmp")), [])

    def test_switching_restores_profiles_and_does_not_reuse_env_key(self):
        with patch.dict(os.environ, {"BRAIN_LLM_API_KEY": "env-secret"}):
            # Новый провайдер не получает ни старый ключ, ни ключ из окружения.
            llm_settings.save({"base_url": "http://localhost:11434/v1", "model": "local-model"})
            self.assertEqual(config.load()["llm"]["api_key"], "")
            llm_settings.save({"base_url": "https://api.openai.com/v1", "model": "remote-model",
                               "api_key": "second-secret", "json_mode": True})
            self.assertEqual(config.load()["llm"]["api_key"], "second-secret")
            llm_settings.save({"base_url": "https://simple-claw.ru/v1"})
            restored = config.load()["llm"]
            self.assertEqual(restored["model"], "old-model")
            self.assertEqual(restored["api_key"], "env-secret")
            self.assertFalse(restored["json_mode"])
            llm_settings.save({"base_url": "https://api.openai.com/v1"})
            self.assertEqual(config.load()["llm"]["api_key"], "second-secret")
            self.assertEqual(config.load()["llm"]["model"], "remote-model")
        self.assertNotIn("env-secret", self.path.read_text())

    def test_invalid_choices_do_not_change_file(self):
        before = self.path.read_bytes()
        for payload in [
            {"base_url": "file:///tmp/test", "model": "x"},
            {"base_url": "https://user:secret@example.com/v1", "model": "x"},
            {"base_url": "https://example.com/v1?api_key=secret", "model": "x"},
            {"base_url": "https://example.com/\nv1", "model": "x"},
            {"base_url": "https://api.openai.com/v1", "model": "x"},
            {"base_url": "https://simple-claw.ru/v1", "model": ""},
            {"base_url": "https://simple-claw.ru/v1", "json_mode": "yes"},
        ]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                llm_settings.save(payload)
            self.assertEqual(self.path.read_bytes(), before)

    def test_failed_atomic_replace_preserves_config_and_removes_temporary(self):
        before = self.path.read_bytes()
        with patch("brain.llm_settings.os.replace", side_effect=OSError("test failure")):
            with self.assertRaises(OSError):
                llm_settings.save({"base_url": "https://simple-claw.ru/v1", "model": "new"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".config.json.*.tmp")), [])

    def test_reporter_keeps_run_model_snapshot_without_credentials(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["db_path"] = str(self.path.parent / "brain.db")
        cfg["llm"].update(self.raw["llm"])
        conn = db.connect(cfg["db_path"])
        self.addCleanup(conn.close)
        reporter = status.Reporter(cfg, conn)
        llm_settings.save({"base_url": "https://simple-claw.ru/v1", "model": "next-model"})
        reporter.progress(0, 1, "card")
        snapshot = status.read(reporter.path)
        self.assertEqual(snapshot["processor"], {"provider": "SimpleClaw", "model": "old-model"})
        self.assertNotIn("first-secret", reporter.path.read_text())

    def server(self):
        requests = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                if self.path == "/redirect/models":
                    self.send_response(302)
                    self.send_header("Location", "/v1/models")
                    self.end_headers()
                    return
                self.send_response(401 if self.path == "/denied/models" else 200)
                self.end_headers()
                if self.path == "/denied/models":
                    self.wfile.write(b'{"error": "first-secret must not appear in error"}')
                else:
                    self.wfile.write(json.dumps({"data": [
                        {"id": "Zulu"}, {"id": "alpha"}, {"id": "alpha"}, {},
                        {"id": "image", "architecture": {"output_modalities": ["image"]}},
                        {"id": "text", "architecture": {"output_modalities": ["text"]}},
                        {"id": "odd", "architecture": "other"},
                    ]}).encode())
        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}", requests

    def test_models_uses_selected_credentials_and_filters_list(self):
        base, requests = self.server()
        self.raw["llm"]["base_url"] = base + "/v1"
        self.write()
        with patch.dict(os.environ, {"BRAIN_LLM_API_KEY": "env-secret"}):
            self.assertEqual(llm_settings.models({"base_url": base + "/v1"})["models"],
                             ["alpha", "odd", "text", "Zulu"])
            llm_settings.models({"base_url": base + "/other"})
            llm_settings.models({"base_url": base + "/other", "api_key": "typed-secret"})
        self.assertEqual(requests, [("/v1/models", "Bearer env-secret"),
                                    ("/other/models", None), ("/other/models", "Bearer typed-secret")])

    def test_redirects_and_error_bodies_do_not_leak_credentials(self):
        base, requests = self.server()
        for suffix in ("redirect", "denied"):
            with self.assertRaises(RuntimeError) as raised:
                llm_settings.models({"base_url": base + "/" + suffix, "api_key": "first-secret"})
            self.assertNotIn("first-secret", str(raised.exception))
        self.assertEqual(len(requests), 2)  # redirect не последовал в /v1/models


if __name__ == "__main__":
    unittest.main()
