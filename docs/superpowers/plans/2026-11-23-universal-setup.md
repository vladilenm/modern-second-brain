# Универсальная установка и мастер настройки — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** новый человек ставит второй мозг одной командой, выбирает источники галочками, настраивает каждый
без правки файлов и по README подключает базу к своим нейронкам по MCP.

**Architecture:** каждый коннектор описывает свою настройку (`setup_fields`, `setup_help`, `detect`, `check`,
`secret_ref`); общий мастер `brain/wizard.py` показывает её через `brain/ui.py`. Секреты — `brain/secret_store.py`
(связка ключей macOS), фоновый запуск — `brain/autostart.py` (launchd с путями этой установки). Установка —
`install.sh` в корне репозитория. README переписывается для нового человека, подробности уходят в `docs/`.

**Tech Stack:** Python 3.9 из `/usr/bin/python3`, только стандартная библиотека (`curses`, `getpass`, `plistlib`,
`subprocess`), POSIX sh, `/usr/bin/security`, `/bin/launchctl`, `git`, unittest.

Спека: `docs/superpowers/specs/2026-11-23-universal-setup-design.md`.

## Global Constraints

- Только macOS. Интерфейсы `secret_store` и `autostart` не зависят от ОС, бэкенд один — macOS.
- Без внешних зависимостей: только стандартная библиотека Python 3.9 (`/usr/bin/python3`). Никаких `match`, `X | Y` в рантайме.
- Формат `config.json` и имена записей в связке ключей (`brain-mail`, `brain-notion`, `brain-youtube`) не меняются.
- В `config.json` мастер пишет только то, что спросил, плюс `enabled`; файл — права 0600, запись атомарная.
- Секрет никогда не передаётся аргументом процесса и не пишется в `config.json` (исключение — ключ модели пересказа: он и сейчас лежит в `config.json`, через `llm_settings.save`).
- Секреты источников — только ASCII без управляющих символов: `security -w` печатает не-ASCII как hex.
- Мастер ничего не меняет в конфигах MCP-клиентов: только печатает команды и JSON.
- Метка автозапуска `local.brain.sync`, прежняя `com.vladilen.brain-sync` снимается при включении.
- Названия не меняются: команда `brain`, репозиторий `vladilenm/modern-second-brain`.
- Тексты для людей — по-русски, коротко, без канцелярита. Комментарии в коде — по-русски, как в проекте.
- Тесты: `/usr/bin/python3 -m unittest discover -s tests` из корня — все зелёные после каждой задачи (сейчас 177).
- Каждая задача — отдельный коммит, сообщение по-русски в стиле `feat: …` / `refactor: …` / `docs: …`,
  в конце строка `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Карта файлов

| Файл | Что делает |
|---|---|
| `brain/secret_store.py` (новый, вместо `keychain.py`) | `get`, `put`, `exists`, `add_command`, `check_secret` |
| `brain/config.py` | + `read_raw`, `write_raw`, `save_sources` |
| `brain/llm_settings.py` | `read_raw`/`save` используют `config.read_raw`/`config.write_raw` |
| `brain/connectors/base.py` | + `Field`, у `Connector` — `setup_fields`, `setup_help`, `secret_service`, `secret_ref`, `detect`, `check`; у `FileConnector` — `detect`/`check` по файлам |
| `brain/connectors/{github,telegram,chatgpt_export,mail,gmail,yandex_mail,notion,youtube}.py` | поля, подсказки, `detect`, `check` |
| `brain/ui.py` (новый) | `TextUi`, `Checklist`, `CursesUi`, `make_ui` |
| `brain/autostart.py` (новый) | `render`, `enable`, `disable`, `enabled`, `status` |
| `brain/wizard.py` (новый) | шаги мастера, `Actions`, `run` |
| `brain/cli.py` | + команды `setup`, `autostart`, `update` |
| `bin/brain` | разворачивает ссылку на себя |
| `install.sh` (новый) | установка и обновление одной командой |
| `menubar/BrainBar.swift`, `menubar/Info.plist`, `menubar/build.sh` | без личной метки и жёсткого `~/brain` |
| `launchd/` | удаляется |
| `README.md`, `docs/sources.md`, `docs/development.md`, `config.example.json` | документация |
| `tests/test_setup.py` (новый) | secret_store, config, ui, autostart, wizard, cli |
| `tests/test_install.py` (новый) | `install.sh`, `bin/brain` через ссылку, нет личных путей |

---

### Task 1: Хранилище секретов `secret_store`

**Files:**
- Create: `brain/secret_store.py`
- Delete: `brain/keychain.py`
- Modify: `brain/connectors/mail.py:15,45`, `brain/connectors/notion.py:18,188-194`, `brain/connectors/youtube.py:21,251-257`
- Modify: `tests/test_mail.py:20,31-39`, `tests/test_notion.py:109`
- Test: `tests/test_setup.py` (новый)

**Interfaces:**
- Produces: `secret_store.get(service: str, account: str, command: Optional[List[str]] = None) -> str` (RuntimeError, если нет);
  `secret_store.put(service: str, account: str, secret: str, command: Optional[List[str]] = None) -> None`
  (ValueError — секрет не подходит, RuntimeError — связка ключей не приняла);
  `secret_store.exists(service: str, account: str, command: Optional[List[str]] = None) -> bool`;
  `secret_store.add_command(service: str, account: str) -> str`; `secret_store.check_secret(secret: str) -> str`.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_setup.py`:

