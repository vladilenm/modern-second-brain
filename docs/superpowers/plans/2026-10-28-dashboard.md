# Панель базы: обзор, граф, проекты, записи — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Переделать `brain dashboard` в панель с вкладками Обзор, Граф, Проекты, Записи по спеке
`docs/superpowers/specs/2026-10-28-dashboard-design.md`.

**Architecture:** Python при сборке считает темы записей, похожие записи (рёбра графа) и склейку проектов
и вшивает их в данные страницы. Страница собирается из `brain/web/` (HTML, CSS, три JS-файла) в один
`data/dashboard.html`; графики рисуются в SVG, граф — на canvas со своей физикой.

**Tech Stack:** Python 3.9 стандартная библиотека (`/usr/bin/python3`), `unittest`; ванильный JS (ES2020),
SVG, Canvas 2D; шрифты Google Fonts.

## Global Constraints

- Только стандартный Python 3.9, без внешних зависимостей; в странице — без внешних скриптов.
- Один выходной файл `data/dashboard.html`, права 600, ровно один `</script>`.
- Стиль — токены дизайн-системы Владилена Минина 1.0.0 (уже в текущем шаблоне) + `side-*` для меню.
- Цвета источников (локальные токены `--series-1…4`): Claude Code `#7C3AED` / `#8e72f2`, Codex `#16a070` / `#199e70`,
  GitHub `#eb6834` / `#d95926`, ChatGPT `#2a78d6` / `#3987e5` (светлая / тёмная). Порядок слотов — порядок
  стопки в графиках.
- Тексты на «ты», без эмодзи, капс только в метках `label`; цифры только настоящие.
- `MIN_SIM = 0.12`, до 3 своих соседей, темы с `df < 2` или `df > 25%` записей (при 20+ записях) в сходстве
  не участвуют. Проверено на живой базе 2026-10-28: 446 рёбер, из общих тем выпадает только «claude code».
- Тесты: `/usr/bin/python3 -m unittest discover -s tests`.

## Файлы

| Файл | Что делает |
|---|---|
| `brain/topics.py` (новый) | нормализация тем, темы записи, `build()` → `Topics` (показываемые темы, соседи, рёбра, темы проекта) |
| `brain/dashboard.py` | `project_key()`, новые поля в `collect()`, сборка страницы из `brain/web/` |
| `brain/web/index.html` (новый) | разметка: меню, четыре вкладки, карточка записи, плейсхолдеры `/*__CSS__*/`, `/*__DATA__*/null`, `/*__JS__*/` |
| `brain/web/style.css` (новый) | токены, каркас, компоненты |
| `brain/web/charts.js` (новый) | SVG: сложенные столбцы, горизонтальные полосы, мини-графики, подсказка |
| `brain/web/graph.js` (новый) | физика (попарно / Barnes-Hut), canvas-отрисовка, масштаб, наведение, фильтры |
| `brain/web/app.js` (новый) | данные, адреса вкладок, поиск, карточка записи, вкладки Обзор / Проекты / Записи, связка с графом |
| `brain/dashboard.html` | удаляется (переезжает в `brain/web/`) |
| `tests/test_brain.py` | `TopicsTests`, `ProjectKeyTests`, обновлённый `DashboardTests` |
| `README.md` | раздел «Панель базы» |

## Данные страницы (контракт между Python и JS)

Нынешние поля `collect()` остаются. Добавляются:

```json
{
  "records": [{"id": 1, "...": "...", "pkey": "club-landing", "topics": ["рилс", "субтитры"]}],
  "projects": {"club-landing": {"name": "club-landing", "paths": ["vladilenm/club_landing", "~/Documents/vscode/club-landing"], "topics": ["лендинг", "telegram"]}},
  "graph": {"edges": [[12, 40, 0.31]]}
}
```

---

### Task 1: Темы записи — `normalize` и `record_topics`

**Files:**
- Create: `brain/topics.py`
- Test: `tests/test_brain.py` (новый класс `TopicsTests`)

**Interfaces:**
- Produces: `topics.normalize(keyword: str) -> Optional[str]`,
  `topics.record_topics(keywords: Optional[str], title: Optional[str]) -> List[Tuple[str, str]]` (ключ, исходная форма).

