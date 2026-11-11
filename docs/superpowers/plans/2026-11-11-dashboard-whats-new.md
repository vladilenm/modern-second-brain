# Блок «Что нового» на обзоре панели — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** На вкладке «Обзор» панели показать, что изменилось в базе за период: по проектам — решения, задачи, ошибки и исправления из карточек новых и продолженных записей.

**Architecture:** Всё считается в браузере из данных, уже вшитых в страницу (`DATA.records`: `updated_at`, `started_at`, `pkey`, `atoms`). Новый код — один блок функций `news*` в `brain/web/app.js` на месте `fresh()`, разметка карточки в `index.html`, стили в `style.css`. Отметка «всё просмотрено» и выбранный период лежат в `localStorage` под ключом `brain.news`.

**Tech Stack:** обычный JavaScript без сборки (файлы вшиваются в HTML функцией `brain/dashboard.py:assemble`), CSS-переменные панели, Playwright-проверка на синтетических данных, Python 3.9 `unittest`.

Спецификация: `docs/superpowers/specs/2026-11-11-dashboard-whats-new-design.md`.

## Global Constraints

- Python, база, синхронизация, пересказ, поиск и MCP не меняются. Правки только в `brain/web/`, `tests/dashboard_fixture.py`, `README.md`.
- Панель остаётся одним автономным HTML-файлом: никаких новых зависимостей и сетевых запросов.
- В JS нельзя писать последовательности `</script` и `<!--`, в CSS — `</style`: `assemble` откажется собирать страницу.
- Весь текст, пришедший из данных, выводится через `esc()`.
- Новые стили используют только существующие переменные (`--space-*`, `--color-*`, `--radius-*`, `--font-*`).
- Тексты интерфейса — по-русски, точно как в плане.
- Порядок типов знаний: `decision`, `todo`, `error`, `fix`. Видимых знаний на проект — 5, названий записей без знаний — 3.
- Команды проверки:
  - `cd brain/web && npm run test:news` — новая браузерная проверка;
  - `cd brain/web && npm run test:graph` — существующая браузерная проверка;
  - `/usr/bin/python3 -m unittest discover -s tests` — Python-тесты (из корня репозитория).
- Браузерные проверки запускают установленный Chrome (`channel: 'chrome'`); другой канал задаётся переменной `BROWSER_CHANNEL`.

## Файлы

- `tests/dashboard_fixture.py` — добавляется третья страница `news.html` с данными для блока (существующие `dashboard.html` и `empty.html` не меняются: на них завязаны счётчики `test-graph.mjs`).
- `brain/web/build/test-news.mjs` — новая браузерная проверка блока.
- `brain/web/package.json` — скрипт `test:news`.
- `brain/web/index.html` — карточка «Что нового» вместо пары «Активные проекты / Свежие записи».
- `brain/web/app.js` — функции `news*` вместо `fresh()`; область соседей в обработчике клика по записи.
- `brain/web/style.css` — стили блока, удаление `.fresh-days`.
- `brain/web/build/test-graph.mjs` — одна строка: клик по записи теперь из `#news`.
- `README.md` — описание вкладки «Обзор» и команда новой проверки.

---

### Task 1: Блок «Что нового» с периодами «Сегодня» и «7 дней»

**Files:**
- Modify: `tests/dashboard_fixture.py`
- Create: `brain/web/build/test-news.mjs`
- Modify: `brain/web/package.json`
- Modify: `brain/web/index.html:57-69`
- Modify: `brain/web/app.js:422`, `brain/web/app.js:439`, `brain/web/app.js:490-503`
- Modify: `brain/web/style.css:247-250`
- Modify: `brain/web/build/test-graph.mjs:216`

**Interfaces:**
- Consumes (уже есть в `app.js`): `R`, `DATA`, `st`, `NOW`, `DAY`, `index()`, `esc`, `n`, `words`, `plural`, `date`, `ago`, `clock`, `color`, `srcName`, `projName`, `atomLabel`, `ATOM`, `RECORD_FORMS`, `PROJECT_FORMS`, `empty`, `seg`, `$`.
- Produces:
  - `news` — `{ range: "seen" | "today" | "7d", seen: string | null, store: boolean, open: Set<string> }`;
  - `NEWS_RANGES` — массив пар `[значение, подпись]`;
  - `newsModel()` → `{ groups: Array<{ key, records, main, rest, bare }>, count: number }`, где элементы `main` и `rest` — `{ type, text, r, cont }`;
  - `newsName(key)`, `newsTally(group)`, `newsPeriod()` → строки;
  - `drawNews()`, `initNews()`;
  - разметка: `#news`, `#news-sub`, `#news-range`, `#news-seen`, `#news-copy`; в блоке — `.ngroup`, `.tally`, `.nlist`, `.nlist.rest`, `.cont`, `.nbare`, кнопки `[data-nmore="<ключ>|main|rest|bare"]`.