```python
"""Мастер настройки и всё, на чём он стоит: секреты, запись конфига, ввод, автозапуск, команды CLI."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brain import secret_store  # noqa: E402


def fake_command(tmp: Path, body: str) -> Path:
    """Исполняемый sh-скрипт вместо системной программы."""
    path = tmp / "fake"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


class SecretStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_put_sends_secret_through_stdin_not_arguments(self):
        fake = fake_command(self.tmp, f'echo "$@" > "{self.tmp}/args"\ncat > "{self.tmp}/stdin"\n')
        secret_store.put("brain-x", "me@x.ru", 'p"a\\ss', command=[str(fake), "-i"])
        self.assertEqual((self.tmp / "args").read_text().strip(), "-i")
        self.assertEqual((self.tmp / "stdin").read_text(),
                         'add-generic-password -U -s "brain-x" -a "me@x.ru" -w "p\\"a\\\\ss"\n')

    def test_put_rejects_empty_and_non_ascii(self):
        for bad in ("", "   ", "пароль", "a\nb"):
            with self.assertRaises(ValueError):
                secret_store.put("brain-x", "me", bad, command=["/usr/bin/false"])

    def test_put_reports_keychain_refusal(self):
        fake = fake_command(self.tmp, 'cat >/dev/null\necho "User interaction is not allowed." >&2\nexit 1\n')
        with self.assertRaises(RuntimeError) as ctx:
            secret_store.put("brain-x", "me", "token", command=[str(fake)])
        self.assertIn("not allowed", str(ctx.exception))

    def test_get_and_exists(self):
        self.assertEqual(secret_store.get("brain-x", "me", [sys.executable, "-c", "print('t-1')"]), "t-1")
        self.assertTrue(secret_store.exists("brain-x", "me", [sys.executable, "-c", "pass"]))
        self.assertFalse(secret_store.exists("brain-x", "me", [sys.executable, "-c", "raise SystemExit(44)"]))

    def test_missing_secret_explains_how_to_add(self):
        with self.assertRaises(RuntimeError) as ctx:
            secret_store.get("brain-mail", "me@x.ru", [sys.executable, "-c", "raise SystemExit(44)"])
        self.assertIn("security add-generic-password -s brain-mail -a me@x.ru -w", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `/usr/bin/python3 -m unittest discover -s tests -p test_setup.py -v`
Expected: ошибка импорта `cannot import name 'secret_store'`.

- [ ] **Step 3: Написать `brain/secret_store.py`**

```python
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
```

- [ ] **Step 4: Перевести коннекторы и тесты на `secret_store`, удалить `keychain.py`**

```bash
git rm -q brain/keychain.py
```

В `brain/connectors/mail.py`, `notion.py`, `youtube.py`: `from .. import keychain` → `from .. import secret_store`;
`keychain.password(` → `secret_store.get(`; `keychain.add_command(` → `secret_store.add_command(`.

В `tests/test_mail.py`: строка 20 `from brain import db, keychain` → `from brain import db, secret_store`;
в `KeychainTests` оба вызова `keychain.password(` → `secret_store.get(`, класс переименовать в `SecretReadTests`.

В `tests/test_notion.py:109`: `mock.patch("brain.keychain.password", return_value=TOKEN)` →
`mock.patch("brain.secret_store.get", return_value=TOKEN)`.

Проверить, что упоминаний не осталось: `grep -rn "keychain\." brain tests | grep -v "keychain_service\|keychain_account"` — пусто.

- [ ] **Step 5: Тесты зелёные**

Run: `/usr/bin/python3 -m unittest discover -s tests`
Expected: `OK`, тестов 182.

- [ ] **Step 6: Проверить на настоящей связке ключей**

Формат строки для `security -i` (кавычки, `\\` и `\"` внутри, `-U` заменяет запись) проверен 2026-11-23 на временной
связке: значения `a\b"c d` и `x$y\`z` читаются обратно без изменений, кириллица возвращается в hex — отсюда `check_secret`.
Проверка в связке login с пробной записью, которая сразу удаляется:

```bash
/usr/bin/python3 -c "
import sys; sys.path.insert(0, '.')
from brain import secret_store
secret_store.put('brain-test', 'me@x.ru', 'se\"cr\\\\et')
print(secret_store.get('brain-test', 'me@x.ru'), secret_store.exists('brain-test', 'me@x.ru'))"
security delete-generic-password -s brain-test -a me@x.ru >/dev/null && echo удалено
```

Expected: `se"cr\et True`, затем `удалено`. Если `security -i` не сработает — запасной путь из спеки: `put` запускает
`security add-generic-password -U -s … -a … -w` без значения, и `security` спрашивает секрет сам на терминале.

- [ ] **Step 7: Commit**

```bash
git add brain/secret_store.py brain/connectors/mail.py brain/connectors/notion.py brain/connectors/youtube.py tests/test_setup.py tests/test_mail.py tests/test_notion.py
git commit -m "refactor: секреты через secret_store — чтение, запись через stdin и проверка наличия

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Запись `config.json` по частям

**Files:**
- Modify: `brain/config.py` (после `config_path`)
- Modify: `brain/llm_settings.py:60-71` (`read_raw`), `:174-205` (`save`)
- Test: `tests/test_setup.py`

**Interfaces:**
- Produces: `config.read_raw() -> dict` (config.json как есть, без умолчаний; нет файла — `{}`; не объект — ValueError);
  `config.write_raw(raw: dict) -> None` (атомарно, 0600); `config.save_sources(changes: Dict[str, dict]) -> dict`
  (вливает каждую секцию в `sources.<name>`, возвращает записанное).

- [ ] **Step 1: Написать падающие тесты** — добавить в `tests/test_setup.py` (импорт `from brain import config, secret_store`):

```python
class ConfigWriteTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "config.json"
        env = mock.patch.dict(os.environ, {"BRAIN_CONFIG": str(self.path)})
        env.start()
        self.addCleanup(env.stop)

    def test_save_sources_keeps_everything_else(self):
        self.path.write_text(json.dumps({"llm": {"model": "m"},
                                         "sources": {"gmail": {"address": "a@b.c", "every_minutes": 5}}}))
        config.save_sources({"gmail": {"enabled": True}, "telegram": {"channels": ["x"], "enabled": True}})
        stored = json.loads(self.path.read_text())
        self.assertEqual(stored["llm"], {"model": "m"})
        self.assertEqual(stored["sources"]["gmail"], {"address": "a@b.c", "every_minutes": 5, "enabled": True})
        self.assertEqual(stored["sources"]["telegram"], {"channels": ["x"], "enabled": True})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["config.json"])  # временных файлов не осталось

    def test_missing_file_is_created_and_read_raw_is_empty_before(self):
        self.assertEqual(config.read_raw(), {})
        config.save_sources({"kimi": {"enabled": False}})
        self.assertEqual(config.read_raw(), {"sources": {"kimi": {"enabled": False}}})

    def test_read_raw_rejects_non_object(self):
        self.path.write_text("[]")
        with self.assertRaises(ValueError):
            config.read_raw()
```

- [ ] **Step 2: Убедиться, что падают**

Run: `/usr/bin/python3 -m unittest discover -s tests -p test_setup.py -v`
Expected: `AttributeError: module 'brain.config' has no attribute 'save_sources'` (и `read_raw`).

- [ ] **Step 3: Реализовать в `brain/config.py`**

В импорты добавить `import tempfile` и `from typing import Dict`. После `config_path()`:

```python
def read_raw() -> dict:
    """config.json как есть, без умолчаний: то, что человек или мастер записали сами."""
    path = config_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("В config.json ожидается объект настроек.")
    return data


def write_raw(raw: dict) -> None:
    """Атомарно и с правами 0600: в файле ключ модели, оборванная запись не должна портить настройки."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as out:
            temporary = out.name
            os.fchmod(out.fileno(), 0o600)
            json.dump(raw, out, ensure_ascii=False, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def save_sources(changes: Dict[str, dict]) -> dict:
    """Влить настройки источников в config.json; остальные ключи и поля не трогаются."""
    raw = read_raw()
    sources = raw.setdefault("sources", {})
    for name, values in changes.items():
        sources.setdefault(name, {}).update(values)
    write_raw(raw)
    return raw
```

- [ ] **Step 4: `llm_settings` использует общие функции**

В `brain/llm_settings.py` `read_raw` заменить на:

```python
def read_raw() -> dict:
    data = config.read_raw()
    if not isinstance(data.get("llm", {}), dict):
        raise ValueError("В config.json ожидается объект настроек с секцией llm.")
    profiles = data.get("llm", {}).get("profiles", {})
    if not isinstance(profiles, dict) or any(not isinstance(p, dict) for p in profiles.values()):
        raise ValueError("В llm.profiles ожидаются настройки провайдеров.")
    return data
```

В `save` всё от `path = config.config_path()` до `os.unlink(temporary)` заменить одной строкой `config.write_raw(raw)`.
Убрать ставший лишним `import tempfile` из `llm_settings.py` (`os` остаётся: он нужен `api_key`/`describe`).

- [ ] **Step 5: Тесты зелёные**

Run: `/usr/bin/python3 -m unittest discover -s tests`
Expected: `OK` (185), в том числе все `test_llm_settings`.

- [ ] **Step 6: Commit**

```bash
git add brain/config.py brain/llm_settings.py tests/test_setup.py
git commit -m "feat: запись config.json по частям — save_sources и общая атомарная запись

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Контракт настройки коннектора и файловые источники

**Files:**
- Modify: `brain/connectors/base.py` (импорты, после `Batch`, класс `Connector`, класс `FileConnector`)
- Modify: `brain/connectors/chatgpt_export.py` (класс `ChatGPTExportConnector`)
- Test: `tests/test_setup.py`

**Interfaces:**
- Produces: `Field(key: str, label: str, kind: str = "text", help: str = "", required: bool = False, account_from: str = "")`
  (frozen dataclass, `kind` ∈ `text | list | secret`) в `brain.connectors.base`;
  атрибуты класса `Connector.setup_fields: Tuple[Field, ...] = ()`, `Connector.setup_help: str = ""`,
  `Connector.secret_service: str = ""`; методы `Connector.secret_ref(field: Field) -> Tuple[str, str]`,
  `Connector.detect() -> Optional[str]`, `Connector.check() -> str` (RuntimeError с причиной);
  `FileConnector.detect_forms: Tuple[str, str, str]`.

- [ ] **Step 1: Написать падающие тесты** — в `tests/test_setup.py`:

```python
from brain.connectors.base import Connector, Field  # noqa: E402
from brain.connectors.chatgpt_export import ChatGPTExportConnector  # noqa: E402
from brain.connectors.claude_code import ClaudeCodeConnector  # noqa: E402
from test_brain import make_cfg, write_jsonl  # noqa: E402


class SetupContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = make_cfg(self.tmp)

    def test_file_source_counts_conversations(self):
        conn = ClaudeCodeConnector(self.cfg)
        self.assertIsNone(conn.detect())
        with self.assertRaises(RuntimeError) as ctx:
            conn.check()
        self.assertIn("ничего не найдено", str(ctx.exception))
        for name in ("a", "b"):
            write_jsonl(self.tmp / "claude" / "proj" / f"{name}.jsonl", [{"type": "user"}])
        self.assertEqual(conn.detect(), "2 разговора")
        self.assertEqual(conn.check(), "2 разговора")

    def test_chatgpt_counts_archives_in_inbox(self):
        conn = ChatGPTExportConnector(self.cfg)
        self.assertIsNone(conn.detect())
        (self.tmp / "inbox").mkdir()
        (self.tmp / "inbox" / "export.zip").write_bytes(b"")
        self.assertEqual(conn.detect(), "1 архив")
        self.assertIn("Export data", conn.setup_help)

    def test_secret_ref_defaults_and_account_from_field(self):
        class Fake(Connector):
            name = "fake"
            secret_service = "brain-fake"
        token = Field("token", "Токен", kind="secret")
        mail = Field("password", "Пароль", kind="secret", account_from="address")
        self.cfg["sources"]["fake"] = {"address": "me@x.ru"}
        self.assertEqual(Fake(self.cfg).secret_ref(token), ("brain-fake", "default"))
        self.assertEqual(Fake(self.cfg).secret_ref(mail), ("brain-fake", "me@x.ru"))
        self.cfg["sources"]["fake"].update(keychain_service="other", keychain_account="work")
        self.assertEqual(Fake(self.cfg).secret_ref(token), ("other", "work"))

    def test_plain_connector_needs_no_setup(self):
        self.assertIsNone(Connector(self.cfg).detect())
        self.assertEqual(Connector(self.cfg).check(), "настройка не требуется")
```

- [ ] **Step 2: Убедиться, что падают**

Run: `/usr/bin/python3 -m unittest discover -s tests -p test_setup.py -v`
Expected: `ImportError: cannot import name 'Field'`.

- [ ] **Step 3: Реализовать в `brain/connectors/base.py`**

Импорт: `from typing import Iterable, Iterator, List, Optional, Tuple`. После класса `Batch`:

```python
@dataclass(frozen=True)
class Field:
    """Что мастер настройки спрашивает у человека."""
    key: str                # ключ в sources.<name>; у секрета — только имя, в конфиг он не пишется
    label: str              # «Адрес почты», «Каналы»
    kind: str = "text"      # text | list | secret
    help: str = ""          # подсказка строкой над вопросом
    required: bool = False
    account_from: str = ""  # для secret: поле, чьё значение — аккаунт в связке ключей (у почты — address)
```

В класс `Connector` после `default_every_minutes = 0`:

```python
    # Настройка в мастере (brain setup): что спросить, где взять ключ, как найти и проверить источник.
    setup_fields: Tuple["Field", ...] = ()
    setup_help = ""
    secret_service = ""  # сервис в связке ключей, если у источника есть секрет
```

И методы после `excludes`:

```python
    def secret_ref(self, field: "Field") -> Tuple[str, str]:
        """Где в связке ключей лежит секрет поля: (сервис, аккаунт)."""
        service = self.settings.get("keychain_service") or self.secret_service
        if field.account_from:
            return service, str(self.settings.get(field.account_from) or "")
        return service, self.settings.get("keychain_account") or "default"

    def detect(self) -> Optional[str]:
        """Что уже есть на компьютере, одной строкой («412 разговоров»); None — ничего. Без записи в базу."""
        return None

    def check(self) -> str:
        """Пробное подключение без записи в базу: строка для показа или RuntimeError с причиной."""
        return "настройка не требуется"
```

В класс `FileConnector` после `one_record_per_file = True`:

```python
    detect_forms = ("разговор", "разговора", "разговоров")

    def detect(self) -> Optional[str]:
        from ..context import words  # context тянет поиск и проекты — импорт по месту
        count = sum(1 for _ in self.files())
        return words(count, self.detect_forms) if count else None

    def check(self) -> str:
        found = self.detect()
        if not found:
            raise RuntimeError("ничего не найдено в " + ", ".join(home_relative(r) for r in self.roots()))
        return found
```

- [ ] **Step 4: ChatGPT — свои слова и подсказка**

В `ChatGPTExportConnector` после `one_record_per_file = False`:

```python
    detect_forms = ("архив", "архива", "архивов")
    setup_help = ("API для истории у OpenAI нет. В ChatGPT: Settings → Data controls → Export data. "
                  "Архив из письма положи в папку inbox внутри папки второго мозга — следующая "
                  "синхронизация заберёт новые и изменившиеся чаты.")
```

- [ ] **Step 5: Тесты зелёные**

Run: `/usr/bin/python3 -m unittest discover -s tests`
Expected: `OK` (189).

- [ ] **Step 6: Commit**

```bash
git add brain/connectors/base.py brain/connectors/chatgpt_export.py tests/test_setup.py
git commit -m "feat: контракт настройки коннектора — поля, подсказка, поиск и проверка источника

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: GitHub и Telegram — настройка и проверка

**Files:**
- Modify: `brain/connectors/github.py` (импорт `Field`, класс `GitHubConnector`)
- Modify: `brain/connectors/telegram.py` (импорт `Field`, класс `TelegramConnector`)
- Test: `tests/test_brain.py` (новый класс после `GitHubRepoTests`, методы в `TelegramTests`)

**Interfaces:**
- Consumes: `Field`, `Connector.detect/check` из Task 3; `read_token`, `GitHubAPI`, `channel_name`, `TelegramConnector.page`.
- Produces: `GitHubConnector.detect() -> "вход в gh выполнен" | None`, `GitHubConnector.check() -> "аккаунт <login>"`;
  `TelegramConnector.check() -> "каналы найдены: <названия через запятую>"`.

- [ ] **Step 1: Написать падающие тесты**

В `tests/test_brain.py` после класса `GitHubRepoTests`:

```python
class GitHubSetupTests(GitHubCase):
    def test_detect_and_check_use_gh_token(self):
        conn = GitHubConnector(self.cfg)
        self.assertEqual(conn.detect(), "вход в gh выполнен")
        self.assertEqual(conn.check(), "аккаунт vm")

    def test_without_gh_login_check_explains(self):
        self.cfg["sources"]["github"]["token_command"] = [sys.executable, "-c", "import sys; sys.exit(1)"]
        conn = GitHubConnector(self.cfg)
        self.assertIsNone(conn.detect())
        with self.assertRaises(RuntimeError) as ctx:
            conn.check()
        self.assertIn("gh auth login", str(ctx.exception))
```

В класс `TelegramTests`:

```python
    def test_check_finds_channel(self):
        self.assertEqual(self.connector(self.cfg).check(), "каналы найдены: Мой канал")

    def test_check_names_channel_without_posts(self):
        self.cfg["sources"]["telegram"]["channels"] = ["mine", "other_channel"]
        with self.assertRaises(RuntimeError) as ctx:
            self.connector(self.cfg).check()
        self.assertIn("@other_channel", str(ctx.exception))

    def test_check_needs_channels(self):
        self.cfg["sources"]["telegram"]["channels"] = []
        with self.assertRaises(RuntimeError):
            self.connector(self.cfg).check()
```

- [ ] **Step 2: Убедиться, что падают**

Run: `/usr/bin/python3 -m unittest discover -s tests -p test_brain.py -k Setup -k check -v`
Expected: `AssertionError: None != 'вход в gh выполнен'` и `'настройка не требуется' != 'каналы найдены: Мой канал'`.

- [ ] **Step 3: GitHub**

В `brain/connectors/github.py` импорт: `from .base import ApiConnector, Batch, Field, Record, clip`. В `GitHubConnector` после `about`:

```python
    setup_fields = (Field("exclude_repos", "Не брать репозитории", kind="list",
                          help="шаблоны owner/name через запятую, можно со *: owner/tmp-*. Enter — брать все"),)
    setup_help = ("Нужна программа gh, вошедшая в твой аккаунт: brew install gh, затем gh auth login. "
                  "Берутся PR и issues с твоим участием, твои коммиты по месяцам и README за 2 года, без форков.")

    def detect(self) -> Optional[str]:
        try:
            read_token(self.settings.get("token_command"))
        except RuntimeError:
            return None
        return "вход в gh выполнен"

    def check(self) -> str:
        api = GitHubAPI(self.settings.get("api_base") or "https://api.github.com",
                        read_token(self.settings.get("token_command")))
        return "аккаунт " + api.get("/user")["login"]
```

- [ ] **Step 4: Telegram**

В `brain/connectors/telegram.py` импорт: `from .base import ApiConnector, Batch, Field, Record, clip, first_line`.
В `TelegramConnector` после `empty_hint`:

```python
    setup_fields = (Field("channels", "Свои публичные каналы", kind="list", required=True,
                          help="имя, @имя или ссылка t.me/имя, несколько — через запятую"),)
    setup_help = ("Читается публичная страница t.me/s/<канал>: вход и ключи не нужны. "
                  "Закрытые каналы, «Избранное» и переписки так не читаются.")

    def check(self) -> str:
        specs = [c for c in self.settings.get("channels") or [] if str(c).strip()]
        if not specs:
            raise RuntimeError("Telegram: не указаны каналы")
        titles = []
        for spec in specs:
            name = channel_name(str(spec))
            data = self.page(name, None)
            if not data["posts"]:
                raise RuntimeError(f"Telegram: у @{name} не видно постов — канал закрыт или предпросмотр выключен")
            titles.append(data["title"] or "@" + name)
        return "каналы найдены: " + ", ".join(titles)
```

- [ ] **Step 5: Тесты зелёные**

Run: `/usr/bin/python3 -m unittest discover -s tests`
Expected: `OK` (194).

- [ ] **Step 6: Commit**

```bash
git add brain/connectors/github.py brain/connectors/telegram.py tests/test_brain.py
git commit -m "feat: настройка и проверка GitHub и Telegram для мастера

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Почта, Notion и YouTube — поля, секреты, проверка

**Files:**
- Modify: `brain/connectors/mail.py` (импорт, класс `MailConnector`: атрибуты, `_login`, `fetch`, `detect`, `check`)
- Modify: `brain/connectors/gmail.py`, `brain/connectors/yandex_mail.py` (`setup_help`)
- Modify: `brain/connectors/notion.py` (импорт, `NotionConnector`)
- Modify: `brain/connectors/youtube.py` (импорт, `YouTubeConnector`)
- Test: `tests/test_mail.py`, `tests/test_notion.py`, `tests/test_brain.py` (`YouTubeTests`)

**Interfaces:**
- Consumes: `Field`, `secret_ref` (Task 3), `secret_store.get/exists/add_command` (Task 1).
- Produces: у `MailConnector` — `setup_fields[1]` с `account_from="address"`, `secret_service = "brain-mail"`,
  `detect() -> address | None`, `check() -> "вход выполнен, папок: N"`; у `NotionConnector` — `secret_service = "brain-notion"`,
  `detect() -> "токен сохранён" | None`, `check() -> "токен работает[: имя]"`; у `YouTubeConnector` —
  `secret_service = "brain-youtube"`, `detect() -> "ключ API сохранён" | None`, `check() -> "каналы найдены: …"`.

- [ ] **Step 1: Написать падающие тесты**

В `tests/test_mail.py` (после `SecretReadTests`):

```python
class MailSetupTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg(Path(tempfile.mkdtemp()))
        self.cfg["sources"]["gmail"].update(address="me@gmail.com",
                                            password_command=[sys.executable, "-c", "print('app-pass')"])

    def test_check_logs_in_and_closes(self):
        box = mock.Mock()
        box.folders.return_value = ["INBOX", "Sent"]
        with mock.patch.object(GmailConnector, "open_mailbox", return_value=box) as opened:
            self.assertEqual(GmailConnector(self.cfg).check(), "вход выполнен, папок: 2")
        opened.assert_called_once_with("me@gmail.com", "app-pass")
        box.close.assert_called_once()

    def test_check_explains_wrong_password(self):
        with mock.patch.object(GmailConnector, "open_mailbox", side_effect=AuthFailed("no")):
            with self.assertRaises(RuntimeError) as ctx:
                GmailConnector(self.cfg).check()
        self.assertIn("пароль приложения", str(ctx.exception))

    def test_secret_lives_under_address(self):
        conn = GmailConnector(self.cfg)
        self.assertEqual(conn.secret_ref(conn.setup_fields[1]), ("brain-mail", "me@gmail.com"))
        with mock.patch("brain.secret_store.exists", return_value=True):
            self.assertEqual(conn.detect(), "me@gmail.com")
        self.assertIn("apppasswords", conn.setup_help)
        self.assertIn("IMAP", YandexMailConnector(self.cfg).setup_help)
```

В `tests/test_notion.py`, класс `NotionTests`:

```python
    def test_check_reports_token_owner(self):
        self.routes["GET", "/v1/users/me"] = {"object": "user", "name": "Рабочий токен"}
        self.assertEqual(notion.NotionConnector(self.cfg).check(), "токен работает: Рабочий токен")

    def test_check_explains_expired_token(self):
        self.routes["GET", "/v1/users/me"] = (401, {}, {"code": "unauthorized"})
        with self.assertRaises(RuntimeError) as ctx:
            notion.NotionConnector(self.cfg).check()
        self.assertIn("HTTP 401", str(ctx.exception))

    def test_detect_sees_saved_token(self):
        with mock.patch("brain.secret_store.exists", return_value=True):
            self.assertEqual(notion.NotionConnector(self.cfg).detect(), "токен сохранён")
        with mock.patch("brain.secret_store.exists", return_value=False):
            self.assertIsNone(notion.NotionConnector(self.cfg).detect())
```

В `tests/test_brain.py`, класс `YouTubeTests`:

```python
    def test_check_finds_channels(self):
        self.assertEqual(self.connector(self.cfg).check(), "каналы найдены: Мой канал")

    def test_check_names_missing_channel(self):
        self.cfg["sources"]["youtube"]["channels"] = ["@mine", "@nobody"]
        with self.assertRaises(RuntimeError) as ctx:
            self.connector(self.cfg).check()
        self.assertIn("@nobody", str(ctx.exception))

    def test_detect_sees_saved_key(self):
        from unittest import mock
        with mock.patch("brain.secret_store.exists", return_value=True):
            self.assertEqual(self.connector(self.cfg).detect(), "ключ API сохранён")
```

- [ ] **Step 2: Убедиться, что падают**

Run: `/usr/bin/python3 -m unittest discover -s tests -v 2>&1 | grep -E "FAIL|ERROR" | head`
Expected: падают новые тесты почты, Notion и YouTube (`'настройка не требуется' != …`, нет `setup_fields`).

- [ ] **Step 3: Почта**

В `brain/connectors/mail.py`: `from .base import ApiConnector, Batch, Field, Record`. В `MailConnector` после `auth_hint = ""`:

```python
    setup_fields = (Field("address", "Адрес почты", required=True),
                    Field("password", "Пароль приложения", kind="secret", account_from="address",
                          help="не пароль от почты, а отдельный пароль приложения"))
    secret_service = KEYCHAIN_SERVICE

    def _login(self):
        """Вход в ящик с паролем из связки ключей; любая неудача — RuntimeError с понятной причиной."""
        address = self.settings.get("address") or ""
        if not address:
            raise RuntimeError(f"{self.title}: в config.json не задан address")
        try:
            secret = secret_store.get(*self.secret_ref(self.setup_fields[1]), self.settings.get("password_command"))
            return self.open_mailbox(address, secret)
        except AuthFailed:
            raise RuntimeError(f"{self.title}: почта не пускает — проверь пароль приложения{self.auth_hint}") from None
        except RuntimeError as exc:
            raise RuntimeError(f"{self.title}: {exc}") from None
        except OSError as exc:
            raise RuntimeError(f"{self.title}: нет связи ({exc})") from None
        except imaplib.IMAP4.error as exc:  # сервер оборвал разговор ещё до входа
            raise RuntimeError(f"{self.title}: ошибка IMAP ({exc})") from None

    def detect(self) -> Optional[str]:
        address = self.settings.get("address") or ""
        if address and secret_store.exists(*self.secret_ref(self.setup_fields[1])):
            return address
        return None

    def check(self) -> str:
        box = self._login()
        try:
            folders = box.folders()
        except (OSError, imaplib.IMAP4.error) as exc:
            raise RuntimeError(f"{self.title}: ошибка IMAP ({exc})") from None
        finally:
            box.close()
        return f"вход выполнен, папок: {len(folders)}"
```

В `fetch` всё от `address = self.settings.get("address") or ""` до конца первого `try/except` (вход) заменить на
`box = self._login()`; дальнейший `try: yield from self._pass(box, cursor) … finally: box.close(); self.close()` не менять.

В `GmailConnector` после `gmail = True`:

```python
    setup_help = ("Нужен пароль приложения: myaccount.google.com/apppasswords (сначала включи двухэтапную "
                  "аутентификацию). В базу идут только треды с твоим письмом и помеченные письма за 2 года.")
```

В `YandexMailConnector` после `auth_hint`:

```python
    setup_help = ("Нужен пароль приложения: Яндекс ID → «Безопасность» → «Пароли приложений» → «Почта». "
                  "В настройках почты → «Почтовые программы» включи IMAP и пароли приложений. "
                  "В базу идут только треды с твоим письмом и помеченные письма за 2 года.")
```

- [ ] **Step 4: Notion**

В `brain/connectors/notion.py` импорт `from .base import …, Field, …` (добавить `Field` к существующему импорту из `.base`).
В `NotionConnector` после `empty_hint`:

```python
    setup_fields = (Field("token", "API-токен Notion", kind="secret"),)
    secret_service = "brain-notion"
    setup_help = ("Создай персональный токен: notion.so/developers/tokens → новый токен с возможностью Notion API "
                  "(на Free — владелец workspace). Код второго мозга только читает страницы. "
                  "Платная подписка не нужна.")

    def _token(self) -> str:
        service, account = self.secret_ref(self.setup_fields[0])
        try:
            return secret_store.get(service, account)
        except RuntimeError:
            raise RuntimeError("Notion: нет API-токена; создай его в Developer portal и сохрани командой `" +
                               secret_store.add_command(service, account) + "`") from None

    def detect(self) -> Optional[str]:
        return "токен сохранён" if secret_store.exists(*self.secret_ref(self.setup_fields[0])) else None

    def check(self) -> str:
        me = NotionAPI(self.settings.get("api_base") or API_BASE, self._token()).request("GET", "/users/me")
        name = me.get("name") or (me.get("bot") or {}).get("workspace_name") or ""
        return "токен работает" + (f": {name}" if name else "")
```

В `fetch` первые строки (от `service = …` до `from None`) заменить на `secret = self._token()`.

- [ ] **Step 5: YouTube**

В `brain/connectors/youtube.py` к импорту из `.base` добавить `Field`. В `YouTubeConnector` после `empty_hint`:

```python
    setup_fields = (Field("channels", "Свои каналы", kind="list", required=True,
                          help="@handle, ссылка на канал или UC…, несколько — через запятую"),
                    Field("api_key", "Ключ YouTube Data API", kind="secret"))
    secret_service = "brain-youtube"
    setup_help = ("Google Cloud Console → проект → включи «YouTube Data API v3» → «Учётные данные» → "
                  "создай ключ API и ограничь его этим API. Ключ даёт доступ только к публичным данным.")

    def _key(self) -> str:
        service, account = self.secret_ref(self.setup_fields[1])
        try:
            return secret_store.get(service, account, self.settings.get("key_command"))
        except RuntimeError:
            raise RuntimeError("YouTube: нет ключа API; создай его в Google Cloud и сохрани командой `"
                               + secret_store.add_command(service, account) + "`") from None

    def _channels(self) -> List[str]:
        channels = [str(c) for c in self.settings.get("channels") or [] if str(c).strip()]
        if not channels:
            raise RuntimeError("YouTube: не указаны каналы — добавь sources.youtube.channels в config.json")
        return channels

    def detect(self) -> Optional[str]:
        return "ключ API сохранён" if secret_store.exists(*self.secret_ref(self.setup_fields[1])) else None

    def check(self) -> str:
        api = YouTubeAPI(self.settings.get("api_base") or API_BASE, self._key())
        found, missing = [], []
        for spec in self._channels():
            channel = api.channel(spec)
            (found if channel else missing).append(channel["title"] if channel else spec)
        if missing:
            raise RuntimeError("YouTube: не найдены каналы " + ", ".join(missing))
        return "каналы найдены: " + ", ".join(found)
```

В `fetch` первые строки (от `channels = [c for c …` до `from None` после чтения ключа) заменить на:

```python
        channels = self._channels()
        key = self._key()
```

- [ ] **Step 6: Тесты зелёные**

Run: `/usr/bin/python3 -m unittest discover -s tests`
Expected: `OK` (203).

- [ ] **Step 7: Commit**

```bash
git add brain/connectors/mail.py brain/connectors/gmail.py brain/connectors/yandex_mail.py brain/connectors/notion.py brain/connectors/youtube.py tests/test_mail.py tests/test_notion.py tests/test_brain.py
git commit -m "feat: настройка и проверка почты, Notion и YouTube для мастера

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Ввод и вывод мастера `brain/ui.py`

**Files:**
- Create: `brain/ui.py`
- Test: `tests/test_setup.py`

**Interfaces:**
- Produces: `TextUi(inp: TextIO = None, out: TextIO = None, secret: Callable[[str], str] = None)` с методами
  `say(text="") -> None`, `ask(label, default="", help="") -> str`, `ask_secret(label) -> str`,
  `confirm(question, default: bool) -> bool`, `choose(question, options: Sequence[str], default=0) -> int`,
  `checklist(title, items: Sequence[Tuple[str, str]], checked: Set[int]) -> List[int]` (отсортированные индексы);
  при конце ввода — `EOFError`. `Checklist(items, checked)` с `.pos`, `.checked`, `.press(key: int) -> bool`, `.lines() -> List[str]`.
  `CursesUi(TextUi)` — свой `checklist` на curses. `make_ui() -> TextUi`.

- [ ] **Step 1: Написать падающие тесты** — в `tests/test_setup.py` (импорт `import curses` и `from brain import ui`):

```python
def text_ui(*lines, secrets=()):
    answers = iter(secrets)
    out = io.StringIO()
    return ui.TextUi(io.StringIO("".join(line + "\n" for line in lines)), out, secret=lambda _: next(answers)), out


class TextUiTests(unittest.TestCase):
    def test_ask_keeps_default_on_enter(self):
        term, _ = text_ui("", "новое")
        self.assertEqual(term.ask("Адрес", default="a@b.c"), "a@b.c")
        self.assertEqual(term.ask("Адрес", default="a@b.c"), "новое")

    def test_confirm_understands_russian_and_english(self):
        term, out = text_ui("", "н", "y", "может", "да")
        self.assertTrue(term.confirm("Включить?", True))
        self.assertFalse(term.confirm("Включить?", True))
        self.assertTrue(term.confirm("Включить?", False))
        self.assertTrue(term.confirm("Включить?", False))
        self.assertIn("Ответь «д» или «н»", out.getvalue())

    def test_choose_returns_index(self):
        term, _ = text_ui("7", "2", "")
        self.assertEqual(term.choose("Что?", ["а", "б", "в"]), 1)
        self.assertEqual(term.choose("Что?", ["а", "б", "в"], default=2), 2)

    def test_checklist_toggles_numbers_until_enter(self):
        term, out = text_ui("2 3", "3", "")
        chosen = term.checklist("Источники", [("A", "найдено"), ("B", ""), ("C", "")], {0})
        self.assertEqual(chosen, [0, 1])
        self.assertIn("[x]  1. A", out.getvalue())

    def test_secret_goes_through_secret_reader(self):
        term, out = text_ui(secrets=["  tok-1 "])
        self.assertEqual(term.ask_secret("Токен"), "tok-1")
        self.assertNotIn("tok-1", out.getvalue())

    def test_end_of_input_stops(self):
        term, _ = text_ui()
        with self.assertRaises(EOFError):
            term.ask("Адрес")


class ChecklistTests(unittest.TestCase):
    def test_keys_move_toggle_and_finish(self):
        state = ui.Checklist([("A", ""), ("B", "x")], {0})
        self.assertFalse(state.press(curses.KEY_DOWN))
        self.assertFalse(state.press(ord(" ")))
        self.assertEqual(state.checked, {0, 1})
        self.assertFalse(state.press(curses.KEY_DOWN))  # по кругу наверх
        self.assertEqual(state.pos, 0)
        self.assertTrue(state.press(10))
        self.assertEqual(state.lines()[0], "> [x] A")
```

- [ ] **Step 2: Убедиться, что падают**

Run: `/usr/bin/python3 -m unittest discover -s tests -p test_setup.py -v`
Expected: `ImportError: cannot import name 'ui'`.

- [ ] **Step 3: Написать `brain/ui.py`**

```python
"""Ввод и вывод мастера настройки: список с галочками и простые вопросы.

TextUi работает в любом терминале и в тестах (потоки подставляются). CursesUi добавляет полноэкранный
список с галочками; если curses не справился (маленькое окно, странный терминал), список задаётся вопросами.
"""
from __future__ import annotations

import curses
import getpass
import locale
import os
import sys
from typing import Callable, List, Sequence, Set, TextIO, Tuple

YES = ("д", "да", "y", "yes")
NO = ("н", "нет", "n", "no")
Item = Tuple[str, str]  # название и подсказка справа


class TextUi:
    def __init__(self, inp: TextIO = None, out: TextIO = None, secret: Callable[[str], str] = None):
        self.inp = inp or sys.stdin
        self.out = out or sys.stdout
        self.secret = secret or getpass.getpass

    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def _line(self, prompt: str) -> str:
        self.out.write(prompt)
        self.out.flush()
        line = self.inp.readline()
        if not line:
            raise EOFError("ввод закончился")
        return line.rstrip("\n")

    def ask(self, label: str, default: str = "", help: str = "") -> str:
        if help:
            self.say(f"  {help}")
        shown = f" [{default}]" if default else ""
        return self._line(f"{label}{shown}: ").strip() or default

    def ask_secret(self, label: str) -> str:
        return self.secret(f"{label} (ввод скрыт): ").strip()

    def confirm(self, question: str, default: bool) -> bool:
        hint = "[Д/н]" if default else "[д/Н]"
        while True:
            answer = self._line(f"{question} {hint} ").strip().lower()
            if not answer:
                return default
            if answer in YES:
                return True
            if answer in NO:
                return False
            self.say("Ответь «д» или «н».")

    def choose(self, question: str, options: Sequence[str], default: int = 0) -> int:
        self.say(question)
        for number, option in enumerate(options, 1):
            self.say(f"  {number}. {option}")
        while True:
            answer = self._line(f"Номер [{default + 1}]: ").strip()
            if not answer:
                return default
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return int(answer) - 1
            self.say(f"Введи номер от 1 до {len(options)}.")

    def checklist(self, title: str, items: Sequence[Item], checked: Set[int]) -> List[int]:
        state = set(checked)
        while True:
            self.say(title)
            for number, (name, hint) in enumerate(items, 1):
                mark = "x" if number - 1 in state else " "
                self.say(f"  [{mark}] {number:>2}. {name:<16} {hint}".rstrip())
            answer = self._line("Номера через пробел — переключить, Enter — дальше: ").strip()
            if not answer:
                return sorted(state)
            for part in answer.replace(",", " ").split():
                if part.isdigit() and 1 <= int(part) <= len(items):
                    state ^= {int(part) - 1}
                else:
                    self.say(f"Нет пункта «{part}».")


class Checklist:
    """Состояние списка с галочками отдельно от curses — его проверяют тесты."""

    def __init__(self, items: Sequence[Item], checked: Set[int]):
        self.items = list(items)
        self.checked = set(checked)
        self.pos = 0

    def press(self, key: int) -> bool:
        """Обработать клавишу; True — выбор закончен."""
        if key in (curses.KEY_UP, ord("k")):
            self.pos = (self.pos - 1) % len(self.items)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.pos = (self.pos + 1) % len(self.items)
        elif key == ord(" "):
            self.checked ^= {self.pos}
        elif key in (curses.KEY_ENTER, 10, 13):
            return True
        return False

    def lines(self) -> List[str]:
        return [f"{'>' if i == self.pos else ' '} [{'x' if i in self.checked else ' '}] {name:<16} {hint}".rstrip()
                for i, (name, hint) in enumerate(self.items)]


class CursesUi(TextUi):
    def checklist(self, title: str, items: Sequence[Item], checked: Set[int]) -> List[int]:
        state = Checklist(items, checked)
        try:
            curses.wrapper(self._draw, title, state)
        except curses.error:
            return super().checklist(title, items, checked)
        return sorted(state.checked)

    @staticmethod
    def _draw(screen, title: str, state: Checklist) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass  # терминал не умеет прятать курсор — не страшно
        while True:
            screen.erase()
            width = screen.getmaxyx()[1] - 1
            screen.addstr(0, 0, title[:width], curses.A_BOLD)
            screen.addstr(1, 0, "↑↓ — выбрать, пробел — вкл/выкл, Enter — дальше"[:width])
            for row, line in enumerate(state.lines()):
                screen.addstr(row + 3, 0, line[:width], curses.A_REVERSE if row == state.pos else 0)
            screen.refresh()
            if state.press(screen.getch()):
                return


def make_ui() -> TextUi:
    """Полноэкранный список, если это настоящий терминал; иначе — простые вопросы."""
    if sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM", "dumb") != "dumb":
        locale.setlocale(locale.LC_ALL, "")  # иначе curses не выведет кириллицу
        return CursesUi()
    return TextUi()
```

- [ ] **Step 4: Тесты зелёные**

Run: `/usr/bin/python3 -m unittest discover -s tests`
Expected: `OK` (210).

- [ ] **Step 5: Посмотреть список с галочками глазами**

```bash
/usr/bin/python3 -c "
import sys; sys.path.insert(0, '.')
from brain import ui
print(ui.make_ui().checklist('Источники', [('Claude Code', '412 разговоров'), ('GitHub', 'вход в gh выполнен'), ('Notion', 'нужно настроить')], {0}))"
```

Expected: полноэкранный список, кириллица читается, стрелки и пробел работают, Enter печатает выбранные индексы.
Сжать окно терминала до 3 строк и повторить — список должен перейти на вопросы.

- [ ] **Step 6: Commit**

```bash
git add brain/ui.py tests/test_setup.py
git commit -m "feat: ввод мастера — список с галочками на curses и простые вопросы

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Автозапуск без личных путей и команды `autostart`, `update`

**Files:**
- Create: `brain/autostart.py`
- Delete: `launchd/com.vladilen.brain-sync.plist` (папка `launchd/` исчезает)
- Modify: `brain/cli.py` (новые `cmd_autostart`, `cmd_update`, парсеры)
- Test: `tests/test_setup.py`

**Interfaces:**
- Produces: `autostart.LABEL = "local.brain.sync"`, `autostart.LEGACY_LABELS`, module-level `LAUNCHCTL`, `AGENTS`, `LOG`
  (тесты подменяют); `autostart.render(root: Path = config.ROOT, log: Path = None) -> bytes`;
  `autostart.enable(root: Path = config.ROOT) -> str` (RuntimeError, если launchd не принял);
  `autostart.disable() -> str`; `autostart.enabled() -> bool`; `autostart.status() -> str`.
  CLI: `brain autostart on|off|status`, `brain update`.

- [ ] **Step 1: Написать падающие тесты** — в `tests/test_setup.py` (импорты `import plistlib`, `from contextlib import redirect_stdout`,
  `from brain import autostart, cli`):

```python
class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.calls = self.tmp / "calls"
        fake = fake_command(self.tmp, f'echo "$@" >> "{self.calls}"\n[ "$1" = print ] && exit "${{FAKE_PRINT_RC:-1}}"\nexit 0\n')
        for name, value in (("LAUNCHCTL", str(fake)), ("AGENTS", self.tmp / "agents"), ("LOG", self.tmp / "logs" / "sync.log")):
            patcher = mock.patch.object(autostart, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def log(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_enable_writes_plist_with_this_installation_paths(self):
        autostart.enable(Path("/opt/brain"))
        data = plistlib.loads((self.tmp / "agents" / "local.brain.sync.plist").read_bytes())
        self.assertEqual(data["Label"], "local.brain.sync")
        self.assertEqual(data["ProgramArguments"], ["/opt/brain/bin/brain", "sync"])
        self.assertEqual(data["StandardOutPath"], str(self.tmp / "logs" / "sync.log"))
        self.assertEqual([d["Minute"] for d in data["StartCalendarInterval"]], [0, 15, 30, 45])
        uid = os.getuid()
        self.assertIn(f"bootstrap gui/{uid} {self.tmp}/agents/local.brain.sync.plist", self.log())

    def test_enable_removes_legacy_agent(self):
        legacy = self.tmp / "agents" / "com.vladilen.brain-sync.plist"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("старый")
        autostart.enable(Path("/opt/brain"))
        self.assertFalse(legacy.exists())
        self.assertIn(f"bootout gui/{os.getuid()}/com.vladilen.brain-sync", self.log())

    def test_disable_and_status(self):
        autostart.enable(Path("/opt/brain"))
        autostart.disable()
        self.assertFalse((self.tmp / "agents" / "local.brain.sync.plist").exists())
        self.assertEqual(autostart.status(), "автозапуск выключен")
        with mock.patch.dict(os.environ, {"FAKE_PRINT_RC": "0"}):
            self.assertTrue(autostart.enabled())
            self.assertIn("каждые 15 минут", autostart.status())

    def test_enable_reports_launchd_refusal(self):
        refuse = fake_command(self.tmp, '[ "$1" = bootstrap ] && { echo "Bootstrap failed: 5" >&2; exit 5; }\nexit 0\n')
        with mock.patch.object(autostart, "LAUNCHCTL", str(refuse)):
            with self.assertRaises(RuntimeError) as ctx:
                autostart.enable(Path("/opt/brain"))
        self.assertIn("launchctl bootstrap", str(ctx.exception))


class CliCommandTests(unittest.TestCase):
    def test_update_needs_git_checkout(self):
        out = io.StringIO()
        with mock.patch.object(config, "ROOT", Path(tempfile.mkdtemp())), redirect_stdout(out):
            self.assertEqual(cli.main(["update"]), 1)
        self.assertIn("не через git", out.getvalue())

    def test_autostart_status_command(self):
        out = io.StringIO()
        with mock.patch.object(autostart, "enabled", return_value=False), redirect_stdout(out):
            self.assertEqual(cli.main(["autostart", "status"]), 0)
        self.assertIn("выключен", out.getvalue())
```

Заметка: `fake_command` пишет в файл `fake`, поэтому в `test_enable_reports_launchd_refusal` второй вызов
перезаписывает его — это нормально, `LAUNCHCTL` подменён явно.

- [ ] **Step 2: Убедиться, что падают**

Run: `/usr/bin/python3 -m unittest discover -s tests -p test_setup.py -v`
Expected: `ImportError: cannot import name 'autostart'`.

- [ ] **Step 3: Написать `brain/autostart.py`**

```python
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
```

- [ ] **Step 4: Команды в `brain/cli.py`**

После `cmd_model`:

```python
def cmd_autostart(args, cfg) -> int:
    from . import autostart
    try:
        print({"on": autostart.enable, "off": autostart.disable, "status": autostart.status}[args.action]())
    except RuntimeError as exc:
        print(f"Не получилось: {exc}")
        return 1
    return 0


def cmd_update(args, cfg) -> int:
    import subprocess
    from . import autostart
    root = config.ROOT
    if not (root / ".git").exists():
        print(f"{root} поставлен не через git: обнови его командой установки из README.")
        return 1
    out = subprocess.run(["git", "-C", str(root), "pull", "--ff-only"], capture_output=True, text=True)
    print((out.stdout + out.stderr).strip())
    if out.returncode != 0:
        print("Не обновилось: в папке есть свои правки или ветка разошлась. Данные не тронуты.")
        return 1
    if autostart.enabled():
        autostart.enable(root)  # пути могли поменяться вместе с кодом
    print("Готово. Перезапусти Claude Code, Codex и другие MCP-клиенты, чтобы они подхватили новые инструменты.")
    return 0
```

В `build_parser` перед `return p`:

```python
    s = sub.add_parser("autostart", help="фоновая синхронизация каждые 15 минут: on, off, status")
    s.add_argument("action", choices=("on", "off", "status"))
    s.set_defaults(func=cmd_autostart)

    s = sub.add_parser("update", help="обновить второй мозг (git pull) без потери данных")
    s.set_defaults(func=cmd_update)
```

- [ ] **Step 5: Удалить шаблон с личными путями**

```bash
git rm -q launchd/com.vladilen.brain-sync.plist
```

- [ ] **Step 6: Тесты зелёные**

Run: `/usr/bin/python3 -m unittest discover -s tests`
Expected: `OK` (216).

- [ ] **Step 7: Commit**

```bash
git add brain/autostart.py brain/cli.py tests/test_setup.py
git commit -m "feat: автозапуск с путями установки, команды autostart и update

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Автозапуск автора в этой задаче не трогать: переход (`brain autostart on`) — в Task 12, после согласия.

---

### Task 8: Мастер `brain setup`

**Files:**
- Create: `brain/wizard.py`
- Modify: `brain/cli.py` (`cmd_setup`, парсер)
- Test: `tests/test_setup.py`

**Interfaces:**
- Consumes: `ui.TextUi` (Task 6), `Field`, `Connector.setup_fields/setup_help/secret_ref/detect/check` (Task 3–5),
  `secret_store.exists/put/add_command` (Task 1), `config.load/read_raw/save_sources/config_path/ROOT` (Task 2),
  `llm_settings.PROVIDERS/describe/models/save`, `autostart.enable/disable/enabled` (Task 7).
- Produces: `wizard.Actions` (`enable_autostart() -> str`, `disable_autostart() -> str`, `autostart_enabled() -> bool`,
  `build_menubar() -> None`, `first_sync() -> None`); `wizard.split_list(text) -> List[str]`;
  `wizard.configure_source(ui, cfg, name) -> Optional[dict]`; `wizard.summary(cfg) -> str`;
  `wizard.run(ui, only: Optional[str] = None, actions: Optional[Actions] = None) -> int`. CLI: `brain setup [источник]`.

- [ ] **Step 1: Написать падающие тесты** — в `tests/test_setup.py` (импорт `from brain import connectors, wizard`
  и `from brain.connectors.base import Connector, Field` уже есть):

```python
class FakeFiles(Connector):
    name = source = "fake_files"
    title = "Файлы"

    def detect(self):
        return "3 разговора"

    def check(self):
        return "3 разговора"


class FakeToken(Connector):
    name = source = "fake_token"
    title = "Сервис"
    setup_help = "Возьми токен на example.com"
    secret_service = "brain-fake"
    setup_fields = (Field("channels", "Каналы", kind="list", required=True), Field("token", "Токен", kind="secret"))
    results: list = []  # что вернёт check по очереди: строка — успех, исключение — ошибка

    def check(self):
        result = FakeToken.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeActions:
    def __init__(self):
        self.calls = []

    def enable_autostart(self):
        self.calls.append("autostart")
        return "включён"

    def disable_autostart(self):
        self.calls.append("autostart off")
        return "выключен"

    def autostart_enabled(self):
        return False

    def build_menubar(self):
        self.calls.append("menubar")

    def first_sync(self):
        self.calls.append("sync")


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "config.json"
        self.secrets = {}
        for patcher in (
            mock.patch.dict(os.environ, {"BRAIN_CONFIG": str(self.path), "BRAIN_LLM_API_KEY": "",
                                         "BRAIN_DB": str(self.dir / "brain.db")}),
            mock.patch.dict(connectors.ALL, {"fake_files": FakeFiles, "fake_token": FakeToken}, clear=True),
            mock.patch.object(wizard.secret_store, "exists", lambda s, a: (s, a) in self.secrets),
            mock.patch.object(wizard.secret_store, "put",
                              lambda s, a, v: self.secrets.__setitem__((s, a), secret_store.check_secret(v))),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        FakeToken.results = []
        self.actions = FakeActions()

    def stored(self):
        return json.loads(self.path.read_text())["sources"]

    def test_first_run_full_pass(self):
        FakeToken.results = [RuntimeError("нет доступа"), "ок"]
        term, out = text_ui(
            "2", "",          # список: включить «Сервис», дальше
            "a, b",           # каналы
            "1",              # проверка упала → попробовать снова
            "",               # каналы — Enter оставляет «a, b»
            "н",              # токен уже сохранён — не заменять
            "",               # модель: пропустить (по умолчанию)
            "", "", "н",      # автозапуск — да, значок — нет, забрать сейчас — нет
            secrets=["tok-123"])
        self.assertEqual(wizard.run(term, actions=self.actions), 0)
        self.assertEqual(self.stored(), {"fake_files": {"enabled": True},
                                         "fake_token": {"channels": ["a", "b"], "enabled": True}})
        self.assertEqual(self.secrets, {("brain-fake", "default"): "tok-123"})
        self.assertEqual(self.actions.calls, ["autostart"])
        text = out.getvalue()
        self.assertIn("Возьми токен на example.com", text)
        self.assertIn("✗ нет доступа", text)
        self.assertIn("✓ ок", text)
        self.assertIn("claude mcp add -s user brain --", text)
        self.assertNotIn("tok-123", text)

    def test_unchecked_and_skipped_sources_are_disabled(self):
        self.path.write_text(json.dumps({"sources": {"fake_files": {"enabled": True}, "fake_token": {"enabled": True}}}))
        FakeToken.results = [RuntimeError("нет доступа")]
        term, _ = text_ui("1", "", "x", "2", "", "н", "", "н", secrets=["tok"])
        wizard.run(term, actions=self.actions)
        self.assertEqual(self.stored()["fake_files"], {"enabled": False})
        self.assertEqual(self.stored()["fake_token"], {"enabled": False})

    def test_force_enable_after_failed_check(self):
        FakeToken.results = [RuntimeError("нет сети")]
        term, _ = text_ui("x", "3", secrets=["tok"])
        self.assertEqual(wizard.run(term, only="fake_token", actions=self.actions), 0)
        self.assertEqual(self.stored()["fake_token"], {"channels": ["x"], "enabled": True})

    def test_required_field_is_asked_again(self):
        FakeToken.results = ["ок"]
        term, out = text_ui("", "y", secrets=["tok"])
        wizard.run(term, only="fake_token", actions=self.actions)
        self.assertIn("Без этого источник не заработает", out.getvalue())
        self.assertEqual(self.stored()["fake_token"]["channels"], ["y"])

    def test_bad_secret_is_asked_again(self):
        FakeToken.results = ["ок"]
        term, out = text_ui("x", secrets=["пароль", "tok"])
        wizard.run(term, only="fake_token", actions=self.actions)
        self.assertIn("не подошло", out.getvalue())
        self.assertEqual(self.secrets, {("brain-fake", "default"): "tok"})

    def test_unknown_source(self):
        term, out = text_ui()
        self.assertEqual(wizard.run(term, only="nope", actions=self.actions), 1)
        self.assertIn("fake_files", out.getvalue())

    def test_split_list(self):
        self.assertEqual(wizard.split_list(" a, b ,,c\nd "), ["a", "b", "c", "d"])
```

- [ ] **Step 2: Убедиться, что падают**

Run: `/usr/bin/python3 -m unittest discover -s tests -p test_setup.py -v`
Expected: `ImportError: cannot import name 'wizard'`.

- [ ] **Step 3: Написать `brain/wizard.py`**

```python
"""Мастер настройки: brain setup [источник].

Знает только общий контракт коннектора (setup_fields, setup_help, secret_ref, detect, check), поэтому новый
источник попадает в мастер сам. Конфиг пишется по шагам: после источников, после модели — прерванная
настройка оставляет пройденные шаги.
"""
from __future__ import annotations

import copy
import subprocess
from typing import Dict, List, Optional

from . import autostart, config, connectors, llm_settings, secret_store
from .connectors.base import home_relative

INTRO = ("Второй мозг — настройка.\n"
         "Отметь источники, из которых собирать историю работы. Всё хранится у тебя на компьютере.")
RETRY, SKIP, FORCE = 0, 1, 2


class Actions:
    """Действия с последствиями вне конфига; тесты подменяют их записью вызовов."""

    def enable_autostart(self) -> str:
        return autostart.enable()

    def disable_autostart(self) -> str:
        return autostart.disable()

    def autostart_enabled(self) -> bool:
        return autostart.enabled()

    def build_menubar(self) -> None:
        subprocess.run([str(config.ROOT / "menubar" / "build.sh")], check=False)

    def first_sync(self) -> None:
        subprocess.run([str(config.ROOT / "bin" / "brain"), "sync", "--no-summarize"], check=False)


def split_list(text: str) -> List[str]:
    return [part.strip() for part in text.replace("\n", ",").split(",") if part.strip()]


def pick_sources(ui, cfg: dict, first_run: bool) -> List[str]:
    """Список с галочками. Первый раз отмечено найденное на компьютере, потом — включённое в конфиге."""
    classes = list(connectors.ALL.values())
    items, checked = [], set()
    for index, cls in enumerate(classes):
        try:
            found = cls(cfg).detect()
        except Exception:  # поиск — подсказка, его ошибка не должна ронять мастер
            found = None
        items.append((cls.title, found or ("нужно настроить" if cls.setup_fields else "не найдено")))
        if (bool(found) if first_run else cfg["sources"].get(cls.name, {}).get("enabled")):
            checked.add(index)
    chosen = ui.checklist("Какие источники подключить", items, checked)
    return [classes[index].name for index in chosen]


def ask_fields(ui, cls, settings: dict) -> dict:
    values = {}
    for field in cls.setup_fields:
        if field.kind == "secret":
            continue
        current = settings.get(field.key)
        default = ", ".join(current or []) if field.kind == "list" else str(current or "")
        while True:
            answer = ui.ask(field.label, default=default, help=field.help).strip()
            if answer or not field.required:
                break
            ui.say("Без этого источник не заработает.")
        values[field.key] = split_list(answer) if field.kind == "list" else answer
    return values


def ask_secrets(ui, conn) -> None:
    for field in conn.setup_fields:
        if field.kind != "secret":
            continue
        service, account = conn.secret_ref(field)
        if secret_store.exists(service, account) and not ui.confirm(f"{field.label}: уже сохранён. Заменить?", False):
            continue
        if field.help:
            ui.say(f"  {field.help}")
        while True:
            try:
                secret_store.put(service, account, ui.ask_secret(field.label))
                ui.say("  сохранено в связке ключей")
                break
            except ValueError as exc:
                ui.say(f"  не подошло: {exc}")
            except RuntimeError as exc:
                ui.say(f"  {exc}\n  Сохрани вручную: {secret_store.add_command(service, account)}")
                break


def configure_source(ui, cfg: dict, name: str) -> Optional[dict]:
    """Спросить поля и секреты, проверить подключение. None — источник пропущен."""
    cls = connectors.ALL[name]
    ui.say(f"\n— {cls.title}")
    if cls.setup_help:
        ui.say(cls.setup_help)
    values = ask_fields(ui, cls, cfg["sources"].get(name, {}))
    trial = copy.deepcopy(cfg)
    trial["sources"].setdefault(name, {}).update(values)
    conn = cls(trial)
    ask_secrets(ui, conn)
    ui.say("  проверяю подключение…")
    try:
        ui.say(f"  ✓ {conn.check()}")
    except Exception as exc:  # причину называет коннектор; мастер не падает
        ui.say(f"  ✗ {exc}")
        choice = ui.choose("Что дальше?", ["Попробовать снова", "Пропустить источник", "Включить всё равно"])
        if choice == RETRY:
            return configure_source(ui, trial, name)
        if choice == SKIP:
            return None
    return dict(values, enabled=True)


def configure_model(ui) -> None:
    """Равный список провайдеров, как в BrainBar; шаг можно пропустить."""
    ui.say("\n— Модель для пересказа\n"
           "Пересказ делает карточки, решения и «что нового». Без модели поиск идёт по сырому тексту. "
           "С моделью текст записей уходит выбранному провайдеру.")
    current = llm_settings.describe()["current"]
    if current.get("model"):
        ui.say(f"Сейчас: {current['provider']} · {current['model']}")
    names = [name for name, _, _ in llm_settings.PROVIDERS] + ["Свой адрес (OpenAI-совместимый API)", "Пропустить"]
    choice = ui.choose("Кто пересказывает записи?", names, default=len(names) - 1)
    if choice == len(names) - 1:
        return
    if choice < len(llm_settings.PROVIDERS):
        base_url = llm_settings.PROVIDERS[choice][1]
    else:
        base_url = ui.ask("Адрес API (например http://localhost:1234/v1)")
    payload = {"base_url": base_url}
    key = ui.ask_secret("API-ключ (Enter — оставить сохранённый или без ключа)")
    if key:
        payload["api_key"] = key
    try:
        found = llm_settings.models(payload)["models"]
        if found:
            ui.say(f"Модели провайдера ({len(found)}): " + ", ".join(found[:20]) + (" …" if len(found) > 20 else ""))
    except (RuntimeError, ValueError) as exc:
        ui.say(f"Список моделей не получен: {exc}")
    payload["model"] = ui.ask("Название модели")
    try:
        llm_settings.save(payload)
        ui.say("  ✓ модель сохранена")
    except (ValueError, OSError) as exc:
        ui.say(f"  ✗ не сохранено: {exc}. Позже: brain setup или BrainBar → Настройки")


def summary(cfg: dict) -> str:
    brain = config.ROOT / "bin" / "brain"
    on = [cls.title for name, cls in connectors.ALL.items() if cfg["sources"].get(name, {}).get("enabled")]
    return "\n".join([
        "",
        f"Готово. Подключено: {', '.join(on) or 'ничего'}.",
        "",
        "Где всё лежит:",
        f"  база        {home_relative(cfg['db_path'])}",
        f"  настройки   {home_relative(str(config.config_path()))}",
        "  ключи       связка ключей macOS (сервисы brain-mail, brain-notion, brain-youtube)",
        "  журнал      ~/Library/Logs/brain-sync.log",
        "",
        "Подключить к нейронкам (MCP):",
        f"  Claude Code   claude mcp add -s user brain -- {brain} mcp",
        f"  Codex         codex mcp add brain -- {brain} mcp",
        "  Claude Desktop, Cursor и другие — README, раздел «Подключение к нейронкам».",
        "",
        "Дальше: brain search \"запрос\", brain recent, brain dashboard --open",
    ])


def run(ui, only: Optional[str] = None, actions: Optional[Actions] = None) -> int:
    actions = actions or Actions()
    cfg = config.load()
    if only:
        if only not in connectors.ALL:
            ui.say(f"Нет источника «{only}». Есть: {', '.join(connectors.ALL)}")
            return 1
        values = configure_source(ui, cfg, only)
        if values is None:
            ui.say("Ничего не изменено.")
            return 0
        config.save_sources({only: values})
        ui.say(f"{connectors.ALL[only].title}: включён. Забрать сейчас: brain sync --source {only}")
        return 0

    ui.say(INTRO)
    first_run = "sources" not in config.read_raw()
    chosen = pick_sources(ui, cfg, first_run)
    changes: Dict[str, dict] = {name: {"enabled": False} for name in connectors.ALL if name not in chosen}
    for name in chosen:
        changes[name] = configure_source(ui, cfg, name) or {"enabled": False}
    config.save_sources(changes)

    configure_model(ui)

    if ui.confirm("\nСинхронизировать в фоне каждые 15 минут?", True):
        try:
            ui.say("  ✓ " + actions.enable_autostart())
        except RuntimeError as exc:
            ui.say(f"  ✗ автозапуск не включён: {exc}")
    elif actions.autostart_enabled():
        ui.say("  " + actions.disable_autostart())
    if ui.confirm("Поставить значок в строке меню (собирается из исходников, около минуты)?", False):
        actions.build_menubar()

    cfg = config.load()
    if ui.confirm("Забрать данные сейчас? Первый раз GitHub и почта за 2 года — долго, Ctrl+C ничего не ломает.", True):
        try:
            actions.first_sync()
        except KeyboardInterrupt:
            ui.say("\nОстановлено. Забранное сохранено, остальное заберёт автозапуск.")
    ui.say(summary(cfg))
    return 0
```

- [ ] **Step 4: Команда в `brain/cli.py`**

После `cmd_update`:

```python
def cmd_setup(args, cfg) -> int:
    from . import ui, wizard
    try:
        return wizard.run(ui.make_ui(), only=args.source)
    except (KeyboardInterrupt, EOFError):
        print("\nНастройка прервана. Пройденные шаги сохранены; продолжить — brain setup.")
        return 130
```

В `build_parser` перед `autostart`:

```python
    s = sub.add_parser("setup", help="мастер настройки: источники, модель, автозапуск")
    s.add_argument("source", nargs="?", help="настроить один источник: " + ", ".join(connectors.ALL))
    s.set_defaults(func=cmd_setup)
```

Строка-описание модуля `brain/cli.py` (первая): `"""Командная строка: brain setup | sync | search | recent | project | show | status | summarize | mcp | autostart | update."""`.

- [ ] **Step 5: Тесты зелёные**

Run: `/usr/bin/python3 -m unittest discover -s tests`
Expected: `OK` (223).

- [ ] **Step 6: Прогнать мастер руками на временном конфиге**

```bash
BRAIN_CONFIG="$(mktemp -d)/config.json" BRAIN_DB="$(mktemp -d)/brain.db" bin/brain setup telegram
```

Ввести канал `durov`, проверить «✓ каналы найдены: …», что временный `config.json` содержит
`{"sources": {"telegram": {"channels": ["durov"], "enabled": true}}}` и права `-rw-------`. Настоящий `config.json` не меняется.

- [ ] **Step 7: Commit**

```bash
git add brain/wizard.py brain/cli.py tests/test_setup.py
git commit -m "feat: мастер brain setup — источники галочками, настройка с проверкой, модель, автозапуск

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Установка одной командой и `bin/brain` через ссылку

**Files:**
- Modify: `bin/brain`
- Create: `install.sh` (исполняемый)
- Test: `tests/test_install.py` (новый)

**Interfaces:**
- Consumes: `brain setup` (Task 8).
- Produces: `install.sh` с переменными `BRAIN_HOME` (по умолчанию `~/brain`), `BRAIN_REF` (`main`),
  `BRAIN_REPO` (адрес репозитория), `BRAIN_BIN_DIR` (`~/.local/bin`), `BRAIN_NO_TTY`, `BRAIN_NO_SETUP` (для тестов).

- [ ] **Step 1: Написать падающие тесты** — `tests/test_install.py`:

```python
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


class LauncherTests(unittest.TestCase):
    def test_brain_works_through_symlink(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "bin").mkdir()
        os.symlink(ROOT / "bin" / "brain", tmp / "bin" / "brain")
        result = subprocess.run([str(tmp / "bin" / "brain"), "--help"], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("setup", result.stdout)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Убедиться, что падают**

Run: `/usr/bin/python3 -m unittest discover -s tests -p test_install.py -v`
Expected: `InstallTests` — `can't open file install.sh`; `LauncherTests` — `No module named brain.cli`.

- [ ] **Step 3: `bin/brain` разворачивает ссылку**

```sh
#!/bin/sh
# brain setup | sync | search "запрос" | recent | project | show ID | status | mcp — все команды: brain --help
# Работает и через ссылку (~/.local/bin/brain): идём по ссылкам до настоящего файла. readlink -f в старом macOS нет.
SELF="$0"
while [ -L "$SELF" ]; do
    LINK="$(readlink "$SELF")"
    case "$LINK" in
        /*) SELF="$LINK" ;;
        *) SELF="$(dirname "$SELF")/$LINK" ;;
    esac
done
DIR="$(cd "$(dirname "$SELF")/.." && pwd)"
PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" exec /usr/bin/python3 -m brain.cli "$@"
```

- [ ] **Step 4: Написать `install.sh`**

```sh
#!/bin/sh
# Установка второго мозга одной командой:
#   curl -fsSL https://raw.githubusercontent.com/vladilenm/modern-second-brain/main/install.sh | sh
# Повторный запуск обновляет код; данные (data/, config.json, inbox/) не трогаются.
# BRAIN_HOME — куда ставить (~/brain), BRAIN_REF — ветка (main).
set -eu

REPO="${BRAIN_REPO:-https://github.com/vladilenm/modern-second-brain.git}"
REF="${BRAIN_REF:-main}"
TARGET="${BRAIN_HOME:-$HOME/brain}"
BIN_DIR="${BRAIN_BIN_DIR:-$HOME/.local/bin}"

say() { printf '%s\n' "$*"; }
fail() { printf 'Ошибка: %s\n' "$*" >&2; exit 1; }
# Скрипт приходит через pipe, поэтому вопросы задаём через /dev/tty.
tty_ok() { [ -z "${BRAIN_NO_TTY:-}" ] && (: </dev/tty) 2>/dev/null; }
ask_yes() {
    tty_ok || return 1
    printf '%s [Д/н] ' "$1" >/dev/tty
    read -r answer </dev/tty || answer=""
    case "$answer" in
        н*|Н*|n*|N*) return 1 ;;
        *) return 0 ;;
    esac
}

[ "$(uname -s)" = "Darwin" ] || fail "пока поддерживается только macOS"
xcode-select -p >/dev/null 2>&1 || fail "нужны Command Line Tools: выполни xcode-select --install, дождись установки и запусти эту команду снова"
/usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null || fail "нужен Python 3.9 или новее в /usr/bin/python3 (идёт с Command Line Tools)"

case "$TARGET" in
    "$HOME/Documents"|"$HOME/Documents/"*|"$HOME/Desktop"|"$HOME/Desktop/"*|"$HOME/Downloads"|"$HOME/Downloads/"*)
        fail "не ставь в Документы, Рабочий стол или Загрузки: macOS не пускает туда фоновую синхронизацию. Например: BRAIN_HOME=\$HOME/brain" ;;
esac

if [ -d "$TARGET/.git" ] && [ -f "$TARGET/brain/cli.py" ]; then
    say "Обновляю $TARGET"
    git -C "$TARGET" pull --ff-only --quiet
elif [ -e "$TARGET" ]; then
    fail "$TARGET уже есть и это не второй мозг — выбери другую папку: BRAIN_HOME=\$HOME/другая-папка"
else
    say "Скачиваю в $TARGET"
    PARENT="$(dirname "$TARGET")"
    mkdir -p "$PARENT"
    TMP="$(mktemp -d "$PARENT/.brain-install.XXXXXX")"
    trap 'rm -rf "$TMP"' EXIT
    git clone --quiet --depth 1 --branch "$REF" "$REPO" "$TMP/brain"
    mv "$TMP/brain" "$TARGET"
    rmdir "$TMP"
    trap - EXIT
fi

mkdir -p "$BIN_DIR"
ln -sf "$TARGET/bin/brain" "$BIN_DIR/brain"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        LINE="export PATH=\"$BIN_DIR:\$PATH\""
        if ask_yes "Добавить $BIN_DIR в PATH (строка в ~/.zprofile), чтобы команда brain работала из любой папки?"; then
            printf '\n# второй мозг: команда brain\n%s\n' "$LINE" >>"$HOME/.zprofile"
            say "Добавлено. В новых окнах терминала команда brain доступна."
        else
            say "Чтобы команда brain работала из любой папки, добавь в ~/.zprofile строку:"
            say "  $LINE"
        fi ;;
esac

say "Установлено: $TARGET"
if [ -z "${BRAIN_NO_SETUP:-}" ] && tty_ok; then
    exec "$TARGET/bin/brain" setup </dev/tty >/dev/tty
fi
say "Дальше: $TARGET/bin/brain setup"
```

```bash
chmod +x install.sh
```

- [ ] **Step 5: Тесты зелёные**

Run: `/usr/bin/python3 -m unittest discover -s tests`
Expected: `OK` (228).

- [ ] **Step 6: Commit**

```bash
git add bin/brain install.sh tests/test_install.py
git commit -m "feat: установка одной командой install.sh, brain работает через ссылку

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: BrainBar без личной метки и чистка личных путей

**Files:**
- Modify: `menubar/BrainBar.swift:1-12`
- Modify: `menubar/Info.plist` (`CFBundleIdentifier`)
- Modify: `menubar/build.sh` (вписать `BrainDir`)
- Modify: `brain/config.py:64` (комментарий с примером)
- Test: `tests/test_install.py` (класс `RepoHygieneTests`)

**Interfaces:**
- Consumes: `autostart.LABEL = "local.brain.sync"` (Task 7).
- Produces: ключ `BrainDir` в `Info.plist` собранного BrainBar.

- [ ] **Step 1: Написать падающий тест** — в `tests/test_install.py`:

```python
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
```

- [ ] **Step 2: Убедиться, что падает**

Run: `/usr/bin/python3 -m unittest discover -s tests -p test_install.py -k hygiene -v`
Expected: FAIL со списком `menubar/BrainBar.swift: com.vladilen`, `menubar/Info.plist: com.vladilen`, `README.md: /Users/vladilen`.
(README поправится в Task 11 — после этой задачи в списке должен остаться только он.)

- [ ] **Step 3: BrainBar читает путь из `Info.plist` и новую метку**

В `menubar/BrainBar.swift` строки 2 и 8–12:

```swift
// Раз в 2 секунды читает data/status.json в папке второго мозга (его пишет brain sync) и показывает,
```

```swift
let home = NSHomeDirectory()
/// Папку проекта вписывает menubar/build.sh в Info.plist (BrainDir); сборка без неё — ~/brain.
let brainDir: String = {
    if let dir = Bundle.main.object(forInfoDictionaryKey: "BrainDir") as? String, !dir.isEmpty { return dir }
    return home + "/brain"
}()
let statusPath = brainDir + "/data/status.json"
let logPath = home + "/Library/Logs/brain-sync.log"
let agentLabel = "local.brain.sync"  // brain/autostart.py, LABEL
```

В `menubar/Info.plist`: `<string>com.vladilen.brainbar</string>` → `<string>local.brain.brainbar</string>`.

В `menubar/build.sh` после `cp Info.plist "$BUILD/Contents/Info.plist"`:

```sh
# Путь к проекту — в Info.plist: значок работает, куда бы ни поставили второй мозг.
/usr/libexec/PlistBuddy -c "Add :BrainDir string $(cd .. && pwd)" "$BUILD/Contents/Info.plist"
```

- [ ] **Step 4: Нейтральный пример в `brain/config.py:64`**

`# Шаблоны owner/name, допускается *: ["vladilenm/tbank-*"].` → `# Шаблоны owner/name, допускается *: ["owner/tmp-*"].`

- [ ] **Step 5: Собрать BrainBar и прогнать его тесты**

Run: `menubar/build.sh --build-only && /usr/libexec/PlistBuddy -c "Print :BrainDir" menubar/build/BrainBar.app/Contents/Info.plist`
Expected: `собрано: …` и путь к проекту (`/Users/…/brain`).

Run: `/usr/bin/python3 -m unittest discover -s tests -p test_menubar.py -v`
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add menubar/BrainBar.swift menubar/Info.plist menubar/build.sh brain/config.py tests/test_install.py
git commit -m "feat: BrainBar берёт путь к проекту из сборки, без личных меток

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

`RepoHygieneTests` пока красный только из-за README — это ожидаемо, его закрывает Task 11.

---

### Task 11: Документация для нового человека

**Files:**
- Rewrite: `README.md`
- Create: `docs/sources.md`, `docs/development.md`
- Modify: `config.example.json`

**Interfaces:**
- Consumes: команды и пути из Task 7–10: `brain setup [источник]`, `brain autostart on|off|status`, `brain update`,
  `install.sh`, `~/.local/bin/brain`, метка `local.brain.sync`, `~/Library/Logs/brain-sync.log`.

- [ ] **Step 1: Сохранить старый README для переноса**

```bash
cp README.md /tmp/README.old.md
```

- [ ] **Step 2: `docs/sources.md`** — подробности по источникам из старого README.

Начало файла:

```markdown
# Источники

Каждый источник включается и настраивается мастером: `brain setup` (все) или `brain setup <имя>` (один).
Здесь — что именно забирается, как часто и какие параметры можно поменять в `config.json` руками.
Значения по умолчанию — в `brain/config.py`, пример — в `config.example.json`.

| Имя в config | Что | Что нужно |
|---|---|---|
| `claude_code` | разговоры Claude Code | ничего |
| `codex` | разговоры Codex | `zstd` для сжатых сессий: `brew install zstd` |
| `kimi` | разговоры Kimi Code / Kimi CLI | ничего |
| `chatgpt_export` | чаты ChatGPT | архив экспорта в `inbox/` |
| `github` | PR, issues, коммиты, README | `gh`, вход: `gh auth login` |
| `gmail`, `yandex_mail` | твои переписки и помеченные письма | пароль приложения |
| `notion` | страницы и записи баз | персональный API-токен |
| `youtube` | видео своих каналов, статистика, комментарии | ключ YouTube Data API |
| `telegram` | посты своих публичных каналов | имя канала |
```

Дальше — разделы старого README дословно, в этом порядке: «Codex», «Kimi Code / Kimi CLI», «ChatGPT», «GitHub»,
«Почта», «Notion», «YouTube», «Telegram», «Исключённые проекты», «Проекты», «Тихие источники»
(каждый `## …` → `## …`, текст без изменений), со следующими правками:

1. В «Почта» пункты 2 и 3 списка «Подключение» заменить на:

   ```markdown
   2. `brain setup gmail` (или `brain setup yandex_mail`): мастер спросит адрес и пароль приложения,
      положит пароль в связку ключей и проверит вход.

   Вручную, без мастера: `security add-generic-password -s brain-mail -a ты@gmail.com -w`
   и в `config.json` — `"sources": {"gmail": {"enabled": true, "address": "ты@gmail.com"}}`.
   ```

2. В «Notion» пункты 2 и 3 заменить на:

   ```markdown
   2. `brain setup notion`: мастер спросит токен, положит его в связку ключей и проверит доступ.

   Вручную: `security add-generic-password -U -s brain-notion -a default -w` и в `config.json` —
   `"sources": {"notion": {"enabled": true}}`.
   ```

3. В «YouTube» пункты 2–4 заменить на:

   ```markdown
   2. `brain setup youtube`: мастер спросит каналы (`@handle`, ссылка или `UC…`) и ключ, проверит, что каналы находятся.

   Вручную: `security add-generic-password -s brain-youtube -a default -w` и в `config.json` —
   `"sources": {"youtube": {"enabled": true, "channels": ["@первый-канал"]}}`.
   ```

4. Во всех перенесённых разделах `vladilenm/` в примерах → `owner/` (`owner/tbank-*`, `owner/club_landing`).
5. Команды `bin/brain …` → `brain …`.

- [ ] **Step 3: `docs/development.md`** — для тех, кто правит код.

Разделы старого README дословно: «Как устроено», «Пересказ (нужна модель)», «Значок в строке меню»,
«Панель базы», «Как это работает», «Тесты». Правки:

1. В «Как устроено» в блок файлов добавить строки (по алфавиту места не ищем — после `brain/keychain.py`,
   а саму строку `brain/keychain.py` заменить):

   ```
   brain/secret_store.py пароли, токены и ключи источников в связке ключей macOS (запись через stdin)
   brain/wizard.py     мастер brain setup: источники, настройка с проверкой, модель, автозапуск
   brain/ui.py         ввод мастера: список с галочками на curses и простые вопросы
   brain/autostart.py  фоновая синхронизация: агент launchd с путями этой установки
   install.sh          установка и обновление одной командой
   ```

2. В «Как это работает» `раз в 15 минут запускает launchd` оставить, добавить в конец абзаца:
   «Агент ставит `brain autostart on` (или мастер): метка `local.brain.sync`, plist собирается с путями этой установки».
3. Добавить раздел «Новый источник» (вместо старого):

   ```markdown
   ## Новый источник

   1. Файл в `brain/connectors/`:
      - файлы на диске — класс от `FileConnector` с методами `files()` и `parse(path) -> [Record]`;
      - источник за API — класс от `ApiConnector` с методом `fetch(cursor)`, который отдаёт `Batch(records, cursor)`.
        Курсор — строка в любом формате, её хранит sync и сдвигает вместе с записанной пачкой.
   2. У класса — `name` (ключ в config), `source` (ключ записей), `title` и `about` (как показывать людям и нейронке).
   3. Настройка для мастера:
      - `setup_fields` — что спросить: `Field(key, label, kind="text" | "list" | "secret", help, required, account_from)`.
        Секрет не пишется в config: он уходит в связку ключей под `secret_service` и аккаунтом
        `keychain_account` (по умолчанию `default`) или значением поля `account_from`;
      - `setup_help` — где взять ключ или пароль, со ссылкой;
      - `detect()` — что уже есть на компьютере одной строкой или `None`, быстро;
      - `check()` — пробное подключение до 20 секунд: строка об успехе или `RuntimeError` с причиной.
   4. Строка в `ALL` в `brain/connectors/__init__.py` и секция в `sources` в `brain/config.py`.

   Курсор, дубли, индекс, расписание, пересказ, свежесть и мастер — общие. Списки источников в CLI, MCP,
   панели, значке и мастере строятся из реестра сами.
   ```

4. Добавить раздел «Автозапуск»:

   ```markdown
   ## Автозапуск

   `brain autostart on` пишет `~/Library/LaunchAgents/local.brain.sync.plist` с путями этой установки и загружает
   агент; `off` — выгружает и удаляет; `status` — включён ли. `brain sync` идёт по часам — в :00, :15, :30 и :45
   (если Mac спал, пропущенный запуск выполнится при пробуждении), журнал — `~/Library/Logs/brain-sync.log`.
   Не `StartInterval` и не `ProcessType: Background`: с ними macOS откладывала запуски на десятки минут.
   Проект не должен лежать в «Документах», на «Рабочем столе» или в «Загрузках»: macOS не пускает туда фоновые
   задачи launchd — агент падает с «Operation not permitted». Установщик туда ставить не даёт.
   `brain autostart on` снимает агент прежней метки ранних версий, если он есть.
   ```

5. Команды `bin/brain …` → `brain …`.

- [ ] **Step 4: Новый `README.md`** — заменить целиком:

````markdown
# Второй мозг

Вся история твоей работы — у тебя на компьютере, и нейронки ищут по ней.

Второй мозг собирает в одну локальную базу разговоры с ИИ (Claude Code, Codex, Kimi Code, ChatGPT),
GitHub, почту (Gmail, Яндекс), Notion, свои YouTube- и Telegram-каналы. Сервисы блокируют, аккаунты теряются,
история чатов исчезает — а база остаётся файлом на твоём диске. Claude, Codex, Cursor и другие агенты
подключаются к ней по MCP: «на чём я остановился в проекте», «как мы тогда решили», «что нового за неделю».

## Установка

Нужны macOS и Command Line Tools (если их нет, установщик подскажет `xcode-select --install`).

```bash
curl -fsSL https://raw.githubusercontent.com/vladilenm/modern-second-brain/main/install.sh | sh
```

Команда скачает проект в `~/brain`, добавит команду `brain` и запустит мастер настройки.
Другая папка — `BRAIN_HOME=~/tools/brain` перед `sh`. Документы, Рабочий стол и Загрузки не подходят:
macOS не пускает туда фоновую синхронизацию.

## Настройка

```bash
brain setup          # мастер: источники, модель, автозапуск
brain setup notion   # настроить или перенастроить один источник
```

Мастер показывает источники списком с галочками и сам отмечает то, что нашёл на компьютере.
Для каждого выбранного спросит только нужное (адрес, каналы, токен), подскажет, где взять ключ,
и проверит подключение. Пароли и токены уходят в связку ключей macOS, в файлы и историю терминала они не попадают.

| Источник | Что берёт | Что нужно |
|---|---|---|
| Claude Code, Codex, Kimi Code | разговоры | ничего |
| ChatGPT | чаты из архива экспорта | выгрузить экспорт и положить в `~/brain/inbox` |
| GitHub | PR, issues, коммиты, README за 2 года | `gh` и `gh auth login` |
| Gmail, Яндекс.Почта | твои переписки и помеченные письма за 2 года | пароль приложения |
| Notion | страницы, заметки, записи баз | персональный API-токен |
| YouTube | видео своих каналов, статистика, комментарии | ключ YouTube Data API |
| Telegram | посты своих публичных каналов | имя канала |

Подробно про каждый источник — [docs/sources.md](docs/sources.md).

**Модель для пересказа** — по желанию. С ней каждая запись получает карточку: суть, решения, задачи,
ошибки и исправления — по ним работают «что нового» и карточки проектов. Подходит любой OpenAI-совместимый API
(SimpleClaw, OpenRouter, OpenAI, Ollama на своём компьютере, свой адрес). Без модели поиск идёт по сырому тексту.

## Где всё хранится

Всё — в папке установки (`~/brain`) и в системе, наружу ничего не выкладывается.

| Что | Где |
|---|---|
| База (SQLite с полнотекстовым поиском) | `~/brain/data/brain.db` |
| Панель в браузере | `~/brain/data/dashboard.html` |
| Настройки | `~/brain/config.json` (права 0600) |
| Архивы ChatGPT | `~/brain/inbox/` |
| Пароли и токены источников | связка ключей macOS: `brain-mail`, `brain-notion`, `brain-youtube` |
| Журнал синхронизации | `~/Library/Logs/brain-sync.log` |
| Автозапуск | `~/Library/LaunchAgents/local.brain.sync.plist` |

Резервная копия — скопировать `data/` и `config.json`. Чтобы перенести базу на другой Mac, поставь второй мозг
там и положи эти файлы на место.

## Подключение к нейронкам

Второй мозг — MCP-сервер, который запускается командой `~/brain/bin/brain mcp` (stdio). Путь замени на свой,
если ставил не в `~/brain`; мастер в конце печатает точные команды.

**Claude Code:**

```bash
claude mcp add -s user brain -- ~/brain/bin/brain mcp
```

**Codex:**

```bash
codex mcp add brain -- ~/brain/bin/brain mcp
```

**Claude Desktop** — `~/Library/Application Support/Claude/claude_desktop_config.json`,
**Cursor** — `~/.cursor/mcp.json`. Добавь в `mcpServers` (путь — полный, `~` эти программы не разворачивают):

```json
{
  "mcpServers": {
    "brain": {"command": "/Users/ТВОЁ_ИМЯ/brain/bin/brain", "args": ["mcp"]}
  }
}
```

**Любой другой MCP-клиент** — stdio-сервер с командой `/Users/ТВОЁ_ИМЯ/brain/bin/brain` и аргументом `mcp`.

После подключения и после обновления перезапусти клиент: список инструментов читается при запуске.

Инструменты (все только читают):

- `search` — поиск по словам; `project` и `days` сужают до проекта и последних дней;
- `get_record` — запись целиком;
- `recent` — что нового за последние дни по проектам: решения, задачи, ошибки и исправления;
- `project` — без имени список проектов, с именем — карточка: пути, источники, последние записи, решения и задачи.

## Что с этим делать

Спрашивай агента обычными словами — он сам сходит в базу:

- «На чём я остановился в проекте landing? Введи меня в курс дела.»
- «Как мы решили проблему с вебхуком в боте? Найди тот разговор.»
- «Что нового у меня за неделю по всем проектам?»
- «Найди письмо от подрядчика про сроки и что я ему ответил.»
- «Какие решения по ценам я принимал в Notion и в разговорах с ИИ?»
- «Собери контекст по проекту X для нового разговора.»

Сам — в терминале и в браузере:

```bash
brain search "вебхук" --project club-bot --days 30
brain recent                  # что нового за 3 дня
brain project                 # список проектов; brain project <имя> — карточка
brain dashboard --open        # панель: что нового, граф связей, проекты, записи
brain status                  # что в базе и живы ли источники
brain sync                    # забрать новое сейчас
```

## Что уходит наружу

- Источники только читаются: второй мозг ничего не пишет в почту, GitHub, Notion и каналы.
- Если включена модель пересказа, текст записей уходит выбранному провайдеру. Ollama на своём компьютере — не уходит никуда.
- Больше ничего: ни аналитики, ни облака, ни серверов второго мозга.

## Обновление и удаление

```bash
brain update
```

Обновляет код (`git pull`), данные и настройки не трогает. Перезапусти MCP-клиенты после обновления.

Удалить:

```bash
brain autostart off
```

```bash
rm -rf ~/brain ~/.local/bin/brain
```

Пароли — в «Связке ключей» по поиску `brain-`. MCP — `claude mcp remove brain -s user`, `codex mcp remove brain`
и строка `brain` в конфигах Claude Desktop и Cursor.

## Для разработчиков

Устройство, новый источник, тесты, сборка панели и значка — [docs/development.md](docs/development.md).
Python 3.9 из `/usr/bin/python3`, без внешних зависимостей.
````

- [ ] **Step 5: `config.example.json`** — `"exclude_projects": ["~/Documents/dev/автоматический-бот"]` оставить,
  `"github": {… "exclude_repos": []}` оставить; проверить, что личных имён нет: `grep -n "vladilen" config.example.json` — пусто.

- [ ] **Step 6: Проверить ссылки и чистоту**

Run: `/usr/bin/python3 -m unittest discover -s tests`
Expected: `OK` (229), `RepoHygieneTests` зелёный.

Run: `grep -n "bin/brain \|keychain.py\|launchd/" README.md docs/sources.md docs/development.md`
Expected: только `~/brain/bin/brain` в разделе MCP, `keychain.py` и `launchd/` нет.

Проверить глазами, что ни один раздел старого README не потерялся:
`grep "^## " /tmp/README.old.md` — каждый заголовок есть в `README.md`, `docs/sources.md` или `docs/development.md`
(кроме «Команды», «MCP», «Автозапуск» и «Новый источник» — они переписаны).

- [ ] **Step 7: Commit**

```bash
git add README.md docs/sources.md docs/development.md config.example.json
git commit -m "docs: README для нового человека — установка, где хранятся данные, MCP, что с этим делать

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Итоговая проверка и переход у автора

**Files:** без изменений кода.

- [ ] **Step 1: Все тесты**

Run: `/usr/bin/python3 -m unittest discover -s tests`
Expected: `OK`, около 229 тестов, 0 падений.

- [ ] **Step 2: Чистая установка с нуля во временный HOME**

```bash
T="$(mktemp -d)"
HOME="$T" BRAIN_REPO="$PWD" BRAIN_REF="$(git branch --show-current)" BRAIN_NO_SETUP=1 sh install.sh
HOME="$T" BRAIN_CONFIG="$T/brain/config.json" "$T/.local/bin/brain" setup
```

Пройти мастер руками: отметить Claude Code и Telegram, настроить Telegram, модель — пропустить, автозапуск — **нет**,
значок — нет, забрать данные — нет. Проверить итоговый экран: пути и команды MCP указывают в `$T/brain`.
Удалить `$T`. (Автозапуск в этой проверке не включать: он настоящий и общий для пользователя.)

- [ ] **Step 3: Спросить автора и только после «да» перевести его установку**

Сказать, что сделает переход: снимет агент `com.vladilen.brain-sync`, поставит `local.brain.sync`, пересоберёт BrainBar.
После согласия:

```bash
brain autostart on && brain autostart status && menubar/build.sh
```

Expected: «автозапуск включён…», `launchctl print gui/$(id -u)/com.vladilen.brain-sync` — ошибка (агента нет),
BrainBar перезапущен и показывает состояние.

- [ ] **Step 4: Проверить фоновые процессы**

```bash
ps -axo pid,etime,command | grep -E "ffmpeg|osascript|yt-dlp|python|node" | grep -v grep
```

Своих процессов (сервера тестов, мастер) не должно остаться.