- [ ] **Step 1: Write the failing test** — добавить в `tests/test_brain.py` перед `class DashboardTests`:

```python
class TopicsTests(unittest.TestCase):
    def test_normalize_merges_word_forms(self):
        from brain import topics
        self.assertEqual({topics.normalize(w) for w in ("Рилс", "рилса", "рилсу", "рилсы")}, {"рилс"})
        self.assertEqual(topics.normalize("клуба"), topics.normalize("клубу"))
        self.assertEqual(topics.normalize("Skills"), topics.normalize("skill"))
        self.assertEqual(topics.normalize("Claude  Code"), "claude code")
        self.assertEqual(topics.normalize("next.js"), "next.js")

    def test_normalize_drops_noise(self):
        from brain import topics
        for noise in ("2026-10-19", "19 октября 2026", "октябрь 2026", "1080×1920", "final.mp4", "lessons.md", "ок", ""):
            self.assertIsNone(topics.normalize(noise), noise)

    def test_record_topics_falls_back_to_title(self):
        from brain import topics
        self.assertEqual([k for k, _ in topics.record_topics("Рилс, рилса, 2026-10-19", "x")], ["рилс"])
        self.assertEqual([k for k, _ in topics.record_topics(None, "Монтаж рилса для клуба")], ["монтаж", "рилс", "клуб"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m unittest tests.test_brain.TopicsTests -v`
Expected: FAIL — `ImportError: cannot import name 'topics'`.

- [ ] **Step 3: Write minimal implementation** — `brain/topics.py`:

```python
"""Темы записей и похожие записи — для графа и карточек проектов в панели.

Тема — ключевое слово карточки, сведённое к основе: «рилс», «рилса», «рилсу» — одна тема.
Похожесть — косинус бинарных векторов тем с весом idf: редкая общая тема значит больше частой.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

MONTHS = "январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр"
DATE = re.compile(rf"^(\d{{4}}-\d\d(-\d\d)?|\d{{1,2}} ({MONTHS})[а-я]* \d{{4}}|({MONTHS})[а-я]* \d{{4}}|\d{{4}})$")
NUMBER = re.compile(r"^[\d\s.,:×x%+\-/]+$")
# Только расширения файлов: «next.js» и «node.js» — технологии, а не файлы.
FILE = re.compile(r"^[^\s/]+\.(md|mdx|mp4|mov|m4a|mp3|wav|srt|ass|vtt|json|jsonl|txt|py|ts|tsx|jsx|html|css|png"
                  r"|jpe?g|webp|gif|svg|pdf|ya?ml|toml|sh|csv|log|plist|db|sqlite|swift|lock|env)$")
ENDINGS = sorted("ами ями ого его ому ему ыми ими ов ев ей ой ий ый ая яя ое ее ые ие ом ем ах ях ам ям "
                 "а я ы и у ю е о".split(), key=len, reverse=True)
STOP = set("для как что это или при про над под без его еще ещё уже так тоже чтобы если когда где".split())


def _stem(word: str) -> str:
    if re.fullmatch(r"[а-я]+", word) and len(word) > 4:
        for e in ENDINGS:
            if word.endswith(e) and len(word) - len(e) >= 3:
                return word[:-len(e)]
    elif re.fullmatch(r"[a-z]+", word) and len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalize(keyword: str) -> Optional[str]:
    k = " ".join((keyword or "").lower().replace("ё", "е").split()).strip(" .,;:!?«»\"'()")
    if len(k) < 3 or DATE.match(k) or NUMBER.match(k) or FILE.match(k):
        return None
    return " ".join(_stem(w) for w in k.split(" "))


def record_topics(keywords: Optional[str], title: Optional[str]) -> List[Tuple[str, str]]:
    """Темы записи без повторов: из ключевых слов карточки, а если их нет — из слов названия."""
    raw = [w.strip() for w in (keywords or "").split(",") if w.strip()]
    if not raw:
        raw = [w for w in re.findall(r"[\w-]+", (title or "").lower()) if len(w) > 3 and w not in STOP]
    out, seen = [], set()
    for w in raw:
        key = normalize(w)
        if key and key not in seen:
            seen.add(key)
            out.append((key, w))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m unittest tests.test_brain.TopicsTests -v`
