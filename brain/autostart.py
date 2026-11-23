"""Фоновая синхронизация: brain sync каждые 15 минут.

macOS — агент launchd. Plist собирается здесь с путями этой установки, поэтому в репозитории нет файла
с чужими путями. Для другой ОС нужны те же enable / disable / enabled / status.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path
from typing import Optional

from . import config

LABEL = "local.brain.sync"
LEGACY_LABELS = ("com.vladilen.brain-sync",)  # метка ранних версий: снимаем при включении
LAUNCHCTL = "/bin/launchctl"
AGENTS = Path.home() / "Library" / "LaunchAgents"
LOG = Path.home() / "Library" / "Logs" / "brain-sync.log"


def plist_path(label: str = LABEL) -> Path:
    return Path(AGENTS) / f"{label}.plist"


def render(root: Path = config.ROOT, log: Optional[Path] = None) -> bytes:
    """По часам (:00, :15, :30, :45): StartInterval с ProcessType Background macOS откладывала на десятки
    минут. Если Mac спал, пропущенный запуск выполнится при пробуждении."""
    log = str(log or LOG)
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(Path(root) / "bin" / "brain"), "sync"],
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},  # иначе журнал пуст до конца прогона
        "StartCalendarInterval": [{"Minute": minute} for minute in (0, 15, 30, 45)],
        "LowPriorityIO": True,
        "Nice": 10,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    })


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([LAUNCHCTL, *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"launchctl недоступен ({exc})") from None


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _unload(label: str) -> None:
    _launchctl("bootout", f"{_domain()}/{label}")  # не загружен — тоже нормально
    if plist_path(label).exists():
        plist_path(label).unlink()


def _log_text() -> str:
    home = str(Path.home())
    path = str(LOG)
    return "~" + path[len(home):] if path.startswith(home) else path


def enable(root: Path = config.ROOT) -> str:
    for label in LEGACY_LABELS:
        _unload(label)
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    Path(LOG).parent.mkdir(parents=True, exist_ok=True)
    _launchctl("bootout", f"{_domain()}/{LABEL}")  # перезапуск с новым plist
    path.write_bytes(render(root))
    out = _launchctl("bootstrap", _domain(), str(path))
    if out.returncode != 0:
        reason = out.stderr.strip() or f"код {out.returncode}"
        raise RuntimeError(f"{reason}; вручную: launchctl bootstrap {_domain()} {path}")
    return f"синхронизация каждые 15 минут, журнал: {_log_text()}"


def disable() -> str:
    _unload(LABEL)
    return "автозапуск выключен"


def enabled() -> bool:
    return _launchctl("print", f"{_domain()}/{LABEL}").returncode == 0


def status() -> str:
    if enabled():
        return f"автозапуск включён: каждые 15 минут, журнал: {_log_text()}"
    return "автозапуск выключен"