- [ ] **Step 1: Добавить в фикстуру страницу `news.html`**

В `tests/dashboard_fixture.py` добавить `import copy` рядом с остальными импортами и вставить блок перед строкой `data["records"] = []`:

```python
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
```

Что получается за 7 дней (13 записей): «Проект-1» — записи 01–06 (6 решений, 1 задача-продолжение, 1 ошибка, 1 команда, 1 факт, 4 записи без знаний); «Проект-3» — записи 21–24 без знаний; «Проект-4» — записи 25–26 без знаний; без проекта — запись 20 с одним исправлением, обновлённая только что.

- [ ] **Step 2: Написать падающую браузерную проверку**

Создать `brain/web/build/test-news.mjs`:

```js
// «Что нового» на обзоре: периоды, группы проектов, раскрытие списков, отметка просмотра, Markdown.
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
import { chromium } from 'playwright';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const out = await mkdtemp(join(tmpdir(), 'brain-news-test-'));
execFileSync(process.env.PYTHON || 'python3', [join(root, 'tests/dashboard_fixture.py'), out], { cwd: root });
const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: true });
const failures = [];
// Отдельный контекст — отдельное хранилище: отметка просмотра не перетекает между проверками.
async function context(viewport = { width: 1280, height: 1000 }) {
  const ctx = await browser.newContext({ viewport });
  await ctx.route(/^https?:/, route => route.fulfill({ contentType: 'text/css', body: '' }));
  return ctx;
}
async function open(ctx, file = 'news.html', init) {
  const page = await ctx.newPage();
  page.on('pageerror', error => failures.push(error.stack));
  page.on('console', message => { if (message.type() === 'error') failures.push(message.text()); });
  if (init) await page.addInitScript(init);
  await page.goto(pathToFileURL(join(out, file)).href);
  await page.locator('#news').waitFor();
  return page;
}
const titles = page => page.locator('#news .ngroup h3').allInnerTexts();
const pressed = page => page.locator('#news-range [aria-pressed="true"]').innerText();

try {
  // Период по умолчанию, группы и их порядок.
  const page = await open(await context());
  assert.equal(await page.locator('#fresh').count(), 0, 'карточка «Свежие записи» убрана');
  assert.equal(await pressed(page), '7 дней');
  assert.equal(await page.locator('#news-range [data-v="seen"]').isHidden(), true, 'без отметки варианта «С прошлого просмотра» нет');
  assert.equal(await page.locator('#news-sub').innerText(), '13 записей, 3 проекта · за 7 дней');
  assert.deepEqual(await titles(page), ['Проект-1', 'Проект-3', 'Проект-4', 'Без проекта']);

  // Проект: итог, пять знаний, раскрытие остальных, пометка продолжения.
  const p1 = page.locator('#news .ngroup').first();
  assert.equal(await p1.locator('.tally').innerText(), '6 записей · 6 решений · 1 задача · 1 ошибка');
  assert.deepEqual(await p1.locator('.nlist .tag').allTextContents(), Array(5).fill('решение'));
  assert.equal(await p1.locator('[data-nmore$="|main"]').innerText(), 'ещё 3');
  await p1.locator('[data-nmore$="|main"]').click();
  assert.deepEqual((await p1.locator('.nlist .tag').allTextContents()).slice(5), ['решение', 'задача', 'ошибка']);
  assert.equal(await p1.locator('.cont').count(), 1, 'продолжение помечено один раз');
  assert.match(await p1.locator('.nlist li', { has: page.locator('.cont') }).innerText(), /Проверить шрифт на телефоне/);

  // Команды и факты свёрнуты, записи без знаний — строкой названий.
  assert.equal(await p1.locator('[data-nmore$="|rest"]').innerText(), 'команды и факты: 2');
  await p1.locator('[data-nmore$="|rest"]').click();
  assert.equal(await p1.locator('.nlist.rest li').count(), 2);
  assert.equal(await p1.locator('.nbare button[data-id]').count(), 3);
  await p1.locator('[data-nmore$="|bare"]').click();
  assert.equal(await p1.locator('.nbare button[data-id]').count(), 4);

  // Клик по знанию открывает его запись.
  await p1.locator('.nlist button').first().click();
  await page.locator('.drawer').waitFor();
  assert.equal(await page.locator('.drawer').getAttribute('data-id'), '1');
  await page.keyboard.press('Escape');

  // «Сегодня»: только запись без проекта, без ссылки на страницу проекта.
  await page.locator('#news-range [data-v="today"]').click();
  assert.deepEqual(await titles(page), ['Без проекта']);
  assert.equal(await page.locator('#news-sub').innerText(), '1 запись · сегодня');
  assert.equal(await page.locator('#news .ngroup h3 a').count(), 0);

  // Пустая база и узкий экран.
  const blank = await open(await context(), 'empty.html');
  assert.match(await blank.locator('#news').innerText(), /Записей пока нет/);
  const phone = await open(await context({ width: 390, height: 800 }));
  assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'нет горизонтальной прокрутки');

  assert.deepEqual(failures, [], 'нет ошибок в браузере');
  console.log('PASS: что нового — периоды, группы, раскрытие, запись по клику, пустая база, узкий экран');
} finally {
  await browser.close();
  await rm(out, { recursive: true, force: true });
}
```