Expected: 3 tests OK.

- [ ] **Step 5: Commit**

```bash
git add brain/topics.py tests/test_brain.py
git commit -m "Темы записей: основа слова без дат, чисел и файлов"
```

### Task 2: Похожие записи — `topics.build`

**Files:**
- Modify: `brain/topics.py`
- Test: `tests/test_brain.py` (`TopicsTests`)

**Interfaces:**
- Consumes: `record_topics`, `normalize` (Task 1).
- Produces: `topics.build(rows: Iterable[Tuple[int, Optional[str], Optional[str]]]) -> Topics` (строки — id, keywords, title);
  `Topics.shown: Dict[int, List[str]]`, `Topics.neighbors: Dict[int, List[Tuple[int, float]]]`,
  `Topics.pairs: List[Tuple[int, int, float]]`, `Topics.project_topics(ids: List[int], limit: int = 5) -> List[str]`;
  константы `MIN_SIM = 0.12`, `MAX_NEIGHBORS = 3`, `MAX_SHARE = 0.25`.

- [ ] **Step 1: Write the failing test** — добавить в `TopicsTests`:

```python
    def _rows(self):
        # 20 записей-фона, чтобы сработал порог «не больше 25%» для общей темы «claude code».
        rows = [(i, f"claude code, фон{i}", "") for i in range(100, 120)]
        rows += [
            (1, "claude code, рилс, субтитры, монтаж", ""),
            (2, "claude code, рилса, субтитры, монтаж", ""),
            (3, "claude code, postgresql", ""),
        ]
        return rows

    def test_build_links_records_with_shared_rare_topics(self):
        from brain import topics
        t = topics.build(self._rows())
        self.assertIn(2, [o for o, _ in t.neighbors[1]])
        self.assertEqual(t.neighbors[3], [])  # общая только «claude code» — она в 92% записей
        self.assertIn((1, 2), [(a, b) for a, b, _ in t.pairs])
        for a, b, w in t.pairs:
            self.assertLess(a, b)
            self.assertTrue(topics.MIN_SIM <= w <= 1)
        self.assertEqual(len(t.pairs), len({(a, b) for a, b, _ in t.pairs}))
        self.assertTrue(t.shown[1] and all(isinstance(s, str) for s in t.shown[1]))

    def test_build_caps_own_neighbors(self):
        from brain import topics
        rows = [(i, "рилс, субтитры, монтаж", "") for i in range(1, 7)]
        t = topics.build(rows)
        for i in range(1, 7):
            self.assertLessEqual(len(t.neighbors[i]), topics.MAX_NEIGHBORS)
        union = {(min(i, o), max(i, o)) for i in range(1, 7) for o, _ in t.neighbors[i]}
        self.assertEqual(union, {(a, b) for a, b, _ in t.pairs})

    def test_project_topics_prefer_repeated(self):
        from brain import topics
        t = topics.build([(1, "рилс, субтитры, редкое1", ""), (2, "рилсы, субтитры, редкое2", ""),
                          (3, "рилс, монтаж", ""), (4, "postgresql, монтаж", "")])
        self.assertEqual(t.project_topics([1, 2, 3])[:2], ["рилс", "субтитры"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m unittest tests.test_brain.TopicsTests -v`
Expected: FAIL — `AttributeError: module 'brain.topics' has no attribute 'build'`.

- [ ] **Step 3: Write minimal implementation** — дописать в `brain/topics.py` (импорты — в начало файла):

