/* Данные, вкладки, адреса, поиск, карточка записи и вкладки Обзор, Граф, Проекты, Записи. */

// ---- Хелперы
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = new Intl.NumberFormat("ru-RU");
const n = (x) => nf.format(x);
const date = (iso) => (iso ? new Date(iso) : null);
const DAY = 864e5;
const NOW = Date.now();
const thisYear = new Date().getFullYear();
const dayMonth = (d) => d.toLocaleDateString("ru-RU", { day: "numeric", month: "short", ...(d.getFullYear() !== thisYear && { year: "numeric" }) }).replace(".", "").replace(" г", "");
const fullDate = (d) => d.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
const clock = (d) => d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
const stamp = (d) => (d ? `${fullDate(d)}, ${clock(d)}` : "—");
const monthName = (d) => d.toLocaleDateString("ru-RU", { month: "long", year: "numeric" }).replace(" г.", "");
const plural = (x, one, few, many) => {
  const a = Math.abs(x) % 100, b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  return b === 1 ? one : many;
};
const words = (x, forms) => `${n(x)} ${plural(x, ...forms)}`;
function ago(d) {
  const m = Math.round((NOW - d.getTime()) / 60000);
  if (m < 1) return "только что";
  if (m < 60) return `${m} мин назад`;
  if (m < 60 * 24) return `${Math.round(m / 60)} ч назад`;
  return `${Math.round(m / 1440)} дн назад`;
}
const daysAgo = (iso) => (NOW - new Date(iso).getTime()) / DAY;
const RECORD_FORMS = ["запись", "записи", "записей"];

// ---- Данные
const R = DATA.records;                                   // свежие сверху
const byId = new Map(R.map((r) => [r.id, r]));
const PROJECTS = DATA.projects || {};
const st = DATA.status || {};
// Порядок слотов палитры = порядок стопки в графиках (проверен валидатором именно так).
const SLOT_ORDER = ["claude_code", "codex", "github", "chatgpt", "gmail", "yandex_mail"];
const SRC = DATA.sources_meta
  .map((m, i) => ({ ...m, order: SLOT_ORDER.includes(m.source) ? SLOT_ORDER.indexOf(m.source) : SLOT_ORDER.length + i }))
  .sort((a, b) => a.order - b.order)
  .map((m, i) => ({ ...m, slot: i + 1, count: R.filter((r) => r.source === m.source).length }));
const SRC_BY = Object.fromEntries(SRC.map((s) => [s.source, s]));
const colorVar = (source) => `--series-${Math.min(6, SRC_BY[source]?.slot || 6)}`;   // больше шести слотов в палитре нет
const color = (source) => `var(${colorVar(source)}, var(--color-ink-subtle))`;
const srcName = (source) => SRC_BY[source]?.title || source;
const projName = (key) => (PROJECTS[key] ? PROJECTS[key].name : "без проекта");
const ATOM = {
  decision: ["решение", "решения", "решений"], fix: ["исправление", "исправления", "исправлений"],
  todo: ["задача", "задачи", "задач"], command: ["команда", "команды", "команд"],
  error: ["ошибка", "ошибки", "ошибок"], fact: ["факт", "факта", "фактов"],
};
function parseAtom(line) {
  const m = line.match(/^\[(\w+)\]\s*(.*)$/s);
  return m ? { type: m[1], text: m[2] } : { type: "", text: line };
}
const NB = new Map();
for (const [a, b, w] of DATA.graph?.edges || []) {
  if (!byId.has(a) || !byId.has(b)) continue;
  (NB.get(a) || NB.set(a, []).get(a)).push([b, w]);
  (NB.get(b) || NB.set(b, []).get(b)).push([a, w]);
}
NB.forEach((list) => list.sort((x, y) => y[1] - x[1]));
const neighbors = (id) => NB.get(id) || [];
const pending = R.filter((r) => !r.card).length;
const failed = R.filter((r) => !r.card && r.error).length;
const syncErrors = Object.entries(DATA.sync).filter(([, s]) => s && s.last_error);
const haystack = new Map(R.map((r) => [r.id, [r.title, projName(r.pkey), r.project, r.keywords, r.summary].join(" ").toLowerCase()]));
const matchAll = (r, q) => q.every((w) => haystack.get(r.id).includes(w));
const terms = (q) => q.trim().toLowerCase().split(/\s+/).filter(Boolean);

// ---- Состояние в меню
(function sideState() {
  const p = st.progress || {};
  const last = date(st.last_sync_at);
  const kind = { sync: "run", summarize: "run", error: "err", interrupted: "err" }[DATA.state] || (DATA.state === "none" ? "err" : "");
  const now = {
    sync: "Забираю новое из источников",
    summarize: `Пересказываю ${Math.min((p.done || 0) + 1, p.total || 0)} из ${p.total || 0}`,
    error: "Последний прогон упал",
    interrupted: "Последний прогон прервался",
    none: "Синхронизации ещё не было",
  }[DATA.state] || (last ? `Синхронизация ${ago(last)}` : "Синхронизации ещё не было");
  // Текст ошибки часто уже начинается с имени источника («GitHub: нет связи»).
  const errText = (src, e) => (e.startsWith(srcName(src) + ":") ? e.slice(srcName(src).length + 1).trim() : e);
  const errs = syncErrors.map(([src, s]) => `<p class="err"><b>${esc(srcName(src))}:</b> ${esc(errText(src, s.last_error))}</p>`).join("");
  $("#side-state").innerHTML = `
    <p class="now"><span class="state-dot ${kind}" aria-hidden="true"></span>${esc(now)}</p>${errs}
    <p class="built">собрано в ${clock(date(DATA.generated_at))}</p>`;
  const dot = $("#topbar-dot");
  dot.className = `state-dot ${syncErrors.length ? "err" : kind}`;
  dot.setAttribute("aria-label", now + (syncErrors.length ? `, ошибок в источниках: ${syncErrors.length}` : ""));
  dot.title = dot.getAttribute("aria-label");
})();

