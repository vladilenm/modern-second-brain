"""Установка: install.sh во временную папку из локального репозитория и bin/brain через ссылку."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com"]


@unittest.skipUnless(sys.platform == "darwin", "установщик только для macOS")
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        (self.repo / "brain").mkdir(parents=True)
        (self.repo / "bin").mkdir()
        (self.repo / "brain" / "cli.py").write_text("# заглушка\n")
        (self.repo / "bin" / "brain").write_text("#!/bin/sh\necho brain\n")
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        subprocess.run(GIT + ["-C", str(self.repo), "add", "."], check=True)
        subprocess.run(GIT + ["-C", str(self.repo), "commit", "-qm", "init"], check=True)

    def install(self, target=None):
        env = dict(os.environ, HOME=str(self.home), BRAIN_REPO=str(self.repo), BRAIN_NO_TTY="1", BRAIN_NO_SETUP="1",
                   BRAIN_HOME=str(target or self.home / "brain"), BRAIN_BIN_DIR=str(self.home / ".local" / "bin"))
        return subprocess.run(["sh", str(ROOT / "install.sh")], env=env, capture_output=True, text=True, timeout=60)

    def test_fresh_install_then_update(self):
        first = self.install()
        self.assertEqual(first.returncode, 0, first.stderr)
        target = self.home / "brain"
        self.assertTrue((target / "brain" / "cli.py").exists())
        link = self.home / ".local" / "bin" / "brain"
        self.assertEqual(os.readlink(link), str(target / "bin" / "brain"))
        self.assertIn("export PATH", first.stdout)  # без терминала профиль не правится, строка печатается
        self.assertFalse((self.home / ".zprofile").exists())
        self.assertEqual([p.name for p in self.home.iterdir() if p.name.startswith(".brain-install")], [])
        second = self.install()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("Обновляю", second.stdout)

    def test_refuses_foreign_folder(self):
        (self.home / "brain").mkdir()
        (self.home / "brain" / "мои-файлы.txt").write_text("не трогать")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("это не второй мозг", result.stderr)
        self.assertTrue((self.home / "brain" / "мои-файлы.txt").exists())

    def test_refuses_protected_folders(self):
        result = self.install(self.home / "Documents" / "brain")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Документы", result.stderr)

    def test_script_syntax(self):
        subprocess.run(["sh", "-n", str(ROOT / "install.sh")], check=True)
        if shutil.which("shellcheck"):
            subprocess.run(["shellcheck", "-s", "sh", str(ROOT / "install.sh")], check=True)


class RepoHygieneTests(unittest.TestCase):
    """В том, что ставится людям, нет путей и меток автора. Прежнюю метку знает только autostart.py — чтобы снять её."""
    PATTERNS = ("/Users/vladilen", "com.vladilen")

    def test_no_personal_paths(self):
        files = subprocess.run(["git", "-C", str(ROOT), "ls-files", "bin", "brain", "menubar", "install.sh",
                                "config.example.json", "README.md", "docs/sources.md", "docs/development.md"],
                               capture_output=True, text=True, check=True).stdout.split()
        found = []
        for name in files:
            if name in ("brain/autostart.py", "brain/web/graph-vendor.js"):
                continue
            text = (ROOT / name).read_text(encoding="utf-8", errors="ignore")
            found += [f"{name}: {p}" for p in self.PATTERNS if p in text]
        self.assertEqual(found, [])


class LauncherTests(unittest.TestCase):
    def test_brain_works_through_symlink(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "bin").mkdir()
        os.symlink(ROOT / "bin" / "brain", tmp / "bin" / "brain")
        # Из чужой папки: python -m ищет модули и в текущей, из корня проекта тест прошёл бы и без исправления.
        result = subprocess.run([str(tmp / "bin" / "brain"), "--help"], capture_output=True, text=True, timeout=30,
                                cwd=tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("setup", result.stdout)

    def test_brain_ignores_other_copy_in_current_folder(self):
        """Запуск из папки с другой копией проекта не должен брать её код и её config.json."""
        tmp = Path(tempfile.mkdtemp())
        (tmp / "brain").mkdir()
        (tmp / "brain" / "__init__.py").write_text("")
        (tmp / "brain" / "cli.py").write_text("print('ЧУЖАЯ КОПИЯ')\ndef main():\n    return 0\n")
        result = subprocess.run([str(ROOT / "bin" / "brain"), "--help"], capture_output=True, text=True, timeout=30,
                                cwd=tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ЧУЖАЯ КОПИЯ", result.stdout)
        self.assertIn("setup", result.stdout)


if __name__ == "__main__":
    unittest.main()