```python
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable

MIN_SIM = 0.12
MAX_NEIGHBORS = 3
MAX_SHARE = 0.25   # тема из большей доли записей ничего не различает («claude code»)
SHOWN = 8


@dataclass
class Topics:
    shown: Dict[int, List[str]]
    neighbors: Dict[int, List[Tuple[int, float]]]
    pairs: List[Tuple[int, int, float]]
    keys: Dict[int, List[str]]
    idf: Dict[str, float]
    display: Dict[str, str]

    def project_topics(self, ids: List[int], limit: int = 5) -> List[str]:
        """Темы проекта: повторяющиеся в его записях важнее, внутри — по «число записей × idf»."""
        count = Counter(k for i in ids for k in self.keys.get(i, []))
        many = len(ids) >= 3
        ranked = sorted(count, key=lambda k: (many and count[k] < 2, -count[k] * self.idf[k], k))
        return [self.display[k] for k in ranked[:limit]]


def build(rows: Iterable[Tuple[int, Optional[str], Optional[str]]]) -> Topics:
    per = {rid: record_topics(kw, title) for rid, kw, title in rows}
    n = len(per)
    forms: Dict[str, Counter] = defaultdict(Counter)
    for ts in per.values():
        for key, word in ts:
            forms[key][word] += 1
    keys = {rid: [k for k, _ in ts] for rid, ts in per.items()}
    df = Counter(k for ks in keys.values() for k in ks)
    idf = {k: math.log(n / d) for k, d in df.items()}
    display = {k: c.most_common(1)[0][0] for k, c in forms.items()}
    cap = MAX_SHARE * n if n >= 20 else n
    useful = {k for k, d in df.items() if 2 <= d <= cap}
    shown = {rid: [display[k] for k in sorted(ks, key=lambda k: (k not in useful, -idf[k], k))[:SHOWN]]
             for rid, ks in keys.items()}

    vec = {rid: [k for k in ks if k in useful] for rid, ks in keys.items()}
    norm = {rid: math.sqrt(sum(idf[k] ** 2 for k in v)) for rid, v in vec.items()}
    postings: Dict[str, List[int]] = defaultdict(list)
    for rid, v in vec.items():
        for k in v:
            postings[k].append(rid)
    neighbors: Dict[int, List[Tuple[int, float]]] = {}
    edges: Dict[Tuple[int, int], float] = {}
    for rid, v in vec.items():
        dot: Dict[int, float] = defaultdict(float)
        for k in v:
            for other in postings[k]:
                if other != rid:
                    dot[other] += idf[k] ** 2
        sims = sorted(((round(s / (norm[rid] * norm[o]), 3), o) for o, s in dot.items()), key=lambda x: (-x[0], x[1]))
        neighbors[rid] = [(o, s) for s, o in sims if s >= MIN_SIM][:MAX_NEIGHBORS]
        for o, s in neighbors[rid]:
            edges[(min(rid, o), max(rid, o))] = s
    pairs = sorted((a, b, w) for (a, b), w in edges.items())
    return Topics(shown, neighbors, pairs, keys, idf, display)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m unittest tests.test_brain.TopicsTests -v`
Expected: 6 tests OK.

- [ ] **Step 5: Commit**

```bash
git add brain/topics.py tests/test_brain.py
git commit -m "Похожие записи по темам: TF-IDF, обратный индекс, до 3 соседей"
```

### Task 3: Данные панели и сборка из `brain/web/`

**Files:**
- Modify: `brain/dashboard.py`
- Create: `brain/web/index.html`, `brain/web/style.css`, `brain/web/charts.js`, `brain/web/graph.js`, `brain/web/app.js`
- Delete: `brain/dashboard.html`
- Test: `tests/test_brain.py` (`ProjectKeyTests`, `DashboardTests`)

**Interfaces:**
- Consumes: `topics.build` (Task 2).
- Produces: `dashboard.project_key(project: Optional[str]) -> Tuple[str, str]`; поля `pkey`, `topics` у записей,
  `projects`, `graph.edges` в данных; `dashboard.assemble() -> str` (шаблон со вшитыми CSS и JS, без данных).

- [ ] **Step 1: Write the failing tests**

```python
class ProjectKeyTests(unittest.TestCase):
    def test_folder_and_repo_with_same_name_merge(self):
        from brain.dashboard import project_key
        self.assertEqual(project_key("~/Documents/vscode/club-landing")[0], project_key("vladilenm/club_landing")[0])
        self.assertEqual(project_key("~/Documents/vscode/club-landing"), ("club-landing", "club-landing"))
        self.assertEqual(project_key("~/Library/Application Support/Claude/scratch-workspaces/a/b/scratch-2026-10-21-f6"),
                         ("scratch", "Черновики"))
        self.assertEqual(project_key(None), ("", "без проекта"))
```