// ---- Адреса вкладок
const TABS = {
  overview: { title: "Обзор", render: renderOverview },
  graph: { title: "Граф", render: renderGraph },
  projects: { title: "Проекты", render: renderProjects },
  records: { title: "Записи", render: renderRecords },
};
let current = null;
function parseHash() {
  const [tab, ...rest] = location.hash.slice(1).split("/");
  const arg = decodeURIComponent(rest.join("/"));
  if (tab === "project") return { tab: "projects", arg };
  return TABS[tab] ? { tab, arg } : { tab: "overview", arg: "" };
}
function route() {
  const r = parseHash();
  const changed = !current || current.tab !== r.tab || current.arg !== r.arg;
  document.querySelectorAll("section.tab").forEach((s) => { s.hidden = s.dataset.tab !== r.tab; });
  document.querySelectorAll(".nav a").forEach((a) => {
    if (a.dataset.tab === r.tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  $("#topbar-tab").textContent = TABS[r.tab].title;
  document.title = `${TABS[r.tab].title} · Второй мозг`;
  closeMenu();
  Charts.tip.hide();
  const prev = current;
  current = r;
  TABS[r.tab].render(r.arg);
  if (prev && changed && !(prev.tab === "graph" && r.tab === "graph")) {
    scrollTo(0, 0);
    const h = $(`section[data-tab="${r.tab}"] h1:not([hidden])`) || $("#project-view h1");
    if (h && !$(".drawer")) h.focus({ preventScroll: true });
  }
}
const go = (hash) => { if (location.hash === hash) route(); else location.hash = hash; };
addEventListener("hashchange", route);

// ---- Меню на узком экране
const side = $("#side"), scrim = $("#side-scrim"), menuBtn = $("#menu-open");
function openMenu() { side.classList.add("open"); scrim.hidden = false; menuBtn.setAttribute("aria-expanded", "true"); }
function closeMenu() { side.classList.remove("open"); scrim.hidden = true; menuBtn.setAttribute("aria-expanded", "false"); }
menuBtn.addEventListener("click", openMenu);
scrim.addEventListener("click", closeMenu);

// ---- Быстрый поиск
(function search() {
  const input = $("#q-global"), list = $("#q-results");
  let found = [], sel = -1;
  function render() {
    const q = terms(input.value);
    if (!q.length) { list.hidden = true; input.setAttribute("aria-expanded", "false"); return; }
    found = R.filter((r) => matchAll(r, q)).slice(0, 10);
    sel = found.length ? 0 : -1;
    list.innerHTML = found.length
      ? found.map((r, i) => `<li role="option" id="qr-${i}" data-id="${r.id}" aria-selected="${i === sel}">${esc(r.title)}<small>${esc(srcName(r.source))}, ${esc(projName(r.pkey))}, ${esc(dayMonth(date(r.updated_at)))}</small></li>`).join("")
      : `<li class="none">Ничего не нашлось</li>`;
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }
  function mark() {
    list.querySelectorAll("li[data-id]").forEach((li, i) => li.setAttribute("aria-selected", i === sel));
    input.setAttribute("aria-activedescendant", sel >= 0 ? `qr-${sel}` : "");
  }
  function pick(id) { list.hidden = true; input.blur(); closeMenu(); openRecord(id); }
  input.addEventListener("input", render);
  input.addEventListener("focus", render);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && found.length) { sel = (sel + 1) % found.length; mark(); e.preventDefault(); }
    else if (e.key === "ArrowUp" && found.length) { sel = (sel - 1 + found.length) % found.length; mark(); e.preventDefault(); }
    else if (e.key === "Enter" && sel >= 0) pick(found[sel].id);
    else if (e.key === "Escape") { input.value = ""; render(); input.blur(); }
  });
  list.addEventListener("mousedown", (e) => { const li = e.target.closest("li[data-id]"); if (li) { e.preventDefault(); pick(+li.dataset.id); } });
  input.addEventListener("blur", () => setTimeout(() => { list.hidden = true; }, 100));
  addEventListener("keydown", (e) => {
    const t = e.target;
    if (e.key === "/" && !e.metaKey && !e.ctrlKey && !(t.closest && t.closest("input, textarea, [contenteditable]"))) {
      e.preventDefault();
      if (innerWidth < 1024) openMenu();
      input.focus();
      input.select();
    }
  });
})();

