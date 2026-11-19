import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import summarize


class CliPathTests(unittest.TestCase):
    def test_relative_path_and_wrapper_runtime_survive_temporary_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = root / "tools"
            tools.mkdir()
            runtime = tools / "brain-test-runtime"
            runtime.write_text("#!/bin/sh\nprintf '{}\\n'\n")
            runtime.chmod(0o755)
            for name in ("codex", "claude"):
                executable = tools / name
                executable.write_text("#!/usr/bin/env brain-test-runtime\n")
                executable.chmod(0o755)
            original = os.getcwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {"PATH": "tools:/usr/bin:/bin"}):
                    for name in ("codex", "claude"):
                        for configured in (False, True):
                            with self.subTest(name=name, configured=configured):
                                llm = {"provider": name + "_cli", "model": "test"}
                                if configured:
                                    llm[name + "_path"] = "tools/" + name
                                self.assertEqual(summarize.chat(llm, "system", "content"), "{}")
            finally:
                os.chdir(original)

    def test_explicit_wrapper_directory_is_available_to_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = Path(tmp)
            (tools / "brain-test-runtime").write_text("#!/bin/sh\nprintf '{}\\n'\n")
            (tools / "brain-test-runtime").chmod(0o755)
            (tools / "codex").write_text("#!/usr/bin/env brain-test-runtime\n")
            (tools / "codex").chmod(0o755)
            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
                self.assertEqual(summarize.chat({"provider": "codex_cli", "model": "test",
                                                 "codex_path": str(tools / "codex")}, "s", "u"), "{}")