В `DashboardTests.test_page_embeds_data_safely` после проверки `files_seen` добавить:

```python
        self.assertEqual(data["records"][0]["pkey"], "p")
        self.assertEqual(data["projects"]["p"]["paths"], ["~/p"])
        self.assertIn("edges", data["graph"])
        self.assertTrue(all("topics" in r for r in data["records"]))
        self.assertIn('id="graph"', html)
```

И новый тест:

```python
    def test_assemble_refuses_closing_script_in_js(self):
        from brain import dashboard
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp)
            (web / "index.html").write_text("<style>/*__CSS__*/</style><script>const DATA = /*__DATA__*/null;\n/*__JS__*/</script>")
            (web / "style.css").write_text("")
            for name in dashboard.SCRIPTS:
                (web / name).write_text("const x = '</script>';" if name == "app.js" else "")
            with self.assertRaises(ValueError):
                dashboard.assemble(web)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m unittest tests.test_brain.ProjectKeyTests tests.test_brain.DashboardTests -v`
Expected: FAIL — нет `project_key`, нет `assemble`.

- [ ] **Step 3: Implement** — в `brain/dashboard.py`:

```python
import re
from collections import Counter, defaultdict
from typing import Optional, Tuple

from . import connectors, status as status_mod, topics

WEB = Path(__file__).with_name("web")
SCRIPTS = ("charts.js", "graph.js", "app.js")   # порядок важен: app.js пользуется первыми двумя


def project_key(project: Optional[str]) -> Tuple[str, str]:
    """Ключ и имя проекта. Папка ~/…/club-landing и репозиторий vladilenm/club_landing — один проект."""
    p = (project or "").rstrip("/")
    if "/scratch-workspaces/" in p:
        return "scratch", "Черновики"
    if not p:
        return "", "без проекта"
    name = p.split("/")[-1] or p
    return re.sub(r"[\s_]+", "-", name.lower()), name


def assemble(web: Path = WEB) -> str:
    """Шаблон страницы со вшитыми стилями и скриптами. «</script» внутри JS закрыл бы тег раньше времени."""
    css = (web / "style.css").read_text(encoding="utf-8")
    js = "\n".join((web / name).read_text(encoding="utf-8") for name in SCRIPTS)
    if re.search(r"</style", css, re.I) or re.search(r"</script|<!--", js, re.I):
        raise ValueError("в brain/web: «</script» в JS или «</style» в CSS сломает страницу")
    html = (web / "index.html").read_text(encoding="utf-8")
    return html.replace("/*__CSS__*/", css, 1).replace("/*__JS__*/", js, 1)
```

В `collect()` после цикла по записям, до `sync = …`:

```python
    t = topics.build((r["id"], r["keywords"], r["title"]) for r in records)
    groups = defaultdict(list)
    for r in records:
        key, name = project_key(r["project"])
        r["pkey"] = key
        r["topics"] = t.shown.get(r["id"], [])
        groups[key].append((name, r["project"], r["id"]))
    projects = {
        key: {"name": Counter(name for name, _, _ in g).most_common(1)[0][0],
              "paths": sorted({p for _, p, _ in g if p}),
              "topics": t.project_topics([i for _, _, i in g])}
        for key, g in groups.items()
    }
```

и в возвращаемый словарь: `"projects": projects, "graph": {"edges": [list(p) for p in t.pairs]},`.

В `build()` заменить чтение шаблона:

```python
    html = assemble().replace("/*__DATA__*/null", payload, 1)
```

(`TEMPLATE` удалить.) Плейсхолдер данных заменяется последним — текст разговоров в данных может содержать
`/*__JS__*/`, его трогать нельзя.

- [ ] **Step 4: Перенести текущую страницу как стартовую точку**