// ---- Общие куски разметки
function seg(el, options, value, onChange) {
  el.innerHTML = options.map(([v, label]) => `<button type="button" data-v="${esc(v)}" aria-pressed="${v === value}">${esc(label)}</button>`).join("");
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    el.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    onChange(b.dataset.v);
  });
}
function recordRow(r) {
  const status = r.card ? "" : r.error ? `<span class="tag err">ошибка</span>` : `<span class="tag wait">ждёт</span>`;
  return `<button class="rrow" type="button" data-id="${r.id}">
    <span class="dot" style="background:${color(r.source)}" title="${esc(srcName(r.source))}"></span>
    <span><span class="t">${esc(r.title)}</span>${r.summary ? `<span class="s">${esc(r.summary)}</span>` : ""}</span>
    <span class="p">${status}<span class="mono" title="${esc(r.project)}">${esc(projName(r.pkey))}</span></span>
  </button>`;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-id]:not(li)");
  if (b && !b.closest(".drawer")) openRecord(+b.dataset.id);
});
function weekStart(d) {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
  return x;
}
const empty = (title, text) => `<div class="empty"><b>${esc(title)}</b>${text}</div>`;

// ---- 01 Обзор
let overviewReady = false, range = "3m";
function renderOverview() {
  if (!overviewReady) {
    overviewReady = true;
    lede();
    kpis();
    topProjects();
    fresh();
    seg($("#weekly-range"), [["3m", "3 месяца"], ["1y", "Год"], ["all", "Всё"]], range, (v) => { range = v; drawWeekly(); });
  }
  drawWeekly();
}
function lede() {
  const gen = date(DATA.generated_at), last = date(st.last_sync_at);
  const parts = [`Панель собрана в <b>${clock(gen)}</b>.`];
  if (last) parts.push(`Последняя синхронизация в <b>${clock(last)}</b>, ${ago(last)}.`);
  parts.push(pending ? `Без карточки ${words(pending, RECORD_FORMS)}, их перескажут следующие синхронизации.` : "Всё забрано и пересказано.");
  $("#lede").innerHTML = parts.join(" ");
}
function kpis() {
  const active7 = R.filter((r) => daysAgo(r.updated_at) <= 7).length;
  const keys = Object.keys(PROJECTS).filter(Boolean);
  const live30 = new Set(R.filter((r) => r.pkey && daysAgo(r.updated_at) <= 30).map((r) => r.pkey)).size;
  const atoms = {};
  for (const r of R) for (const a of r.atoms) { const t = parseAtom(a).type; atoms[t] = (atoms[t] || 0) + 1; }
  const atomTotal = Object.values(atoms).reduce((a, b) => a + b, 0);
  const atomNote = Object.entries(atoms).filter(([t]) => t !== "fact" && ATOM[t]).sort((a, b) => b[1] - a[1]).slice(0, 3)
    .map(([t, c]) => words(c, ATOM[t])).join(", ");
  const pct = R.length ? Math.round(((R.length - pending) / R.length) * 100) : 0;
  const bySource = SRC.filter((s) => s.count).map((s) => `${esc(s.title)} ${n(s.count)}`).join(", ");
  const cardsNote = failed ? `${words(failed, ["запись", "записи", "записей"])} не удалось пересказать`
    : pending ? `${words(pending, RECORD_FORMS)} ждут пересказа` : "очередь пересказа пуста";
  const tiles = [
    ["Записей", n(R.length), R.length ? `${n(active7)} активных за 7 дней. ${bySource}` : "Запусти <code>brain sync</code>, чтобы забрать первые"],
    ["Проектов", n(keys.length), `${n(live30)} живых за 30 дней`],
    ["Знаний", n(atomTotal), atomTotal ? atomNote || "только факты" : "появятся в карточках после пересказа"],
    ["Карточки", `${pct}%`, cardsNote, pct],
  ];
  $("#kpis").innerHTML = tiles.map(([label, value, note, bar]) => `
    <div class="card kpi">
      <p class="label" style="margin:0">${label}</p>
      <p class="value">${value}</p>
      <p class="note">${note}</p>
      ${bar !== undefined ? `<div class="meter" role="progressbar" aria-label="Записи с карточкой" aria-valuenow="${bar}" aria-valuemin="0" aria-valuemax="100"><span style="width:${bar}%"></span></div>` : ""}
    </div>`).join("");
}
function weeklyBuckets() {
  const series = SRC.map((s) => ({ key: s.source, title: esc(s.title), color: color(s.source) }));
  let buckets;
  if (range === "all") {
    const first = R.length ? date(R[R.length - 1].updated_at) : new Date();
    const start = new Date(first.getFullYear(), first.getMonth(), 1), end = new Date();
    buckets = [];
    for (let d = new Date(start); d <= end; d.setMonth(d.getMonth() + 1)) {
      const m = new Date(d);
      buckets.push({ key: `${m.getFullYear()}-${m.getMonth()}`, label: m.toLocaleDateString("ru-RU", { month: "short" }).replace(".", "") + (m.getMonth() === 0 ? ` ${String(m.getFullYear()).slice(2)}` : ""), tip: monthName(m), values: {} });
    }
    const at = new Map(buckets.map((b, i) => [b.key, i]));
    for (const r of R) { const d = date(r.updated_at); const i = at.get(`${d.getFullYear()}-${d.getMonth()}`); if (i != null) buckets[i].values[r.source] = (buckets[i].values[r.source] || 0) + 1; }
  } else {
    const weeks = range === "3m" ? 13 : 52, now = weekStart(new Date());
    buckets = Array.from({ length: weeks }, (_, i) => {
      const s = new Date(now); s.setDate(s.getDate() - (weeks - 1 - i) * 7);
      const e = new Date(s); e.setDate(e.getDate() + 6);
      return { start: s, label: dayMonth(s), tip: `${dayMonth(s)} – ${dayMonth(e)}`, values: {} };
    });
    for (const r of R) {
      const i = Math.round((weekStart(date(r.updated_at)) - buckets[0].start) / (7 * DAY));
      if (i >= 0 && i < weeks) buckets[i].values[r.source] = (buckets[i].values[r.source] || 0) + 1;
    }
  }
  return { buckets, series };
}
function drawWeekly() {
  const { buckets, series } = weeklyBuckets();
  const totals = {};
  for (const b of buckets) for (const [k, v] of Object.entries(b.values)) totals[k] = (totals[k] || 0) + v;
  const sum = Object.values(totals).reduce((a, b) => a + b, 0);
  const unit = range === "all" ? "по месяцам" : "по неделям";
  $("#weekly-sub").textContent = `${words(sum, RECORD_FORMS)} ${unit}, по дате последней активности`;
  $("#weekly-legend").innerHTML = series.filter((s) => totals[s.key])
    .map((s) => `<span><span class="swatch" style="background:${s.color}"></span>${s.title} <span class="num">${n(totals[s.key])}</span></span>`).join("");
  const peak = buckets.reduce((a, b) => { const t = Object.values(b.values).reduce((x, y) => x + y, 0); return t > a.t ? { t, b } : a; }, { t: 0, b: null });
  Charts.stackedBars($("#weekly"), buckets, series, {
    aria: `Столбцы ${unit}: всего ${sum}, больше всего ${peak.t} в период ${peak.b ? peak.b.tip : ""}`,
    empty: R.length ? "За этот период записей нет" : "Здесь появится активность после первой синхронизации",
  });
}
function projectCounts() {
  const c = {};
  for (const r of R) if (r.pkey) c[r.pkey] = (c[r.pkey] || 0) + 1;
  return c;
}
function topProjects() {
  const c = projectCounts();
  const rows = Object.entries(c).sort((a, b) => b[1] - a[1]).slice(0, 8)
    .map(([key, value]) => ({ key, value, label: esc(projName(key)), title: esc(PROJECTS[key].paths.join(", ")) }));
  if (!rows.length) { $("#top-projects").innerHTML = empty("Проектов пока нет", "Они появятся вместе с первыми записями."); return; }
  Charts.hbars($("#top-projects"), rows, (r) => go(`#project/${encodeURIComponent(r.key)}`));
}
function fresh() {
  $("#fresh").innerHTML = R.slice(0, 6).map((r) => `
    <button type="button" data-id="${r.id}">
      <span class="dot" style="background:${color(r.source)}" title="${esc(srcName(r.source))}"></span>
      <span><span class="t">${esc(r.title)}</span><span class="m">${esc(srcName(r.source))}, ${esc(projName(r.pkey))}</span></span>
      <span class="d">${esc(dayMonth(date(r.updated_at)))}</span>
    </button>`).join("") || empty("Записей пока нет", "Запусти <code>brain sync</code>.");
}

