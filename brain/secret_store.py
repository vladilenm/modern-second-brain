"""Секреты источников: пароли приложений, токены и ключи API.

В config.json их не храним. На macOS — связка ключей через /usr/bin/security: коннекторы читают секрет
на каждый прогон, мастер настройки записывает. Бэкенд для другой ОС — те же функции.
"""
from __future__ import annotations

import subprocess
from typing import List, Optional

SECURITY = "/usr/bin/security"
TIMEOUT = 20


def add_command(service: str, account: str) -> str:
    """Ручная команда: она сама спросит секрет, в историю терминала он не попадёт."""
    return f"security add-generic-password -s {service} -a {account} -w"


def _run(cmd: List[str], stdin: Optional[str] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"не получилось обратиться к связке ключей ({exc})") from None


def get(service: str, account: str, command: Optional[List[str]] = None) -> str:
    out = _run(command or [SECURITY, "find-generic-password", "-s", service, "-a", account, "-w"])
    secret = out.stdout.rstrip("\n")
    if out.returncode != 0 or not secret:
        raise RuntimeError(f"нет пароля в связке ключей — выполни `{add_command(service, account)}`")
    return secret


def exists(service: str, account: str, command: Optional[List[str]] = None) -> bool:
    """Есть ли запись; сам секрет при этом не читается."""
    return _run(command or [SECURITY, "find-generic-password", "-s", service, "-a", account]).returncode == 0


def check_secret(secret: str) -> str:
    """Токены и пароли приложений — ASCII: не-ASCII `security -w` потом отдаёт в hex."""
    secret = (secret or "").strip()
    if not secret:
        raise ValueError("пусто")
    if not secret.isascii() or any(ord(c) < 32 for c in secret):
        raise ValueError("ключи и пароли приложений состоят из латиницы, цифр и знаков, без переносов строк")
    return secret


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def put(service: str, account: str, secret: str, command: Optional[List[str]] = None) -> None:
    """Записать или заменить. Секрет идёт через stdin `security -i`, а не аргументом: его не видно в ps."""
    secret = check_secret(secret)
    line = f"add-generic-password -U -s {_quote(service)} -a {_quote(account)} -w {_quote(secret)}\n"
    out = _run(command or [SECURITY, "-i"], stdin=line)
    problem = out.stderr.strip()
    if out.returncode != 0 or problem:
        raise RuntimeError(f"связка ключей не приняла запись ({problem or out.returncode})")
