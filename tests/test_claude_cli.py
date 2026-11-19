import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import llm_settings, summarize


class ClaudeCliTests(unittest.TestCase):
    def test_list_and_switch_between_cli_providers(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"BRAIN_CONFIG": tmp + "/config.json"}):
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"llm": {"provider": "codex_cli", "model": "gpt-6-luna", "batch_limit": 20}}))
            self.assertIn("sonnet", llm_settings.models({"provider": "claude_cli"})["models"])
            result = llm_settings.save({"provider": "claude_cli", "model": "sonnet"})
            self.assertEqual(result["current"]["provider"], "Claude Code CLI")
            self.assertEqual(json.loads(path.read_text())["llm"]["batch_limit"], 20)
            llm_settings.save({"provider": "codex_cli", "model": "gpt-6-luna"})
            choices = llm_settings.describe()["providers"]
            self.assertEqual(next(x for x in choices if x.get("provider") == "claude_cli")["model"], "sonnet")

    @patch("brain.summarize.subprocess.run")
    def test_claude_uses_stdin_without_tools_or_persistent_session(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, stdout='{"title":"test"}', stderr="")
        cfg = {"provider": "claude_cli", "model": "sonnet", "claude_path": "/usr/local/bin/claude"}
        self.assertTrue(summarize.configured(cfg))
        self.assertEqual(summarize.chat(cfg, "system instruction", "private content"), '{"title":"test"}')
        args, kwargs = run.call_args
        self.assertIn("--no-session-persistence", args[0])
        self.assertIn("--tools", args[0])
        self.assertIn("private content", kwargs["input"])
        self.assertNotIn("private content", " ".join(args[0]))

    @patch("brain.summarize.subprocess.run")
    def test_errors_do_not_leak_private_output(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, stdout="private content", stderr="private content")
        with self.assertRaises(RuntimeError) as ctx:
            summarize.chat({"provider": "claude_cli", "model": "sonnet"}, "system", "private content")
        self.assertNotIn("private content", str(ctx.exception))

    @patch("shutil.which", return_value="/custom/bin/claude")
    @patch("brain.summarize.subprocess.run")
    def test_uses_claude_from_path_without_configured_executable(self, run, which):
        run.return_value = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
        summarize.chat({"provider": "claude_cli", "model": "sonnet"}, "system", "text")
        self.assertEqual(run.call_args.args[0][0], "/custom/bin/claude")