// ---- 02 Граф
let graph = null;
const gf = { sources: new Set(SRC.map((s) => s.source)), period: "all", topic: true, q: "" };
function renderGraph(arg) {
  if (!graph) initGraph();
  if (!graph) return;
  requestAnimationFrame(() => {
    graph.resize();
    const id = +arg;
    if (arg && byId.has(id)) graph.focus(id); else graph.unpin();
  });
}
function initGraph() {
  const box = $(".graph-box");
  if (!R.length) {
    box.insertAdjacentHTML("beforeend", `<div class="graph-empty">${empty("Граф пока пустой", "Запусти <code>brain sync</code>: записи соберутся вокруг своих проектов.")}</div>`);
    $("#graph-sub").textContent = "";
    return;
  }
  const counts = projectCounts();
  const logMin = Math.log(500), logMax = Math.log(80000);
  const nodes = R.map((r) => ({
    id: r.id, kind: "record", source: r.source, colorVar: colorVar(r.source), label: r.title, pkey: r.pkey, updated: r.updated_at,
    r: 3 + 6 * Math.max(0, Math.min(1, (Math.log(Math.max(1, r.body_len)) - logMin) / (logMax - logMin))),
  }));
  for (const [key, count] of Object.entries(counts)) {
    nodes.push({ id: "p:" + key, kind: "project", label: projName(key), pkey: key, count, r: Math.min(16, 6 + Math.sqrt(count) * 1.4) });
  }
  const links = R.filter((r) => r.pkey).map((r) => ({ s: r.id, t: "p:" + r.pkey, w: 1, kind: "project" }));
  for (const [a, b, w] of DATA.graph.edges) if (byId.has(a) && byId.has(b)) links.push({ s: a, t: b, w, kind: "topic" });
  const topicCount = links.length - R.filter((r) => r.pkey).length;
  const summary = `${words(R.length, RECORD_FORMS)}, ${words(Object.keys(counts).length, ["проект", "проекта", "проектов"])}, ${words(topicCount, ["связь", "связи", "связей"])} по темам`;
  $("#graph-sub").textContent = `${summary}. Записи держатся рядом со своим проектом, линии между ними — общие темы из карточек.`;
  const canvas = $("#graph");
  canvas.setAttribute("aria-label", `Граф: ${summary}. Те же записи списком на вкладках «Проекты» и «Записи».`);

  const c = $("#graph-controls");
  c.innerHTML = `
    ${SRC.filter((s) => s.count).map((s) => `<button class="chip" type="button" data-src="${esc(s.source)}" aria-pressed="true">
      <span class="dot" style="background:${color(s.source)}"></span>${esc(s.title)} <span class="num">${n(s.count)}</span></button>`).join("")}
    <div class="seg" id="g-period" role="group" aria-label="Период"></div>
    <label class="toggle ${topicCount ? "" : "off"}"><input type="checkbox" id="g-topic" ${topicCount ? "checked" : "disabled"}>
      ${topicCount ? "Связи по темам" : "Связи по темам появятся после пересказа"}</label>
    <input type="search" id="g-q" placeholder="Найти на графе" aria-label="Найти на графе">
    <span class="graph-found" id="g-found" aria-live="polite"></span>`;
  gf.topic = !!topicCount;

  graph = createGraph(canvas, {
    nodes, links,
    onOpen: (id) => openRecord(id),
    onProject: (key) => go(`#project/${encodeURIComponent(key)}`),
    onHover(node, x, y) {
      if (!node) return Charts.tip.hide();
      if (node.kind === "project") {
        Charts.tip.show(`<b>${esc(node.label)}</b><small>${words(node.count, RECORD_FORMS)}, клик открывает проект</small>`, x, y);
      } else {
        const r = byId.get(node.id);
        Charts.tip.show(`<div class="row"><span class="swatch" style="background:${color(r.source)}"></span><b>${esc(r.title)}</b></div><small>${esc(srcName(r.source))}, ${esc(projName(r.pkey))}, ${esc(dayMonth(date(r.updated_at)))}</small>`, x, y);
      }
    },
  });
  const apply = () => {
    const maxDays = gf.period === "all" ? Infinity : +gf.period;
    const ok = new Set(R.filter((r) => gf.sources.has(r.source) && daysAgo(r.updated_at) <= maxDays).map((r) => r.id));
    const liveProjects = new Set(R.filter((r) => ok.has(r.id)).map((r) => r.pkey));
    graph.setFilter((node) => (node.kind === "record" ? ok.has(node.id) : liveProjects.has(node.pkey)));
    const q = terms(gf.q);
    if (q.length) {
      const hits = new Set(R.filter((r) => ok.has(r.id) && matchAll(r, q)).map((r) => r.id));
      graph.highlight(hits.size ? hits : new Set([-1]));
      $("#g-found").textContent = hits.size ? `найдено ${words(hits.size, RECORD_FORMS)}` : "ничего не нашлось";
    } else { graph.highlight(null); $("#g-found").textContent = ""; }
  };
  c.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    const s = chip.dataset.src, on = chip.getAttribute("aria-pressed") !== "true";
    chip.setAttribute("aria-pressed", on);
    if (on) gf.sources.add(s); else gf.sources.delete(s);
    apply();
  });
  seg($("#g-period"), [["all", "Всё время"], ["90", "90 дней"], ["30", "30 дней"]], gf.period, (v) => { gf.period = v; apply(); });
  $("#g-topic").addEventListener("change", (e) => { gf.topic = e.target.checked; graph.setTopicLinks(gf.topic); });
  $("#g-q").addEventListener("input", (e) => { gf.q = e.target.value; apply(); });
  document.querySelector(".graph-zoom").addEventListener("click", (e) => {
    const z = e.target.closest("button")?.dataset.zoom;
    if (z === "in") graph.zoom(1.4); else if (z === "out") graph.zoom(1 / 1.4); else if (z === "fit") graph.fit();
  });
  apply();
}