В `brain/web/package.json` добавить скрипт после `test:graph`:

```json
    "test:graph": "node build/test-graph.mjs",
    "test:news": "node build/test-news.mjs"
```

- [ ] **Step 3: Убедиться, что проверка падает**

Run: `cd brain/web && npm run test:news`
Expected: FAIL — ожидание `#news` истекает по таймауту (элемента ещё нет).

- [ ] **Step 4: Разметка карточки**

В `brain/web/index.html` заменить блок `<div class="split">…</div>` (строки 57–69, обе карточки «Активные проекты» и «Свежие записи») на:

```html
      <div class="card">
        <div class="card-head">
          <div>
            <h2 class="card-title">Что нового</h2>
            <p class="card-sub" id="news-sub"></p>
          </div>
          <div class="news-tools">
            <div class="seg" id="news-range" role="group" aria-label="Период"></div>
            <button class="btn sm" type="button" id="news-seen" hidden>Всё просмотрено</button>
            <button class="btn sm" type="button" id="news-copy" hidden>Скопировать как Markdown</button>
          </div>
        </div>
        <div class="news" id="news"></div>
      </div>
      <div class="card">
        <h2 class="card-title">Активные проекты</h2>
        <p class="card-sub" id="week-sub"></p>
        <div class="fresh" id="week-projects"></div>
      </div>
```

Кнопки `#news-seen` и `#news-copy` остаются скрытыми до задач 2 и 3.

- [ ] **Step 5: Логика блока в `app.js`**

В `brain/web/app.js` удалить функцию `fresh()` целиком (строки 490–503) и на её место вставить:

```js
// ---- Что нового: знания из записей, изменившихся за период
const NEWS_MAIN = ["decision", "todo", "error", "fix"];
const NEWS_TOP = 5, NEWS_BARE = 3;
const NEWS_RANGES = [["seen", "С прошлого просмотра"], ["today", "Сегодня"], ["7d", "7 дней"]];
// seen — отметка «всё просмотрено» (время сборки панели); store — доступно ли хранилище; open — раскрытые списки.
const news = { range: "7d", seen: null, store: false, open: new Set() };
function newsStart() {
  if (news.range === "seen" && news.seen) return new Date(news.seen).getTime();
  if (news.range === "today") { const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime(); }
  return NOW - 7 * DAY;
}
// Группы по проекту: main — решения, задачи, ошибки, исправления; rest — команды, факты и прочее; bare — записи без знаний.
function newsModel() {
  const from = newsStart(), by = new Map();
  let count = 0;
  for (const r of R) {
    if (!(new Date(r.updated_at).getTime() > from)) continue;
    count++;
    const g = by.get(r.pkey) || by.set(r.pkey, { key: r.pkey, records: [], main: [], rest: [], bare: [] }).get(r.pkey);
    g.records.push(r);
    const atoms = index().get(r.id).atoms;
    if (!atoms.length) { g.bare.push(r); continue; }
    // Запись начата до начала периода — это продолжение: её знания могли появиться раньше.
    const cont = !!r.started_at && new Date(r.started_at).getTime() <= from;
    for (const a of atoms) (NEWS_MAIN.includes(a.type) ? g.main : g.rest).push({ type: a.type, text: a.text, r, cont });
  }
  const groups = [...by.values()];
  // R идёт от свежих к старым, сортировка устойчива: внутри типа порядок по свежести сохраняется.
  for (const g of groups) g.main.sort((a, b) => NEWS_MAIN.indexOf(a.type) - NEWS_MAIN.indexOf(b.type));
  groups.sort((a, b) => !a.key - !b.key || b.main.length - a.main.length
    || new Date(b.records[0].updated_at) - new Date(a.records[0].updated_at));
  return { groups, count };
}
const newsName = (key) => (key ? projName(key) : "Без проекта");
function newsTally(g) {
  const parts = [words(g.records.length, RECORD_FORMS)];
  for (const t of NEWS_MAIN) {
    const c = g.main.filter((a) => a.type === t).length;
    if (c) parts.push(words(c, ATOM[t]));
  }
  return parts.join(" · ");
}
function newsPeriod() {
  if (news.range === "today") return "сегодня";
  if (news.range === "seen" && news.seen) {
    const d = new Date(news.seen);
    return `с ${d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}, ${clock(d)}`;
  }
  return "за 7 дней";
}
const newsMore = (id, label) => `<button class="nmore" type="button" data-nmore="${esc(id)}">${esc(label)}</button>`;
function newsAtom(a) {
  const tone = a.type === "todo" ? "wait" : a.type === "error" ? "err" : "";
  return `<li><button type="button" data-id="${a.r.id}" title="${esc(a.r.title)}">
    <span class="dot" style="background:${color(a.r.source)}" title="${esc(srcName(a.r.source))}"></span>
    <span class="tag ${tone}">${esc(atomLabel(a.type))}</span>
    <span class="x">${esc(a.text)}</span>${a.cont ? `<span class="cont">продолжение</span>` : ""}
  </button></li>`;
}
function newsGroup(g) {
  const main = news.open.has(g.key + "|main") ? g.main : g.main.slice(0, NEWS_TOP);
  const bare = news.open.has(g.key + "|bare") ? g.bare : g.bare.slice(0, NEWS_BARE);
  const head = g.key ? `<a href="#project/${encodeURIComponent(g.key)}">${esc(newsName(g.key))}</a>` : esc(newsName(g.key));
  const rest = !g.rest.length ? ""
    : news.open.has(g.key + "|rest") ? `<ul class="nlist rest">${g.rest.map(newsAtom).join("")}</ul>`
    : newsMore(g.key + "|rest", `команды и факты: ${n(g.rest.length)}`);
  const bareLine = !g.bare.length ? "" : `<p class="nbare">Без знаний: ${bare.map((r) => `<button type="button" data-id="${r.id}">${esc(r.title)}</button>`).join(", ")}${
    g.bare.length > bare.length ? " " + newsMore(g.key + "|bare", `ещё ${n(g.bare.length - bare.length)}`) : ""}</p>`;
  return `<section class="ngroup">
    <h3>${head}</h3>
    <p class="tally">${esc(newsTally(g))}</p>
    ${main.length ? `<ul class="nlist">${main.map(newsAtom).join("")}</ul>` : ""}
    ${g.main.length > main.length ? newsMore(g.key + "|main", `ещё ${n(g.main.length - main.length)}`) : ""}
    ${rest}
    ${bareLine}
  </section>`;
}
function drawNews() {
  const m = newsModel(), projects = m.groups.filter((g) => g.key).length;
  const counts = m.count ? [words(m.count, RECORD_FORMS), projects && words(projects, PROJECT_FORMS)].filter(Boolean).join(", ") : "";
  $("#news-sub").textContent = [counts, newsPeriod()].filter(Boolean).join(" · ");
  if (!m.count) {
    const last = date(st.last_sync_at);
    $("#news").innerHTML = !R.length ? empty("Записей пока нет", "Запусти <code>brain sync</code>.")
      : empty(news.range === "seen" ? "С прошлого просмотра ничего не изменилось" : "За этот период записей не было",
        last ? `Синхронизация ${esc(ago(last))}.` : "Синхронизации ещё не было.");
    return;
  }
  $("#news").innerHTML = m.groups.map(newsGroup).join("");
}
function initNews() {
  seg($("#news-range"), NEWS_RANGES, news.range, (v) => { news.range = v; news.open.clear(); drawNews(); });
  $('#news-range [data-v="seen"]').hidden = !news.seen;
  $("#news").addEventListener("click", (e) => {
    const b = e.target.closest("[data-nmore]");
    if (!b) return;
    news.open.add(b.dataset.nmore);
    drawNews();
  });
  drawNews();
}
```

