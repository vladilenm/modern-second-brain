"""Командная строка: brain sync | search | show | status | summarize | mcp."""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import config, db, render, status, summarize
from . import search as search_mod
from . import sync as sync_mod
from .timeutil import age_days, now_iso


def cmd_sync(args, cfg) -> int:
    conn = db.connect(cfg["db_path"])
    rep = status.Reporter(cfg, conn)
    rep.update(state="sync", last_sync_at=rep.data["started_at"])
    print(f"=== sync {rep.data['started_at']}")
    try:
        totals = sync_mod.sync(cfg, conn, only=args.source, rescan=args.rescan)
        # Сводку обновляем сразу: пересказ идёт минутами, а новые и удалённые записи уже в базе.
        rep.update(last_sync=totals, **status.snapshot(conn))
        if not args.no_summarize:
            result = summarize.summarize_pending(conn, cfg, limit=args.limit, on_progress=rep.progress)
            rep.update(last_summarize=dict(zip(("done", "failed"), result)) if result else None)
    except Exception as exc:
        rep.finish(error=str(exc))
        raise
    rep.finish()
    return 0


def cmd_summarize(args, cfg) -> int:
    conn = db.connect(cfg["db_path"])
    rep = status.Reporter(cfg, conn)
    try:
        result = summarize.summarize_pending(conn, cfg, limit=args.limit, on_progress=rep.progress)
    except Exception as exc:
        rep.finish(error=str(exc))
        raise
    rep.update(last_summarize=dict(zip(("done", "failed"), result)) if result else None)
    rep.finish()
    return 0 if result is not None else 1


def cmd_search(args, cfg) -> int:
    conn = db.connect(cfg["db_path"])
    results = search_mod.search(conn, args.query, limit=args.n, source=args.source, **cfg["search"])
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(render.results_text(results) if results else "ничего не найдено")
    return 0


def cmd_show(args, cfg) -> int:
    conn = db.connect(cfg["db_path"])
    row = search_mod.get_record(conn, args.id)
    if row is None:
        print(f"записи {args.id} нет")
        return 1
    print(render.render_record(row, offset=args.offset, max_chars=args.chars))
    return 0


def cmd_status(args, cfg) -> int:
    conn = db.connect(cfg["db_path"])
    print(f"база: {cfg['db_path']}")
    rows = conn.execute(
        """SELECT source, COUNT(*) AS n,
                  SUM(summarized_hash IS NOT NULL AND summarized_hash = content_hash) AS done,
                  MAX(updated_at) AS latest
           FROM records GROUP BY source ORDER BY source"""
    ).fetchall()
    for r in rows:
        print(f"  {render.SOURCE_NAMES.get(r['source'], r['source'])}: записей {r['n']}, "
              f"с пересказом {r['done']}, последняя от {r['latest']}")
    if not rows:
        print("  записей пока нет — запусти brain sync")
    print("синхронизация:")
    for s in conn.execute("SELECT * FROM sync_state ORDER BY source"):
        stale = s["last_success_at"] is None or age_days(s["last_success_at"]) > 1
        mark = "  ВНИМАНИЕ:" if stale or s["last_error"] else "  "
        print(f"{mark} {s['source']}: последний запуск {s['last_run_at']}, "
              f"последний успешный {s['last_success_at'] or 'не было'}")
        if s["last_error"]:
            print(f"     ошибка: {s['last_error']}")
    llm = cfg["llm"]
    state = f"{llm['model']} через {llm['base_url']}" if summarize.configured(llm) else "модель не настроена"
    print(f"пересказ: {state}; в очереди {summarize.pending_count(conn)}")
    return 0


def cmd_dashboard(args, cfg) -> int:
    from . import dashboard
    path = dashboard.build(db.connect(cfg["db_path"]), cfg)
    print(path)
    if args.open:
        import subprocess
        subprocess.run(["/usr/bin/open", str(path)], check=False)
    return 0


def cmd_mcp(args, cfg) -> int:
    from . import mcp_server
    mcp_server.main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="brain", description="Второй мозг: поиск по прошлым разговорам с ИИ")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("sync", help="забрать новое из источников и пересказать")
    s.add_argument("--source", help="только один источник: claude_code, codex, chatgpt_export")
    s.add_argument("--no-summarize", action="store_true", help="без пересказа")
    s.add_argument("--rescan", action="store_true", help="перечитать все файлы (после правки коннектора)")
    s.add_argument("--limit", type=int, help="сколько записей пересказать за запуск")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("summarize", help="пересказать записи из очереди")
    s.add_argument("--limit", type=int)
    s.set_defaults(func=cmd_summarize)

    s = sub.add_parser("search", help="поиск")
    s.add_argument("query")
    s.add_argument("-n", type=int, default=8, help="сколько результатов")
    s.add_argument("--source", help="claude_code, codex или chatgpt")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("show", help="показать запись целиком")
    s.add_argument("id", type=int)
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--chars", type=int, default=20000)
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("status", help="что в базе и живы ли коннекторы")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("dashboard", help="собрать HTML-панель базы (data/dashboard.html)")
    s.add_argument("--open", action="store_true", help="сразу открыть в браузере")
    s.set_defaults(func=cmd_dashboard)

    s = sub.add_parser("mcp", help="запустить MCP-сервер (stdio)")
    s.set_defaults(func=cmd_mcp)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args, config.load())


if __name__ == "__main__":
    sys.exit(main())