// ---- 03 Проекты
let gridReady = false, psort = "recent", pq = "";
function projectList() {
  const by = {};
  for (const r of R) {
    if (!r.pkey) continue;
    const p = by[r.pkey] || (by[r.pkey] = { key: r.pkey, records: [], last: r.updated_at });
    p.records.push(r);
    if (r.updated_at > p.last) p.last = r.updated_at;
  }
  return Object.values(by);
}
function renderProjects(arg) {
  const grid = $("#projects-grid-view"), view = $("#project-view");
  if (arg) { grid.hidden = true; view.hidden = false; showProject(arg); return; }
  grid.hidden = false; view.hidden = true;
  if (gridReady) return;
  gridReady = true;
  const list = projectList();
  $("#projects-sub").textContent = list.length
    ? `${words(list.length, ["проект", "проекта", "проектов"])}. Папка и репозиторий с одним именем считаются одним проектом.` : "";
  seg($("#projects-sort"), [["recent", "Недавние"], ["size", "Больше записей"]], psort, (v) => { psort = v; drawGrid(list); });
  $("#q-projects").addEventListener("input", (e) => { pq = e.target.value; drawGrid(list); });
  drawGrid(list);
}
function weeklyCounts(records, weeks = 12) {
  const now = weekStart(new Date()), out = new Array(weeks).fill(0);
  for (const r of records) {
    const i = weeks - 1 - Math.round((now - weekStart(date(r.updated_at))) / (7 * DAY));
    if (i >= 0 && i < weeks) out[i]++;
  }
  return out;
}
function shareOf(records) {
  return Charts.shareBar(SRC.map((s) => ({ value: records.filter((r) => r.source === s.source).length, color: color(s.source), title: esc(s.title) })));
}
function drawGrid(list) {
  const q = terms(pq);
  const shown = list
    .filter((p) => !q.length || q.every((w) => (projName(p.key) + " " + PROJECTS[p.key].paths.join(" ")).toLowerCase().includes(w)))
    .sort(psort === "size" ? (a, b) => b.records.length - a.records.length || (b.last > a.last ? 1 : -1) : (a, b) => (b.last > a.last ? 1 : -1));
  $("#pgrid").innerHTML = shown.map((p) => {
    const meta = PROJECTS[p.key];
    const paths = meta.paths.slice(0, 2).map(esc).join("<br>") + (meta.paths.length > 2 ? `<br>и ещё ${meta.paths.length - 2}` : "");
    return `<a class="card pcard" href="#project/${encodeURIComponent(p.key)}" style="text-decoration:none">
      <h3>${esc(meta.name)}</h3>
      <p class="paths">${paths}</p>
      <div class="pstats">
        <span class="n">${n(p.records.length)}<small>${plural(p.records.length, ...RECORD_FORMS)}</small></span>
        <span>${Charts.sparkline(weeklyCounts(p.records), "Записи за 12 недель")}<span class="last">последняя ${esc(dayMonth(date(p.last)))}</span></span>
      </div>
      ${shareOf(p.records)}
      ${meta.topics.length ? `<div class="topics">${meta.topics.map((t) => `<span class="tag topic">${esc(t)}</span>`).join("")}</div>` : ""}
    </a>`;
  }).join("") || empty(list.length ? "Ничего не нашлось" : "Проектов пока нет", list.length ? "Попробуй другое слово." : "Они появятся вместе с первыми записями.");
}
function showProject(key) {
  const view = $("#project-view"), meta = PROJECTS[key];
  const records = R.filter((r) => r.pkey === key);
  if (!meta || !records.length) {
    view.innerHTML = `<p class="crumb"><a href="#projects">Проекты</a></p><h1 class="headline" tabindex="-1">Проект не найден</h1>
      <p class="lede">Такого проекта в этом снимке базы нет. Вернись к <a href="#projects">списку проектов</a>.</p>`;
    return;
  }
  const first = records[records.length - 1], last = records[0];
  const bySrc = SRC.filter((s) => records.some((r) => r.source === s.source))
    .map((s) => `<span><span class="swatch" style="background:${color(s.source)}"></span>${esc(s.title)} <span class="num">${n(records.filter((r) => r.source === s.source).length)}</span></span>`).join("");
  const atoms = [];
  for (const r of records) for (const a of r.atoms) { const p = parseAtom(a); if (p.type === "decision" || p.type === "todo") atoms.push({ ...p, r }); }
  const months = new Map();
  for (const r of records) { const d = date(r.updated_at), k = `${d.getFullYear()}-${d.getMonth()}`; (months.get(k) || months.set(k, { d, list: [] }).get(k)).list.push(r); }
  view.innerHTML = `
    <header class="tab-head">
      <p class="crumb"><a href="#projects">Проекты</a> / ${esc(meta.name)}</p>
      <h1 class="headline" tabindex="-1">${esc(meta.name)}</h1>
      <p class="paths" style="margin-top:var(--space-3)">${meta.paths.map(esc).join("<br>")}</p>
    </header>
    <div class="pmeta">
      <div class="card"><p class="label" style="margin:0">Записей</p><p class="value">${n(records.length)}</p>
        <p class="note">${words(weeklyCounts(records, 4).reduce((a, b) => a + b, 0), RECORD_FORMS)} за 4 недели</p></div>
      <div class="card"><p class="label" style="margin:0">По источникам</p><div style="margin-top:var(--space-4)">${shareOf(records)}</div>
        <div class="legend">${bySrc}</div></div>
      <div class="card"><p class="label" style="margin:0">Когда</p><p class="value" style="font-size:24px">${esc(dayMonth(date(first.updated_at)))} – ${esc(dayMonth(date(last.updated_at)))}</p>
        <p class="note">первая и последняя запись</p></div>
    </div>
    ${meta.topics.length ? `<div class="topics">${meta.topics.map((t) => `<span class="tag topic">${esc(t)}</span>`).join("")}</div>` : ""}
    <div class="card">
      <h2 class="card-title">Решения и задачи</h2>
      <p class="card-sub">Последние из карточек этого проекта</p>
      ${atoms.length ? `<ul class="atoms">${atoms.slice(0, 10).map((a) => `<li><span class="tag ${a.type === "todo" ? "wait" : ""}">${a.type === "todo" ? "задача" : "решение"}</span>${esc(a.text)}
        <button class="from" type="button" data-id="${a.r.id}">${esc(dayMonth(date(a.r.updated_at)))}, ${esc(a.r.title)}</button></li>`).join("")}</ul>`
        : `<p class="muted" style="margin:var(--space-3) 0 0">В карточках этого проекта решений и задач пока нет.</p>`}
    </div>
    <h2 class="card-title section-title">Записи</h2>
    <div class="feed">${[...months.values()].map((m) => `<div class="day"><h3>${esc(monthName(m.d))}</h3><div class="rows">${m.list.map(recordRow).join("")}</div></div>`).join("")}</div>`;
  view.style.display = "grid";
  view.style.gap = "var(--space-5)";
}