В `renderOverview()` (строка 439) заменить вызов `fresh();` на `initNews();`.

В общем обработчике клика по записи (строка 422) заменить область соседей, чтобы стрелки в карточке записи ходили по всему блоку:

```js
  const scope = b.closest(".feed, .news, .atoms") || b.parentElement;
```

- [ ] **Step 6: Стили**

В `brain/web/style.css` удалить четыре правила `.fresh-days…` (строки 247–250) и на их место вставить:

```css
.news-tools { display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; }
.news { display: grid; gap: var(--space-5); margin-top: var(--space-4); }
.ngroup { min-width: 0; }
.ngroup + .ngroup { padding-top: var(--space-5); border-top: 1px solid var(--color-line); }
.ngroup h3 { margin: 0; font: 600 16px/1.4 var(--font-body); }
.ngroup h3 a { color: inherit; text-decoration: none; }
.ngroup h3 a:hover { color: var(--color-accent-text); text-decoration: underline; }
.ngroup .tally { margin: 2px 0 0; font: 400 12px/1.5 var(--font-mono); color: var(--color-ink-subtle); }
.nlist { margin: var(--space-2) 0 0; padding: 0; list-style: none; }
.nlist button {
  display: flex; align-items: flex-start; gap: var(--space-2); width: calc(100% + 2 * var(--space-2)); margin: 0 calc(-1 * var(--space-2));
  padding: 8px var(--space-2); border: 0; border-radius: var(--radius-sm); background: none; cursor: pointer;
  font: 400 15px/1.5 var(--font-body); color: inherit; text-align: left;
}
.nlist button:hover { background: var(--color-surface-sunken); }
.nlist .dot { margin-top: 7px; }
.nlist .tag { flex: none; margin-top: 1px; }
.nlist .x { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.nlist .cont { flex: none; margin-top: 3px; font: 400 12px var(--font-mono); color: var(--color-ink-subtle); }
.nmore { padding: 0; margin-top: var(--space-2); border: 0; background: none; cursor: pointer; font: 400 14px var(--font-body); color: var(--color-accent-text); }
.nmore:hover { text-decoration: underline; }
.nbare { margin: var(--space-2) 0 0; font-size: 14px; line-height: 1.6; color: var(--color-ink-muted); }
.nbare button { display: inline; padding: 0; margin: 0; border: 0; background: none; cursor: pointer; font: inherit; color: var(--color-ink); text-align: left; }
.nbare button:hover { text-decoration: underline; }
.nbare .nmore { color: var(--color-accent-text); }
@media (max-width: 640px) { .nlist button { flex-wrap: wrap; } .nlist .x { flex-basis: 100%; } }
```

- [ ] **Step 7: Поправить существующую проверку графа**

В `brain/web/build/test-graph.mjs` (строка 216) карточки `#fresh` больше нет. Заменить:

```js
  await home.locator('#fresh button[data-id]').first().click();
```

на:

```js
  await home.locator('#news button[data-id]').first().click();
```

- [ ] **Step 8: Прогнать проверки**

Run: `cd brain/web && npm run test:news`
Expected: `PASS: что нового — периоды, группы, раскрытие, запись по клику, пустая база, узкий экран`

Run: `cd brain/web && npm run test:graph`
Expected: строка, начинающаяся с `PASS: offline 3D`

Run (из корня): `/usr/bin/python3 -m unittest discover -s tests`
Expected: `OK`

- [ ] **Step 9: Commit**

