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