// ---- 04 Записи и здоровье базы
let recordsReady = false;
const rf = { q: "", source: "all", status: "all", limit: 100 };
function renderRecords() {
  if (recordsReady) return;
  recordsReady = true;
  health();
  seg($("#f-source"), [["all", "Все"], ...SRC.filter((s) => s.count).map((s) => [s.source, s.title])], rf.source, (v) => { rf.source = v; rf.limit = 100; drawFeed(); });
  seg($("#f-status"), [["all", "Любые"], ["card", "С карточкой"], ["wait", "Без карточки"], ["err", "Ошибка"]], rf.status, (v) => { rf.status = v; rf.limit = 100; drawFeed(); });
  $("#q-records").addEventListener("input", (e) => { rf.q = e.target.value; rf.limit = 100; drawFeed(); });
  $("#feed").addEventListener("click", (e) => { if (e.target.closest(".more")) { rf.limit += 100; drawFeed(); } });
  drawFeed();
}
function health() {
  const head = `<div class="hrow head"><span>Источник</span><span class="st-h">Синхронизация</span>
    <span class="n">Записей</span><span class="n">Без карточки</span><span class="n">Ещё идут</span><span class="n">Пропущено</span></div>`;
  const rows = SRC.map((s) => {
    const rs = R.filter((r) => r.source === s.source);
    const sync = DATA.sync[s.source];
    const seen = DATA.files_seen[s.source];
    const skipped = seen === undefined ? null : Math.max(0, seen - rs.length);
    const status = !sync ? `<span class="tag">не запускалась</span>`
      : sync.last_error ? `<span class="tag err">ошибка</span> <span class="msg">${esc(sync.last_error)}</span>`
      : `<span class="tag ok">ок</span> <span>${esc(ago(date(sync.last_success_at)))}</span>`;
    const name = `<span class="src"><span class="dot" style="background:${color(s.source)}"></span>${esc(s.title)}</span>`;
    if (s.hint && !rs.length) return `<div class="hrow">${name}<span class="st">${status}</span><span class="hint">${esc(s.hint)}</span></div>`;
    return `<div class="hrow">${name}<span class="st">${status}</span>
      <span class="n">${n(rs.length)}</span><span class="n">${n(rs.filter((r) => !r.card).length)}</span>
      <span class="n">${n(DATA.waiting[s.source] || 0)}</span><span class="n">${skipped === null ? "—" : n(skipped)}</span></div>`;
  }).join("");
  $("#health").innerHTML = head + rows;
  const runs = Math.ceil(pending / DATA.batch_limit);
  const queue = pending
    ? `Без карточки ${words(pending, RECORD_FORMS)}: их разберут ${runs === 1 ? "на следующей синхронизации" : `ближайшие ${words(runs, ["синхронизация", "синхронизации", "синхронизаций"])}`} (до ${DATA.batch_limit} за раз).`
    : "Очередь пересказа пуста.";
  $("#health-note").innerHTML = `${queue} «Ещё идут» — разговоры, где меньше 30 минут назад что-то происходило: их заберёт одна из следующих синхронизаций. «Пропущено» — файлы без твоих сообщений и из проектов в <code>exclude_projects</code>.`;
}
function feedMatches(r) {
  if (rf.source !== "all" && r.source !== rf.source) return false;
  if (rf.status === "card" && !r.card) return false;
  if (rf.status === "wait" && (r.card || r.error)) return false;
  if (rf.status === "err" && (r.card || !r.error)) return false;
  const q = terms(rf.q);
  return !q.length || matchAll(r, q);
}
function dayLabel(d) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const x = new Date(d); x.setHours(0, 0, 0, 0);
  const diff = Math.round((today - x) / DAY);
  if (diff === 0) return "Сегодня";
  if (diff === 1) return "Вчера";
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long", ...(d.getFullYear() !== thisYear && { year: "numeric" }) }).replace(" г.", "");
}
function drawFeed() {
  const list = R.filter(feedMatches);
  $("#count").textContent = list.length === R.length ? `Все ${words(R.length, RECORD_FORMS)}, свежие сверху` : `Найдено ${n(list.length)} из ${n(R.length)}`;
  if (!list.length) {
    $("#feed").innerHTML = empty(R.length ? "Ничего не нашлось" : "Записей пока нет", R.length ? "Попробуй другое слово или сбрось фильтры." : "Запусти <code>brain sync</code>.");
    return;
  }
  const groups = [];
  for (const r of list.slice(0, rf.limit)) {
    const label = dayLabel(date(r.updated_at));
    if (!groups.length || groups[groups.length - 1].label !== label) groups.push({ label, list: [] });
    groups[groups.length - 1].list.push(r);
  }
  $("#feed").innerHTML = groups.map((g) => `<div class="day"><h3>${esc(g.label)}</h3><div class="rows">${g.list.map(recordRow).join("")}</div></div>`).join("")
    + (list.length > rf.limit ? `<button class="more" type="button">Показать ещё ${n(Math.min(100, list.length - rf.limit))}</button>` : "");
}