`brain/web/index.html` — разметка из `brain/dashboard.html` (строки 1–9 и 198–267), где `<style>…</style>`
заменён на `<style>/*__CSS__*/</style>`, а скрипт — на `<script>\nconst DATA = /*__DATA__*/null;\n/*__JS__*/\n</script>`.
`brain/web/style.css` — содержимое старого `<style>` (строки 11–196). `brain/web/app.js` — старый скрипт
(строки 271–614) без строки `const DATA = …`. `charts.js` и `graph.js` — пока с комментарием-шапкой.
В `index.html` временно добавить пустой `<div id="graph" hidden></div>` (его ждёт тест; вкладка — в Task 6).
`git rm brain/dashboard.html`.

- [ ] **Step 5: Run all tests**

Run: `/usr/bin/python3 -m unittest discover -s tests`
Expected: всё OK. Затем `bin/brain dashboard` — печатает путь, страница открывается как раньше.

- [ ] **Step 6: Commit**

```bash
git add -A brain/dashboard.py brain/web brain/dashboard.html tests/test_brain.py
git commit -m "Панель: проекты по имени, темы и рёбра в данных, шаблон в brain/web"
```

### Tasks 4–8: страница

Дальше — визуальная работа: код пишется по контрактам ниже и проверяется в браузере на живой базе
(`bin/brain dashboard`, затем открыть `data/dashboard.html` во встроенном браузере), а не юнит-тестами.
Перед Task 4 загрузить скиллы `vladilen-design-system`, `dataviz`, `design-taste-frontend` и
`ui-ux-pro-max` и сверяться с ними. После каждой задачи: тесты зелёные, в консоли нет ошибок, коммит.

#### Task 4: Каркас — меню, адреса, поиск, карточка записи

**Files:** `brain/web/index.html`, `brain/web/style.css`, `brain/web/app.js`

**Interfaces (Produces, для Tasks 5–8):**
- В `app.js`: `const R` (записи), `byId: Map<id, record>`, `PROJECTS`, `SRC` — `[{source, title, slot}]` в порядке слотов
  (claude_code 1, codex 2, github 3, chatgpt 4, дальше по реестру); `color(source)` → `var(--series-N)`;
  `openRecord(id)`, `closeRecord()`; `route` — `{tab, arg}` из `location.hash`; `go(hash)`;
  `onTab(tab, render)` — регистрирует отрисовку вкладки; хелперы `esc`, `n`, `plural`, `dayMonth`, `ago`, `stamp`
  (перенести из старого скрипта); `neighbors(id)` → `[[otherId, w]]` по `DATA.graph.edges`, по убыванию.
- Разметка: `<nav class="side">` (название, 4 пункта с `aria-current`, поиск, блок состояния),
  `<header class="topbar">` (до 1024px), `<main>` с `<section data-tab="overview|graph|projects|records">`.

**Требования:**
- Токены `--series-1…4` в `:root` и в тёмной теме (значения — Global Constraints); `--side-*` из DESIGN.md.
- Меню фиксировано слева ≥1024px (ширина 240px); уже — скрыто, открывается кнопкой в `topbar`, закрывается
  кликом мимо и `Esc`.
- Адреса: `#overview` (по умолчанию и для неизвестных), `#graph`, `#graph/<id>`, `#projects`, `#project/<ключ>`,
  `#records`; `hashchange` перерисовывает, заголовок вкладки браузера — «Второй мозг · <вкладка>».
- Поиск: `/` фокусирует поле (если фокус не в другом поле), выпадающий список до 10 совпадений
  (все слова в `title + project + keywords + summary`), стрелки и Enter, клик — `openRecord`.
- Блок состояния: точка (ок — `success`, прогон — `accent`, ошибка/прервался — `danger`) + текст как в старом
  `lede`; ниже — ошибки источников из `DATA.sync[*].last_error`, по одной строке, тег `danger-soft`.
- Карточка записи — старая `openRecord` плюс «Похожие записи» (до 5 из `neighbors`, клик открывает) и кнопка
  «Показать на графе» (`go('#graph/' + id)`).

#### Task 5: Обзор

**Files:** `brain/web/charts.js`, `brain/web/app.js`, `brain/web/style.css`

**Interfaces (Produces в `charts.js`):**
- `stackedBars(el, buckets, series, opts)` — `buckets: [{label, from, to, values: {source: n}}]`,
  `series: [{key, title, color}]`; SVG по ширине `el`, сетка 4 линии, подписи пика и последнего столбца,
  2px зазор между сегментами, скругление 4px у верхнего сегмента, подсказка при наведении.
