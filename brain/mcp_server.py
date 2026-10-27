"""MCP-сервер второго мозга: JSON-RPC поверх stdin/stdout, без внешних зависимостей.

Подключается к Claude Code, Claude Desktop и Codex как локальный (stdio) сервер.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from . import __version__, config, db, render
from . import search as search_mod

INSTRUCTIONS = (
    "Второй мозг пользователя — поисковая база его прошлых разговоров с Claude Code, Codex и ChatGPT: "
    "решения и их причины, ошибки и исправления, команды, договорённости. Ищи здесь, когда пользователь "
    "ссылается на прошлую работу («мы же обсуждали», «как я тогда сделал»), спрашивает про свои проекты "
    "или когда ответ зависит от решений, принятых раньше. Если записи противоречат друг другу, верь более свежей."
)

# Инструменты только читают локальную базу. По этой пометке клиенты (например, Codex)
# могут не спрашивать разрешения на каждый вызов.
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

TOOLS = [
    {
        "name": "search",
        "description": (
            "Поиск по прошлым разговорам пользователя с ИИ. Возвращает до limit записей: id, название, "
            "источник, дату, проект и краткое содержание. Свежие записи поднимаются выше. "
            "Чтобы прочитать запись целиком, вызови get_record с её id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Ключевые слова: название проекта, инструмента, человека, текст ошибки. "
                                   "Пиши на языке разговора, можно добавить английские термины.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
                "source": {
                    "type": "string",
                    "enum": ["claude_code", "codex", "chatgpt"],
                    "description": "Искать только в одном источнике",
                },
            },
            "required": ["query"],
        },
        "annotations": READ_ONLY,
    },
    {
        "name": "get_record",
        "description": (
            "Полная запись по id из search: карточка (краткое содержание, атомы) и расшифровка разговора. "
            "Длинные разговоры отдаются кусками по 20000 символов — для продолжения передай offset из ответа."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "required": ["id"],
        },
        "annotations": READ_ONLY,
    },
]


class MethodNotFound(Exception):
    pass


def call_tool(name: str, args: dict, cfg: dict) -> dict:
    # Базу создаёт только sync. Если её нет на месте (проект перенесли), не плодим пустую.
    if not Path(cfg["db_path"]).expanduser().exists():
        return {"content": [{"type": "text", "text": f"База не найдена: {cfg['db_path']}. Запусти brain sync."}],
                "isError": True}
    conn = db.connect(cfg["db_path"])  # новое соединение на вызов — видим свежие данные sync
    try:
        if name == "search":
            results = search_mod.search(conn, str(args.get("query", "")), limit=int(args.get("limit", 8)),
                                        source=args.get("source"), **cfg["search"])
            text = render.results_text(results) if results else (
                "Ничего не найдено. Попробуй другие слова: название проекта или инструмента, текст ошибки.")
        elif name == "get_record":
            row = search_mod.get_record(conn, int(args["id"]))
            if row is None:
                return {"content": [{"type": "text", "text": f"Записи {args['id']} нет"}], "isError": True}
            text = render.render_record(row, offset=int(args.get("offset", 0)))
        else:
            return {"content": [{"type": "text", "text": f"Неизвестный инструмент: {name}"}], "isError": True}
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"Ошибка: {exc}"}], "isError": True}
    finally:
        conn.close()


def handle(msg: dict, cfg: dict) -> dict:
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion") or "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "brain", "version": __version__},
            "instructions": INSTRUCTIONS,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return call_tool(params.get("name"), params.get("arguments") or {}, cfg)
    raise MethodNotFound(method)


def main() -> None:
    cfg = config.load()
    # Кодировку задаём явно: MCP-клиент может запустить нас без локали.
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    out = sys.stdout
    while True:
        line = stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            if "id" not in msg:
                continue  # уведомление, ответ не нужен
            try:
                resp = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg, cfg)}
            except MethodNotFound as exc:
                resp = {"jsonrpc": "2.0", "id": msg["id"],
                        "error": {"code": -32601, "message": f"Method not found: {exc}"}}
            except Exception as exc:
                resp = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": str(exc)}}
        out.write(json.dumps(resp) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