```bash
git add tests/dashboard_fixture.py brain/web/build/test-news.mjs brain/web/build/test-graph.mjs brain/web/package.json brain/web/index.html brain/web/app.js brain/web/style.css
git commit -m "feat: блок «Что нового» на обзоре панели"
```

---

### Task 2: Отметка «Всё просмотрено» и запоминание периода

**Files:**
- Modify: `brain/web/app.js` (блок `news*` из задачи 1)
- Modify: `brain/web/build/test-news.mjs`

**Interfaces:**
- Consumes: `news`, `NEWS_RANGES`, `drawNews()`, `initNews()` из задачи 1; `DATA.generated_at` (ISO-время сборки панели); кнопка `#news-seen`.
- Produces:
  - ключ `localStorage` `brain.news` со значением `{"seen": "<ISO или null>", "range": "seen" | "today" | "7d"}`;
  - `saveNews()`, `syncNewsTools()`, `markSeen()`.

- [ ] **Step 1: Дописать падающие проверки**

В `brain/web/build/test-news.mjs` вставить перед строкой `assert.deepEqual(failures, [], 'нет ошибок в браузере');`:

```js
  // Отметка просмотра: пишется время сборки панели, блок пустеет, выбор переживает перезагрузку.
  const mark = await open(await context());
  assert.equal(await mark.locator('#news-seen').isVisible(), true);
  await mark.locator('#news-seen').click();
  const built = await mark.evaluate(() => DATA.generated_at);
  assert.deepEqual(await mark.evaluate(() => JSON.parse(localStorage.getItem('brain.news'))), { seen: built, range: 'seen' });
  assert.equal(await pressed(mark), 'С прошлого просмотра');
  assert.match(await mark.locator('#news').innerText(), /С прошлого просмотра ничего не изменилось/);
  assert.equal(await mark.locator('#news-seen').isHidden(), true, 'повторно отмечать нечего');
  await mark.reload();
  await mark.locator('#news').waitFor();
  assert.equal(await pressed(mark), 'С прошлого просмотра');
  assert.match(await mark.locator('#news').innerText(), /ничего не изменилось/);
  await mark.locator('#news-range [data-v="7d"]').click();
  assert.equal((await titles(mark)).length, 4);
  await mark.reload();
  await mark.locator('#news').waitFor();
  assert.equal(await pressed(mark), '7 дней', 'период запоминается');

  // Отметка полуторасуточной давности: видны только записи новее неё.
  await mark.evaluate(() => localStorage.setItem('brain.news', JSON.stringify({ seen: new Date(Date.now() - 36 * 3600e3).toISOString(), range: 'seen' })));
  await mark.reload();
  await mark.locator('#news').waitFor();
  assert.deepEqual(await titles(mark), ['Проект-1', 'Проект-3', 'Без проекта']);
  assert.match(await mark.locator('#news-sub').innerText(), /^3 записи, 2 проекта · с \d+ [а-я]+, \d\d:\d\d$/);
  assert.equal(await mark.locator('#news-seen').isVisible(), true);

  // Хранилище недоступно: кнопки и варианта «С прошлого просмотра» нет, блок работает.
  const locked = await open(await context(), 'news.html', () => { Storage.prototype.setItem = () => { throw new Error('denied'); }; });
  assert.equal(await locked.locator('#news-seen').isHidden(), true);
  assert.equal(await locked.locator('#news-range [data-v="seen"]').isHidden(), true);
  assert.equal((await titles(locked)).length, 4);
```

И заменить итоговую строку `console.log(...)` на:

```js
  console.log('PASS: что нового — периоды, группы, раскрытие, запись по клику, пустая база, узкий экран, отметка просмотра, хранилище');
```

- [ ] **Step 2: Убедиться, что проверка падает**

Run: `cd brain/web && npm run test:news`
Expected: FAIL на `assert.equal(await mark.locator('#news-seen').isVisible(), true)` — кнопка скрыта.

- [ ] **Step 3: Чтение и запись отметки**

В `brain/web/app.js` сразу после строки `const news = { range: "7d", seen: null, store: false, open: new Set() };` вставить:

```js
const NEWS_KEY = "brain.news";
try {
  // getItem в приватном режиме может молча вернуть null, а запись — упасть: проверяем именно запись.
  localStorage.setItem(NEWS_KEY + ".probe", "1");
  localStorage.removeItem(NEWS_KEY + ".probe");
  news.store = true;
  const saved = JSON.parse(localStorage.getItem(NEWS_KEY)) || {};
  if (typeof saved.seen === "string" && !isNaN(Date.parse(saved.seen))) news.seen = saved.seen;
  if (NEWS_RANGES.some(([v]) => v === saved.range) && (saved.range !== "seen" || news.seen)) news.range = saved.range;
} catch { /* без хранилища отметку не ведём */ }
const saveNews = () => { try { localStorage.setItem(NEWS_KEY, JSON.stringify({ seen: news.seen, range: news.range })); } catch { /* не запомним */ } };
function syncNewsTools() {
  $('#news-range [data-v="seen"]').hidden = !news.seen;
  $("#news-range").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === news.range));
  $("#news-seen").hidden = !news.store || news.seen === DATA.generated_at;
}
// В отметку идёт время сборки панели, а не клика: записи, попавшие в базу между ними, не теряются.
function markSeen() {
  news.seen = DATA.generated_at;
  news.range = "seen";
  news.open.clear();
  saveNews();
  drawNews();
}
```

В `drawNews()` первой строкой тела добавить вызов:

```js
  syncNewsTools();
```

Заменить `initNews()` целиком на:

```js
function initNews() {
  seg($("#news-range"), NEWS_RANGES, news.range, (v) => { news.range = v; news.open.clear(); saveNews(); drawNews(); });
  $("#news-seen").addEventListener("click", markSeen);
  $("#news").addEventListener("click", (e) => {
    const b = e.target.closest("[data-nmore]");
    if (!b) return;
    news.open.add(b.dataset.nmore);
    drawNews();
  });
  drawNews();
}
```

- [ ] **Step 4: Прогнать проверки**

Run: `cd brain/web && npm run test:news`
Expected: `PASS: что нового — … отметка просмотра, хранилище`

Run: `cd brain/web && npm run test:graph`
Expected: строка, начинающаяся с `PASS: offline 3D`

- [ ] **Step 5: Commit**

```bash
git add brain/web/app.js brain/web/build/test-news.mjs
git commit -m "feat: отметка «Всё просмотрено» в блоке «Что нового»"
```

---

### Task 3: Копирование сводки как Markdown, README, проверка на реальной базе

**Files:**
- Modify: `brain/web/app.js` (блок `news*`)
- Modify: `brain/web/build/test-news.mjs`
- Modify: `README.md:299-300`, `README.md:338-339`

**Interfaces:**
- Consumes: `newsModel()`, `newsName()`, `newsTally()`, `newsPeriod()`, `drawNews()`, `initNews()` из задач 1–2; существующие `atomLabel(type)` и `copyText(button)` (копирует `button.dataset.copy`); кнопка `#news-copy`.
- Produces: `newsMarkdown()` → строка Markdown.

- [ ] **Step 1: Дописать падающую проверку**

В `brain/web/build/test-news.mjs` вставить перед строкой `assert.deepEqual(failures, [], 'нет ошибок в браузере');`:

```js
  // Markdown: все шесть типов знаний без обрезки, записи без знаний одной строкой.
  const copy = await open(await context());
  assert.equal(await copy.locator('#news-copy').isVisible(), true);
  const md = await copy.evaluate(() => newsMarkdown());
  assert.match(md, /^# Что нового за 7 дней\n\n## Проект-1\n6 записей · 6 решений · 1 задача · 1 ошибка\n\n- \*\*решение:\*\* Решение 1\n/);
  assert.equal((md.match(/\*\*решение:\*\*/g) || []).length, 6, 'все решения, а не первые пять');
  assert(md.includes('- **команда:** brain sync --rescan'));
  assert(md.includes('- **факт:** Субтитры хранятся в srt'));
  assert(md.includes('- без знаний: Запись 03; Запись 04; Запись 05; Запись 06'));
  assert(md.endsWith('## Без проекта\n1 запись · 1 исправление\n\n- **исправление:** Исправили путь к базе\n'));
  await copy.locator('#news-range [data-v="today"]').click();
  assert.match(await copy.evaluate(() => newsMarkdown()), /^# Что нового сегодня\n\n## Без проекта\n/);
```

И дополнить итоговую строку `console.log(...)`: в конец текста перед закрывающей кавычкой добавить `, Markdown`.

- [ ] **Step 2: Убедиться, что проверка падает**