// ---- Карточка записи
let lastFocus = null;
function openRecord(id) {
  const r = byId.get(id);
  if (!r) return;
  closeRecord(true);
  lastFocus = document.activeElement;
  Charts.tip.hide();
  const notice = r.card ? "" : r.error
    ? `<p class="notice err">Модель не смогла пересказать эту запись: ${esc(r.error)}. Попробует ещё раз в одном из следующих прогонов.</p>`
    : `<p class="notice wait">Карточки пока нет: запись в очереди на пересказ.</p>`;
  const atoms = r.atoms.map((a) => { const p = parseAtom(a); return `<li>${p.type ? `<span class="tag">${esc(ATOM[p.type] ? ATOM[p.type][0] : p.type)}</span>` : ""}${esc(p.text)}</li>`; }).join("");
  const sim = neighbors(r.id).slice(0, 5).map(([o, w]) => {
    const x = byId.get(o);
    return `<li><button type="button" data-open="${o}"><span class="dot" style="background:${color(x.source)}"></span><span class="t">${esc(x.title)}</span><span class="w">${esc(projName(x.pkey))}</span></button></li>`;
  }).join("");
  const scrimEl = document.createElement("div");
  scrimEl.className = "scrim";
  scrimEl.addEventListener("click", () => closeRecord());
  const d = document.createElement("aside");
  d.className = "drawer";
  d.setAttribute("role", "dialog");
  d.setAttribute("aria-modal", "true");
  d.setAttribute("aria-label", r.title);
  d.innerHTML = `
    <button class="close" type="button">Закрыть</button>
    <p class="label">${esc(srcName(r.source))}, id ${r.id}</p>
    <h2>${esc(r.title)}</h2>
    <dl class="meta">
      <dt>Начат</dt><dd>${esc(stamp(date(r.started_at)))}</dd>
      <dt>Последнее сообщение</dt><dd>${esc(stamp(date(r.updated_at)))}</dd>
      <dt>Проект</dt><dd>${r.pkey ? `<a href="#project/${encodeURIComponent(r.pkey)}">${esc(projName(r.pkey))}</a>` : "—"} <span class="mono muted">${esc(r.project || "")}</span></dd>
      <dt>Файл</dt><dd class="mono">${esc(r.location || "—")}</dd>
    </dl>
    <div class="drawer-actions">
      <button class="btn" type="button" data-graph="${r.id}">Показать на графе</button>
    </div>
    ${notice}
    ${r.summary ? `<p class="label">Кратко</p><p>${esc(r.summary)}</p>` : ""}
    ${sim ? `<p class="label">Похожие записи</p><ul class="similar">${sim}</ul>` : ""}
    ${atoms ? `<p class="label">Атомы</p><ul>${atoms}</ul>` : ""}
    ${r.questions.length ? `<p class="label">Как это будут искать</p><ul>${r.questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ul>` : ""}
    ${r.keywords ? `<p class="label">Ключевые слова</p><p class="muted" style="font-size:14px">${esc(r.keywords)}</p>` : ""}
    <p class="label">Расшифровка</p>
    <p class="muted" style="font-size:14px;margin-bottom:var(--space-3)">${r.body_len > DATA.preview_chars
      ? `Первые ${n(DATA.preview_chars)} из ${n(r.body_len)} символов. Целиком: <code>brain show ${r.id}</code>.`
      : `${n(r.body_len)} символов.`}</p>
    <pre class="transcript">${esc(r.preview)}</pre>`;
  d.addEventListener("click", (e) => {
    if (e.target.closest(".close")) return closeRecord();
    const open = e.target.closest("[data-open]");
    if (open) return openRecord(+open.dataset.open);
    const g = e.target.closest("[data-graph]");
    if (g) { closeRecord(true); go(`#graph/${g.dataset.graph}`); return; }
    if (e.target.closest("a[href^='#']")) closeRecord(true);
  });
  document.body.append(scrimEl, d);
  d.querySelector(".close").focus();
}
function closeRecord(silent) {
  document.querySelectorAll(".scrim, .drawer").forEach((el) => el.remove());
  if (!silent && lastFocus && document.contains(lastFocus)) lastFocus.focus();
  if (!silent) lastFocus = null;
}
addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if ($(".drawer")) closeRecord();
  else if (side.classList.contains("open")) closeMenu();
});
// Фокус не уходит из карточки клавишей Tab.
addEventListener("keydown", (e) => {
  const d = $(".drawer");
  if (e.key !== "Tab" || !d) return;
  const f = [...d.querySelectorAll("button, a[href], [tabindex]:not([tabindex='-1'])")];
  if (!f.length) return;
  if (e.shiftKey && document.activeElement === f[0]) { f[f.length - 1].focus(); e.preventDefault(); }
  else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { f[0].focus(); e.preventDefault(); }
});

let resizeTimer;
addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (current?.tab === "overview") drawWeekly();
    if (current?.tab === "graph" && graph) graph.resize();
  }, 100);
});

route();
