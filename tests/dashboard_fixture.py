"""Synthetic, private-data-free dashboard for the WebGL browser checks."""
import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_brain import make_cfg
from brain import dashboard, db
from brain.connectors.base import Record

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
cfg = make_cfg(out)
conn = db.connect(":memory:")
# Без долей секунды: время сборки панели (generated_at) тоже округлено, и запись «только что» не должна его обгонять.
now = datetime.now(timezone.utc).replace(microsecond=0)
for i in range(1, 49):
    updated = now - timedelta(days=120 if i > 36 else i % 20)
    db.upsert(conn, Record(
        "codex" if i % 2 else "claude_code", str(i), f"Запись {i:02d}",
        "Тестовый текст. " * (100 + i * 5), updated.isoformat(),
        project=f"~/projects/Проект-{(i - 1) // 8 + 1}",
    ))
conn.execute("UPDATE records SET summary = 'Тестовая карточка записи', keywords = 'граф, камера', summarized_hash = content_hash")
data = dashboard.collect(conn, cfg)
# Separate chains and one bridge: the neighborhood is smaller than the whole graph.
data["graph"]["edges"] = [[i, i + 1, 0.2 + (i % 3) * 0.2] for i in range(1, 48) if i % 8] + [[8, 9, 0.4]]
for record in data["records"]:
    record["topics"] = ["граф", "камера"]
    if record["title"] == "Запись 07":
        record["atoms"] = ["[decision] Субтитры вшиваем в ролик через ffmpeg", "[todo] Проверить шрифт на телефоне"]
        record["questions"] = ["Как мы делали субтитры для рилсов?"]
html = dashboard.assemble().replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"), 1)
(out / "dashboard.html").write_text(html, encoding="utf-8")
# «Что нового»: новая запись со знаниями, продолжение, записи без знаний, запись без проекта.
news = copy.deepcopy(data)
for record in news["records"]:
    record["atoms"] = []
    record["started_at"] = record["updated_at"]
    if record["title"] == "Запись 01":
        record["atoms"] = [f"[decision] Решение {k}" for k in range(1, 7)] + [
            "[error] Сборка падала без ffmpeg", "[command] brain sync --rescan", "[fact] Субтитры хранятся в srt"]
    if record["title"] == "Запись 02":
        record["atoms"] = ["[todo] Проверить шрифт на телефоне"]
        record["started_at"] = (now - timedelta(days=30)).isoformat()
    if record["title"] == "Запись 20":
        record["atoms"] = ["[fix] Исправили путь к базе"]
        record["project"] = None
        record["pkey"] = ""
(out / "news.html").write_text(
    dashboard.assemble().replace("/*__DATA__*/null", json.dumps(news, ensure_ascii=False).replace("</", "<\\/"), 1),
    encoding="utf-8")
data["records"] = []
data["projects"] = {}
data["graph"]["edges"] = []
(out / "empty.html").write_text(dashboard.assemble().replace("/*__DATA__*/null", json.dumps(data), 1), encoding="utf-8")