Run: `cd brain/web && npm run test:news`
Expected: FAIL на `assert.equal(await copy.locator('#news-copy').isVisible(), true)` — кнопка скрыта.

- [ ] **Step 3: Реализация**

В `brain/web/app.js` перед функцией `drawNews()` вставить:

```js
function newsMarkdown() {
  return `# Что нового ${newsPeriod()}\n\n` + newsModel().groups.map((g) => {
    const lines = [...g.main, ...g.rest].map((a) => `- **${atomLabel(a.type)}:** ${a.text}`);
    if (g.bare.length) lines.push(`- без знаний: ${g.bare.map((r) => r.title).join("; ")}`);
    return `## ${newsName(g.key)}\n${newsTally(g)}\n\n${lines.join("\n")}`;
  }).join("\n\n") + "\n";
}
```

В `drawNews()` сразу после строки `const m = newsModel(), projects = …;` добавить:

```js
  $("#news-copy").hidden = !m.count;
```

В `initNews()` после строки с `#news-seen` добавить:

```js
  $("#news-copy").addEventListener("click", (e) => { e.currentTarget.dataset.copy = newsMarkdown(); copyText(e.currentTarget); });
```

- [ ] **Step 4: README**

В `README.md` в разделе «Панель базы» заменить пункт про «Обзор» (строки 299–300):

```markdown
- **Обзор** (`#overview`) — записей, проектов, знаний из карточек, доля карточек; записи по неделям
  по источникам (3 месяца, год, всё время); проекты с наибольшим числом записей; свежие записи.
```

на:

```markdown
- **Обзор** (`#overview`) — записей, проектов, знаний из карточек; «Что нового»; проекты с наибольшим
  числом записей за неделю; записи по неделям по источникам (3 месяца, год, всё время).
  «Что нового» показывает по проектам решения, задачи, ошибки и исправления из записей, изменившихся
  за период: сегодня, 7 дней или с прошлого просмотра. Отметка просмотра хранится в браузере и сдвигается
  только кнопкой «Всё просмотрено». Команды и факты свёрнуты, записи без знаний идут строкой названий.
  У продолженного разговора показываются все его знания с пометкой «продолжение»: у знаний нет своей даты.
  «Скопировать как Markdown» отдаёт сводку периода целиком.
```

В том же разделе после предложения про `npm run test:graph` (строки 338–339, оканчивается на
«канал можно задать через `BROWSER_CHANNEL`).») добавить строку:

```markdown
`npm run test:news` так же проверяет блок «Что нового» на обзоре.
```

- [ ] **Step 5: Прогнать все проверки**

Run: `cd brain/web && npm run test:news`
Expected: `PASS: что нового — … отметка просмотра, хранилище, Markdown`

Run: `cd brain/web && npm run test:graph`
Expected: строка, начинающаяся с `PASS: offline 3D`

Run (из корня): `/usr/bin/python3 -m unittest discover -s tests`
Expected: `OK`

- [ ] **Step 6: Проверка на реальной базе**

Run (из корня): `bin/brain dashboard --open`

В браузере на вкладке «Обзор» проверить и записать результат каждого пункта:

1. Блок «Что нового» стоит под строкой счётчиков, выбран период «7 дней», в подзаголовке число записей и проектов.
2. Проекты упорядочены по числу знаний, «Без проекта» — последняя группа.
3. Клик по знанию открывает запись; стрелки ← → в карточке ходят по записям блока.
4. «ещё N» и «команды и факты: N» раскрывают списки.
5. «Всё просмотрено» → блок сообщает, что ничего не изменилось; после перезагрузки страницы состояние то же.
6. После `bin/brain sync` и повторного `bin/brain dashboard --open` в периоде «С прошлого просмотра» видны только записи новее отметки.
7. «Скопировать как Markdown» кладёт сводку в буфер обмена.
8. Окно шириной около 390 px: нет горизонтальной прокрутки, кнопки переносятся.
9. Тёмная тема системы: текст и пометки читаются.

Если страницы Notion заполняют сводку так, что остальных проектов не видно, — не чинить в этой задаче, а сообщить: фильтр по источникам вынесен за рамки спецификации.

- [ ] **Step 7: Commit**

```bash
git add brain/web/app.js brain/web/build/test-news.mjs README.md
git commit -m "feat: копирование сводки «Что нового» как Markdown"
```
