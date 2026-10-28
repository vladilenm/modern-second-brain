"""Пароли из связки ключей macOS.

Пароли приложений почты не храним в config.json: пользователь кладёт их в связку ключей
командой `security add-generic-password`, а мы читаем на каждый прогон и нигде не сохраняем.
"""
from __future__ import annotations

import subprocess
from typing import List, Optional

SECURITY = "/usr/bin/security"


def add_command(service: str, account: str) -> str:
    """Команда, которой пользователь кладёт пароль: она сама его спросит, в историю терминала он не попадёт."""
    return f"security add-generic-password -s {service} -a {account} -w"


def password(service: str, account: str, command: Optional[List[str]] = None) -> str:
    cmd = command or [SECURITY, "find-generic-password", "-s", service, "-a", account, "-w"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"не получилось прочитать связку ключей ({exc})") from None
    secret = out.stdout.rstrip("\n")
    if out.returncode != 0 or not secret:
        raise RuntimeError(f"нет пароля в связке ключей — выполни `{add_command(service, account)}`")
    return secret