- `hbars(el, rows, opts)` — `rows: [{label, value, title, onClick}]`, один цвет `--color-accent`.
- `sparkline(values)` → строка SVG (столбики 4px, высота 24px, цвет `--color-ink-subtle`).
- `tip.show(html, x, y)`, `tip.hide()` — общая подсказка (перенести старую `#tip`).

**Требования:** плитки, график по неделям с переключателем «3 месяца / год / всё» (по месяцам для «всё»),
«Где больше всего работы» (8 проектов, клик → `#project/<ключ>`), «Свежее» (6 записей) — по спеке.
Перерисовка графиков на `resize` с задержкой 100ms.

#### Task 6: Граф

**Files:** `brain/web/graph.js`, `brain/web/app.js`, `brain/web/style.css`

**Interfaces (Produces в `graph.js`):**
- `createGraph(canvas, {nodes, links, color, onOpen, onProject})` → `{setFilter(fn), highlight(ids), focus(id), fit(), zoom(k), resize(), destroy()}`;
  `nodes: [{id, kind: 'record'|'project', r, source?, label, pkey}]`, `links: [{s, t, w, kind: 'project'|'topic'}]`.
- Узлы проектов — `id` вида `p:<ключ>`.

**Требования (по спеке):** радиусы, цвета, прозрачность рёбер по весу; физика с Barnes-Hut выше 600 узлов,
~300 шагов, `prefers-reduced-motion` — без анимации; колесо/щипок/перетаскивание/кнопки; наведение гасит
остальное; клик по записи — `openRecord`, по проекту — `go('#project/<ключ>')`; подписи по масштабу;
строка фильтров (чипы источников с числами, период, «связи по темам», «найти на графе»); `#graph/<id>` —
фокус и подсветка записи; `devicePixelRatio`; `aria-label` со сводкой. Раскладка считается один раз
при первом открытии вкладки.

#### Task 7: Проекты

**Files:** `brain/web/app.js`, `brain/web/style.css`

**Требования:** сетка карточек (имя, пути, число записей, полоса долей по источникам с 2px зазорами,
`sparkline` за 12 недель, дата последней, до 5 тем из `PROJECTS[key].topics`), сортировка «недавние / больше
записей», фильтр; страница `#project/<ключ>` — крошка, цифры, «Решения и задачи» (10 последних атомов
`decision`/`todo` по `updated_at`, клик — `openRecord`), записи по месяцам (строки как в Task 8).

#### Task 8: Записи и здоровье базы

**Files:** `brain/web/app.js`, `brain/web/style.css`

**Требования:** блок источников (статус, ошибка, синхронизация, записей, без карточки, ещё идут, пропущено,
`hint` для пустых; пояснение одной строкой; очередь пересказа), лента с поиском и сегментами, группы по дням
(«Сегодня», «Вчера», «27 октября», год если не текущий), строка записи (точка, название, пересказ в одну строку,
проект, тег статуса только без карточки), 100 строк + «Показать ещё 100», «Найдено N из M».
Функцию строки записи `recordRow(r)` использует и страница проекта.

### Task 9: README и проверка на живой базе

**Files:** `README.md`

- [ ] **Step 1:** Раздел «Панель базы» в README переписать: вкладки Обзор, Граф, Проекты, Записи; граф —
записи вокруг проектов и связи по общим темам (TF-IDF по ключевым словам карточек); проекты склеиваются по имени
папки и репозитория; исходники страницы — `brain/web/`, собираются в один файл.
- [ ] **Step 2:** `/usr/bin/python3 -m unittest discover -s tests` — всё OK.
- [ ] **Step 3:** `bin/brain dashboard`; во встроенном браузере открыть `data/dashboard.html`: все вкладки
в светлой и тёмной теме и на ширине 375px; консоль без ошибок; граф раскладывается меньше чем за 2 секунды;
карточка открывается из обзора, графа, проекта и записей; `#graph/<id>` подсвечивает запись.
- [ ] **Step 4:** Commit: `git commit -m "README: новая панель базы"`.
