import subprocess
import unittest
from unittest.mock import patch

from brain import summarize


class CodexSummarizerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"provider": "codex_cli", "model": "gpt-6-luna", "codex_path": "/usr/local/bin/codex", "timeout": 30}

    def test_configured_without_api_url(self):
        self.assertTrue(summarize.configured(self.cfg))
        self.assertFalse(summarize.configured({"provider": "codex_cli"}))

    @patch("shutil.which", return_value="/custom/bin/codex")
    @patch("brain.summarize.subprocess.run")
    def test_uses_codex_from_path_without_configured_executable(self, run, which):
        run.return_value = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
        summarize.chat({"provider": "codex_cli", "model": "example"}, "system", "text")
        self.assertEqual(run.call_args.args[0][0], "/custom/bin/codex")

    @patch("brain.summarize.subprocess.run")
    def test_uses_ephemeral_codex_and_passes_private_text_on_stdin(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, stdout='{"title":"test"}', stderr="")
        result = summarize.chat(self.cfg, "system instruction", "private conversation")
        self.assertEqual(result, '{"title":"test"}')
        args, kwargs = run.call_args
        command = args[0]
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("gpt-6-luna", command)
        self.assertIn("read-only", command)
        self.assertNotIn("private conversation", " ".join(command))
        self.assertIn("private conversation", kwargs["input"])
        self.assertEqual(kwargs["timeout"], 30)

    @patch("brain.summarize.subprocess.run")
    def test_failure_does_not_leak_stderr_into_database(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="private content")
        with self.assertRaises(RuntimeError) as ctx:
            summarize.chat(self.cfg, "system", "private content")
        self.assertNotIn("private content", str(ctx.exception))

class CodexStatusTests(unittest.TestCase):
    def test_status_shows_cli_provider_without_api_url(self):
        import contextlib
        import io
        import tempfile
        from pathlib import Path
        from brain import cli, config

        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.load()
            cfg['db_path'] = str(Path(tmp) / 'brain.db')
            cfg['llm'].update(provider='codex_cli', model='gpt-6-luna')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.cmd_status(None, cfg), 0)
            self.assertIn('gpt-6-luna через Codex CLI', out.getvalue())
