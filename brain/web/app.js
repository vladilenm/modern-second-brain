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
const dayMonth = (d) => d.toLocaleDateString("ru-RU", { day: "numeric", month: "short", ...(d.getFullYear() !== thisYear && { year: "numeric" }) }).replace(/\./g, "").replace(" г", "");
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
const SLOT_ORDER = ["claude_code", "codex", "github", "chatgpt", "gmail", "yandex_mail", "kimi"];
const SRC = DATA.sources_meta
  .map((m, i) => ({ ...m, order: SLOT_ORDER.includes(m.source) ? SLOT_ORDER.indexOf(m.source) : SLOT_ORDER.length + i }))
  .sort((a, b) => a.order - b.order)
  .map((m, i) => ({ ...m, slot: i + 1, count: R.filter((r) => r.source === m.source).length }));
const SRC_BY = Object.fromEntries(SRC.map((s) => [s.source, s]));
const colorVar = (source) => `--series-${Math.min(7, SRC_BY[source]?.slot || 7)}`;
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
const PCOUNT = projectCounts();

// ---- Поиск по базе: карточка (название, проект, ключевые слова, пересказ, вопросы, знания) и начало текста.
const nrm = (s) => String(s || "").toLowerCase().replace(/ё/g, "е");
// Окончания от длинных к коротким: «субтитры», «субтитров», «субтитрами» сводятся к «субтитр».
const ENDINGS = ["иями", "ями", "ами", "ией", "иях", "ого", "его", "ому", "ему", "ыми", "ими", "ить", "ать", "ять", "еть",
  "ов", "ев", "ей", "ий", "ый", "ой", "ая", "яя", "ое", "ее", "ые", "ие", "ом", "ем", "ам", "ям", "ах", "ях", "ую", "юю", "ию", "ия", "ью",
  "а", "я", "ы", "и", "у", "ю", "е", "о", "ь", "й"];
function stem(w) {
  if (w.length < 5 || !/^[а-я]+$/.test(w)) return w;
  for (const e of ENDINGS) if (w.endsWith(e) && w.length - e.length >= 4) return w.slice(0, -e.length);
  return w;
}
const terms = (q) => nrm(q).split(/[\s,;]+/).map((w) => w.replace(/^[«"'(]+|[»"').!?:]+$/g, "")).filter(Boolean).map(stem);
let IX = null;
function index() {
  if (IX) return IX;
  IX = new Map();
  for (const r of R) {
    const atoms = r.atoms.map((a) => { const p = parseAtom(a); return { type: p.type, text: p.text, n: nrm(p.text) }; });
    const qs = r.questions.map((t) => ({ text: t, n: nrm(t) }));
    const head = nrm([r.title, projName(r.pkey), r.project, r.keywords].join("\n"));
    const sum = nrm(r.summary);
    IX.set(r.id, { title: nrm(r.title), head, sum, atoms, qs, pre: nrm(r.preview),
      card: [head, sum, ...qs.map((x) => x.n), ...atoms.map((x) => x.n)].join("\n") });
  }
  return IX;
}
// По карточке: фильтр графа. С текстом записи: общий поиск и лента.
const matchAll = (r, q) => { const x = index().get(r.id); return q.every((w) => x.card.includes(w)); };
const matchDeep = (r, q) => { const x = index().get(r.id); return q.every((w) => x.card.includes(w) || x.pre.includes(w)); };
// Совпадение с оценкой и фрагментом, где нашлось. f: { source, days, type } — фильтры поиска.
function hit(r, q, f = {}) {
  if (f.source && r.source !== f.source) return null;
  if (f.days && daysAgo(r.updated_at) > f.days) return null;
  const x = index().get(r.id);
  const count = (s) => q.reduce((k, w) => k + s.includes(w), 0);
  if (f.type) {
    // Тип знания: слова ищем в самих знаниях этого типа, название и проект задают контекст.
    const own = x.atoms.filter((a) => a.type === f.type);
    if (!own.length || !q.every((w) => x.head.includes(w) || own.some((a) => a.n.includes(w)))) return null;
    let best = own[0], top = -1;
    for (const a of own) { const c = count(a.n); if (c > top) { top = c; best = a; } }
    return { r, score: top * 10 + count(x.head), sn: { tag: best.type, text: best.text } };
  }
  if (!q.every((w) => x.card.includes(w) || x.pre.includes(w))) return null;
  let score = 0;
  for (const w of q) score += x.title.includes(w) ? 10 : x.head.includes(w) ? 6 : x.card.includes(w) ? 4 : 1;
  let sn = null, top = 0;
  const offer = (c, tag, text) => { if (c > top) { top = c; sn = { tag, text }; } };
  for (const a of x.atoms) offer(count(a.n), a.type, a.text);
  for (const t of x.qs) offer(count(t.n), "q", t.text);
  offer(count(x.sum), "", r.summary);
  offer(count(x.pre), "text", r.preview);
  if (q.length > 1 && top === q.length) score += 3;        // все слова рядом, в одном фрагменте
  if (!sn && r.summary) sn = { tag: "", text: r.summary };
  return { r, score, sn };
}
function excerpt(text, q, max = 180) {
  const t = String(text || ""), low = nrm(t);
  let at = -1;
  for (const w of q) { const i = low.indexOf(w); if (i >= 0 && (at < 0 || i < at)) at = i; }
  let from = 0;
  if (at > 70) { from = at - 60; const sp = t.indexOf(" ", from); if (sp >= 0 && sp < at) from = sp + 1; }
  return (from ? "…" : "") + t.slice(from, from + max).replace(/\s+/g, " ").trim() + (from + max < t.length ? "…" : "");
}
// Подсветка: от найденной основы до конца слова.
function hl(text, q) {
  const t = String(text || ""), low = nrm(t), spans = [];
  for (const w of q) for (let i = low.indexOf(w); i >= 0; i = low.indexOf(w, i + w.length)) {
    let e = i + w.length;
    while (e < low.length && /[a-zа-я0-9]/.test(low[e])) e++;
    spans.push([i, e]);
  }
  spans.sort((a, b) => a[0] - b[0]);
  let out = "", pos = 0;
  for (const [a, b] of spans) { if (a < pos) continue; out += `${esc(t.slice(pos, a))}<mark>${esc(t.slice(a, b))}</mark>`; pos = b; }
  return out + esc(t.slice(pos));
}
const SNIP_TAG = { q: "вопрос", text: "в тексте" };
function snippet(sn, q) {
  if (!sn) return "";
  const label = SNIP_TAG[sn.tag] || ATOM[sn.tag]?.[0] || (sn.tag ? "знание" : "");
  return `${label ? `<span class="tag ${sn.tag === "todo" ? "wait" : ""}">${esc(label)}</span>` : ""}${hl(excerpt(sn.text, q), q)}`;
}

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
  topics: { title: "Темы", render: renderTopics },
  knowledge: { title: "Знания", render: renderKnowledge },
  records: { title: "Записи", render: renderRecords },
};
let current = null;
function parseHash() {
  const [tab, ...rest] = location.hash.slice(1).split("/");
  const arg = decodeURIComponent(rest.join("/"));
  if (tab === "project") return { tab: "projects", arg };
  if (tab === "topic") return { tab: "topics", arg };
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
  if (r.tab !== "graph" && graphFull()) setGraphFull(false);
  const prev = current;
  current = r;
  TABS[r.tab].render(r.arg);
  if (graph) graph.setActive(r.tab === "graph");
  if (prev && changed && !(prev.tab === "graph" && r.tab === "graph")) {
    scrollTo(0, 0);
    const h = (r.tab === "topics" && r.arg && $("#topic-view h1")) || $(`section[data-tab="${r.tab}"] h1:not([hidden])`) || $("#project-view h1");
    if ($(".drawer")) return;
    if (r.tab === "overview" && matchMedia("(hover: hover)").matches) $("#q-global").focus({ preventScroll: true });
    else if (h) h.focus({ preventScroll: true });
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

// ---- Поиск: одно поле на всю панель. На обзоре оно в шапке, на остальных вкладках открывается поверх страницы.
let openPalette = () => {};
(function search() {
  const input = $("#q-global"), list = $("#q-results"), panel = $("#q-panel"), bar = $("#q-filters");
  const finder = $("#finder"), home = $("#finder-home"), recentBox = $("#recent");
  const PAGE = 8, RECENT_KEY = "brain.search.recent";
  const sf = { type: "", days: 0, source: "" };
  let found = [], sel = -1, wrap = null, limit = PAGE, recent = [];
  try { recent = (JSON.parse(localStorage.getItem(RECENT_KEY)) || []).filter((x) => typeof x === "string"); } catch { /* без хранилища просто не помним */ }
  const filtered = () => Boolean(sf.type || sf.days || sf.source);
  function drawRecent() {
    recentBox.hidden = !recent.length;
    recentBox.innerHTML = recent.length
      ? `<span class="quick-label">Недавнее</span>${recent.map((q) => `<button class="chip" type="button" data-q="${esc(q)}">${esc(q)}</button>`).join("")}<button class="link" type="button" data-clear>очистить</button>` : "";
  }
  function remember(text) {
    const q = text.trim();
    if (q.length < 2) return;
    recent = [q, ...recent.filter((x) => nrm(x) !== nrm(q))].slice(0, 6);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(recent)); } catch { /* не страшно */ }
    drawRecent();
  }
  function drawFilters() {
    const types = Object.keys(ATOM_TITLE);
    const group = (key, options) => `<div class="sf-g" role="group">${options.map(([v, label, dot]) =>
      `<button type="button" data-k="${key}" data-v="${esc(v)}" aria-pressed="${String(sf[key] || "") === String(v)}">${dot ? `<span class="dot" style="background:${dot}"></span>` : ""}${esc(label)}</button>`).join("")}</div>`;
    bar.innerHTML = group("type", [["", "Всё"], ...types.map((t) => [t, ATOM_TITLE[t]])])
      + group("days", [["", "Любое время"], [7, "Неделя"], [30, "Месяц"], [365, "Год"]])
      + group("source", [["", "Все источники"], ...SRC.filter((s) => s.count).map((s) => [s.source, s.title, color(s.source)])]);
  }
  function show(open) {
    panel.hidden = !open;
    input.setAttribute("aria-expanded", String(open));
    if (!open) { found = []; sel = -1; }
  }
  function render() {
    const q = terms(input.value);
    if (!q.length && !filtered()) {
      // Пустое поле поверх страницы: недавние запросы. На обзоре они и так под полем.
      if (!wrap || !recent.length) return show(false);
      found = recent.map((text) => ({ kind: "recent", text }));
    } else {
      const projects = filtered() || !q.length ? [] : Object.keys(PCOUNT).filter((k) => q.every((w) => nrm(projName(k)).includes(w)))
        .sort((a, b) => PCOUNT[b] - PCOUNT[a]).slice(0, 3);
      const all = [];
      for (const r of R) { const h = hit(r, q, sf); if (h) all.push(h); }
      all.sort((a, b) => b.score - a.score);          // при равной оценке свежие выше: R уже по дате
      found = [...projects.map((key) => ({ kind: "project", key })), ...all.slice(0, limit).map((h) => ({ kind: "record", ...h }))];
      // Тип знания и период есть только здесь, поэтому с ними список растёт на месте.
      if (all.length > limit) found.push(sf.type || sf.days ? { kind: "more", count: all.length - limit } : { kind: "all", count: all.length });
    }
    sel = found.length ? Math.min(Math.max(sel, 0), found.length - 1) : -1;
    const q0 = q;
    list.innerHTML = found.map((x, i) => {
      const body = x.kind === "project"
        ? `<span class="tag">проект</span><span><span class="t">${hl(projName(x.key), q0)}</span><small>${words(PCOUNT[x.key], RECORD_FORMS)}</small></span>`
        : x.kind === "record"
          ? `<span class="dot" style="background:${color(x.r.source)}"></span><span><span class="t">${hl(x.r.title, q0)}</span>${x.sn ? `<span class="sn">${snippet(x.sn, q0)}</span>` : ""}<small>${esc(srcName(x.r.source))}, ${esc(projName(x.r.pkey))}, ${esc(dayMonth(date(x.r.updated_at)))}</small></span>`
          : x.kind === "recent"
            ? `<span class="tag">недавнее</span><span class="t">${esc(x.text)}</span>`
            : x.kind === "more"
              ? `<span class="t all">Показать ещё ${n(Math.min(20, x.count))} из ${n(x.count)}</span>`
              : `<span class="t all">Все ${words(x.count, ["результат", "результата", "результатов"])} в «Записях»</span>`;
      return `<li role="option" id="qr-${i}" data-i="${i}" class="k-${x.kind}" aria-selected="${i === sel}">${body}</li>`;
    }).join("") || `<li class="none">Ничего не нашлось${filtered() ? `. <button class="link" type="button" data-reset>Сбросить фильтры</button>` : ""}</li>`;
    drawFilters();
    show(true);
  }
  function mark() {
    list.querySelectorAll("li[data-i]").forEach((li, i) => li.setAttribute("aria-selected", i === sel));
    input.setAttribute("aria-activedescendant", sel >= 0 ? `qr-${sel}` : "");
    list.querySelector('li[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }
  function closePalette() {
    if (!wrap) return;
    home.append(finder);
    wrap.remove();
    wrap = null;
  }
  openPalette = () => {
    closeMenu();
    if (current?.tab === "overview" && !wrap) { scrollTo(0, 0); input.focus(); input.select(); return; }
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.className = "palette";
      wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) closePalette(); });
      wrap.append(finder);
      // Во весь экран браузер показывает только развёрнутый элемент.
      (fsElement() || document.body).append(wrap);
    }
    input.focus();
    input.select();
  };
  function pick(i) {
    const x = found[i];
    if (!x) return;
    if (x.kind === "recent") { input.value = x.text; sel = 0; return render(); }
    if (x.kind === "more") { limit += 20; sel = i; render(); return mark(); }
    const ids = found.filter((f) => f.kind === "record").map((f) => f.r.id);
    remember(input.value);
    show(false);
    input.blur();
    closePalette();
    if (x.kind === "record") openRecord(x.r.id, ids);
    else if (x.kind === "project") go(`#project/${encodeURIComponent(x.key)}`);
    else showAllResults(input.value, sf.source);
  }
  input.addEventListener("input", () => { limit = PAGE; sel = 0; render(); });
  input.addEventListener("focus", render);
  // Фильтры не забирают фокус у поля: печатать можно дальше.
  panel.addEventListener("mousedown", (e) => { if (e.target.closest("button")) e.preventDefault(); });
  panel.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.k) sf[b.dataset.k] = b.dataset.k === "days" ? +b.dataset.v : b.dataset.v;
    else if ("reset" in b.dataset) Object.assign(sf, { type: "", days: 0, source: "" });
    else return;
    limit = PAGE; sel = 0;
    render();
  });
  const useQuery = (e) => {
    if (e.target.closest("[data-clear]")) {
      recent = [];
      try { localStorage.removeItem(RECENT_KEY); } catch { /* не страшно */ }
      return drawRecent();
    }
    const b = e.target.closest("[data-q]");
    if (!b) return;
    input.value = b.dataset.q;
    limit = PAGE; sel = 0;
    input.focus();
    render();
  };
  drawRecent();
  input.addEventListener("keydown", (e) => {
    if (panel.hidden && e.key !== "Escape") return;
    if (e.key === "ArrowDown" && found.length) { sel = (sel + 1) % found.length; mark(); e.preventDefault(); }
    else if (e.key === "ArrowUp" && found.length) { sel = (sel - 1 + found.length) % found.length; mark(); e.preventDefault(); }
    else if (e.key === "Enter" && sel >= 0) { e.preventDefault(); pick(sel); }   // иначе Enter долетит до «Закрыть» в открывшейся карточке
    else if (e.key === "Escape") {
      e.stopPropagation();
      if (wrap) closePalette(); else { input.value = ""; Object.assign(sf, { type: "", days: 0, source: "" }); render(); input.blur(); }
    }
  });
  list.addEventListener("mousedown", (e) => { const li = e.target.closest("li[data-i]"); if (li) { e.preventDefault(); pick(+li.dataset.i); } });
  input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input) show(false); }, 100));
  $("#q-open").addEventListener("click", () => openPalette());
  // Темы недели и недавние запросы под полем: клик подставляет их в поиск.
  $("#quick").addEventListener("click", useQuery);
  recentBox.addEventListener("click", useQuery);
  addEventListener("keydown", (e) => {
    const t = e.target;
    if (e.key !== "/" || e.metaKey || e.ctrlKey || (t.closest && t.closest("input, textarea, [contenteditable]"))) return;
    e.preventDefault();
    // На графе «/» ведёт в его собственный поиск: он подсвечивает найденное прямо на холсте.
    const gq = current?.tab === "graph" && !$(".drawer") && $("#g-q");
    if (gq) { gq.focus(); gq.select(); } else openPalette();
  });
})();
function showAllResults(q, source) {
  rf.q = q; rf.source = source || "all"; rf.status = "all"; rf.limit = 100;
  $("#q-records").value = q;
  go("#records");
  if (recordsReady) {
    for (const id of ["#f-source", "#f-status"]) $(id).querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === (id === "#f-source" ? rf.source : "all")));
    drawFeed();
  }
}

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
function recordRow(r, sub) {
  const status = r.card ? "" : r.error ? `<span class="tag err">ошибка</span>` : `<span class="tag wait">ждёт</span>`;
  return `<button class="rrow" type="button" data-id="${r.id}">
    <span class="dot" style="background:${color(r.source)}" title="${esc(srcName(r.source))}"></span>
    <span><span class="t">${esc(r.title)}</span>${sub || r.summary ? `<span class="s">${sub || esc(r.summary)}</span>` : ""}</span>
    <span class="p">${status}<span class="mono" title="${esc(r.project)}">${esc(projName(r.pkey))}</span></span>
  </button>`;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-id]:not(li)");
  if (!b || b.closest(".drawer")) return;
  // Соседи по списку, из которого открыли запись: по ним ходят стрелки в карточке.
  const scope = b.closest(".feed, .news, .atoms") || b.parentElement;
  openRecord(+b.dataset.id, [...new Set([...scope.querySelectorAll("[data-id]")].map((x) => +x.dataset.id))]);
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
    baseLine();
    weekProjects();
    initNews();
    initPins();
    seg($("#weekly-range"), [["3m", "3 месяца"], ["1y", "Год"], ["all", "Всё"]], range, (v) => { range = v; drawWeekly(); });
  }
  drawWeekly();
}
const PROJECT_FORMS = ["проект", "проекта", "проектов"];
// Счётчики базы одной строкой; отдельный блок появляется, только когда что-то сломалось.
function baseLine() {
  const last = date(st.last_sync_at);
  let atoms = 0;
  for (const r of R) atoms += r.atoms.length;
  const parts = [
    words(R.length, RECORD_FORMS),
    words(Object.keys(PROJECTS).filter(Boolean).length, PROJECT_FORMS),
    words(atoms, ["знание", "знания", "знаний"]),
    last ? `синхронизация ${ago(last)}` : "синхронизации ещё не было",
  ];
  if (pending) parts.push(`${words(pending, RECORD_FORMS)} без карточки`);
  $("#base-line").innerHTML = `${parts.map(esc).join(" · ")} · <a href="#records">Состояние базы</a>`;
  const bad = [];
  if (DATA.state === "error") bad.push("Последний прогон упал.");
  if (DATA.state === "interrupted") bad.push("Последний прогон прервался.");
  if (syncErrors.length) bad.push(`Ошибки синхронизации: ${syncErrors.map(([src]) => esc(srcName(src))).join(", ")}.`);
  if (failed) bad.push(`${words(failed, RECORD_FORMS)} не удалось пересказать.`);
  const quiet = Object.values(DATA.silence || {}).filter((s) => s.quiet);
  if (quiet.length) bad.push(`Подозрительно тихо: ${quiet.map((s) => `${esc(srcName(s.source))} (${n(s.silent_days)} дн., обычно до ${n(s.usual_days)})`).join(", ")}.`);
  const alert = $("#base-alert");
  alert.hidden = !bad.length;
  alert.innerHTML = bad.length ? `${bad.join(" ")} <a href="#records">Подробности</a>` : "";
}
const WEEK_DAYS = 7;
function weekProjects() {
  const recent = R.filter((r) => daysAgo(r.updated_at) <= WEEK_DAYS);
  const by = new Map();
  for (const r of recent) if (r.pkey) (by.get(r.pkey) || by.set(r.pkey, []).get(r.pkey)).push(r);
  $("#week-sub").textContent = recent.length
    ? `${words(recent.length, RECORD_FORMS)} за ${WEEK_DAYS} дней, ${words(by.size, PROJECT_FORMS)}`
    : `За ${WEEK_DAYS} дней записей не было`;
  const rows = [...by].sort((a, b) => b[1].length - a[1].length).slice(0, 7);
  const max = rows.length ? rows[0][1].length : 1;
  $("#week-projects").innerHTML = rows.map(([key, list]) => `
    <a class="wrow" href="#project/${encodeURIComponent(key)}">
      <span><span class="t">${esc(projName(key))}</span><span class="m">${esc(list[0].title)}</span></span>
      <span class="wbar" aria-hidden="true"><i style="width:${Math.max(6, Math.round((list.length / max) * 100))}%"></i></span>
      <span class="d">${n(list.length)}</span>
    </a>`).join("") || empty(R.length ? "Тихая неделя" : "Проектов пока нет", R.length ? `Все проекты — на вкладке <a href="#projects">«Проекты»</a>.` : "Они появятся вместе с первыми записями.");
  // Темы недели: чем чаще тема в свежих карточках, тем раньше.
  const counts = new Map();
  for (const r of recent.length ? recent : R.slice(0, 40)) for (const t of r.topics) counts.set(t, (counts.get(t) || 0) + 1);
  const top = [...counts].sort((a, b) => b[1] - a[1]).slice(0, 8);
  $("#quick").innerHTML = top.length
    ? `<span class="quick-label">Темы недели</span>${top.map(([t]) => `<button class="chip" type="button" data-q="${esc(t)}">${esc(t)}</button>`).join("")}<a class="link" href="#topics">все темы</a>` : "";
}
// ---- Что нового: знания из записей, изменившихся за период
const NEWS_MAIN = ["decision", "todo", "error", "fix"];
const NEWS_TOP = 5, NEWS_BARE = 3, NEWS_GROUPS = 5;
const NEWS_RANGES = [["seen", "С прошлого просмотра"], ["today", "Сегодня"], ["7d", "7 дней"]];
// seen — отметка «всё просмотрено» (время сборки панели); store — доступно ли хранилище; open — раскрытые списки.
const news = { range: "7d", seen: null, store: false, open: new Set() };
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
function newsMarkdown() {
  return `# Что нового ${newsPeriod()}\n\n` + newsModel().groups.map((g) => {
    const lines = [...g.main, ...g.rest].map((a) => `- **${atomLabel(a.type)}:** ${a.text}`);
    if (g.bare.length) lines.push(`- без знаний: ${g.bare.map((r) => r.title).join("; ")}`);
    return `## ${newsName(g.key)}\n${newsTally(g)}\n\n${lines.join("\n")}`;
  }).join("\n\n") + "\n";
}
function drawNews() {
  syncNewsTools();
  const m = newsModel(), projects = m.groups.filter((g) => g.key).length;
  $("#news-copy").hidden = !m.count;
  const counts = m.count ? [words(m.count, RECORD_FORMS), projects && words(projects, PROJECT_FORMS)].filter(Boolean).join(", ") : "";
  $("#news-sub").textContent = [counts, newsPeriod()].filter(Boolean).join(" · ");
  if (!m.count) {
    const last = date(st.last_sync_at);
    $("#news").innerHTML = !R.length ? empty("Записей пока нет", "Запусти <code>brain sync</code>.")
      : empty(news.range === "seen" ? "С прошлого просмотра ничего не изменилось" : "За этот период записей не было",
        last ? `Синхронизация ${esc(ago(last))}.` : "Синхронизации ещё не было.");
    return;
  }
  // Проектов бывает десятки: показываем первые, остальные — по кнопке. Markdown отдаёт все.
  const shown = news.open.has("*") ? m.groups : m.groups.slice(0, NEWS_GROUPS);
  $("#news").innerHTML = shown.map(newsGroup).join("") + (m.groups.length > shown.length
    ? `<button class="more" type="button" data-nmore="*">Показать ещё ${esc(words(m.groups.length - shown.length, PROJECT_FORMS))}</button>` : "");
}
// ---- Закреплённые записи: быстрый возврат и пакет контекста для вставки в нейронку
const PINS_KEY = "brain.pins";
let pins = [];
try { pins = (JSON.parse(localStorage.getItem(PINS_KEY)) || []).filter((id) => byId.has(id)); } catch { /* без хранилища список живёт до перезагрузки */ }
const isPinned = (id) => pins.includes(id);
const pinLabel = (id) => (isPinned(id) ? "★ Закреплено" : "☆ Закрепить");
function togglePin(id) {
  pins = isPinned(id) ? pins.filter((x) => x !== id) : [id, ...pins];
  try { localStorage.setItem(PINS_KEY, JSON.stringify(pins)); } catch { /* не запомним */ }
  document.querySelectorAll(`[data-pin="${id}"]`).forEach((b) => { b.textContent = pinLabel(id); b.setAttribute("aria-pressed", isPinned(id)); });
  drawPins();
}
function pinsMarkdown() {
  return `# Закреплённые записи (${pins.length})\n\n` + pins.map((id) => {
    const r = byId.get(id);
    const meta = [projName(r.pkey), srcName(r.source), fullDate(date(r.updated_at)), /^https?:/.test(r.location || "") && r.location].filter(Boolean).join(" · ");
    const atoms = r.atoms.map(parseAtom).map((a) => `- **${atomLabel(a.type)}:** ${a.text}`).join("\n");
    return [`## ${r.title}\n${meta}`, r.summary, atoms].filter(Boolean).join("\n\n");
  }).join("\n\n") + "\n";
}
function drawPins() {
  $("#pins-card").hidden = !pins.length;
  $("#pins-sub").textContent = pins.length ? `${words(pins.length, RECORD_FORMS)} · звёздочка в карточке записи или клавиша S` : "";
  $("#pins").innerHTML = pins.map((id) => recordRow(byId.get(id))).join("");
}
function initPins() {
  $("#pins-copy").addEventListener("click", (e) => { e.currentTarget.dataset.copy = pinsMarkdown(); copyText(e.currentTarget); });
  $("#pins-clear").addEventListener("click", () => {
    if (!confirm("Убрать все закреплённые записи?")) return;
    [...pins].forEach(togglePin);
  });
  drawPins();
}
// S в открытой карточке закрепляет или открепляет запись.
addEventListener("keydown", (e) => {
  const d = $(".drawer");
  if (!d || e.metaKey || e.ctrlKey || e.altKey || !/^[sSыЫ]$/.test(e.key)) return;
  e.preventDefault();
  togglePin(+d.dataset.id);
});
function initNews() {
  seg($("#news-range"), NEWS_RANGES, news.range, (v) => { news.range = v; news.open.clear(); saveNews(); drawNews(); });
  $("#news-seen").addEventListener("click", markSeen);
  $("#news-copy").addEventListener("click", (e) => { e.currentTarget.dataset.copy = newsMarkdown(); copyText(e.currentTarget); });
  $("#news").addEventListener("click", (e) => {
    const b = e.target.closest("[data-nmore]");
    if (!b) return;
    news.open.add(b.dataset.nmore);
    drawNews();
  });
  drawNews();
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
// ---- 02 Граф
let graph = null;
const PREFS_KEY = "brain.graph.prefs";
// open — проект, раскрытый на карте проектов; ok — записи, прошедшие фильтры периода и источников.
const gf = { sources: new Set(SRC.map((s) => s.source)), period: "all", q: "", hits: [], at: -1, open: null, ok: new Set() };
// mode: "map" — карта проектов (записи появляются у раскрытого проекта), "all" — все записи сразу.
const gv = { topic: true, minW: 0, areas: true, labels: "less", mode: "map" };
try { Object.assign(gv, JSON.parse(localStorage.getItem(PREFS_KEY)) || {}); } catch { /* настройки по умолчанию */ }
const savePrefs = () => { try { localStorage.setItem(PREFS_KEY, JSON.stringify(gv)); } catch { /* не запомним */ } };
// Кольцо есть только у проекта из двух записей и больше: проект из одной записи — это сама запись.
const hubId = (key) => (PCOUNT[key] >= 2 ? "p:" + key : null);
const nodeId = (v) => (/^\d+$/.test(v) ? +v : v);
const TOPIC_W = (DATA.graph?.edges || []).filter(([a, b]) => byId.has(a) && byId.has(b)).map(([, , w]) => w);
// Связи проекта с другими: сколько рёбер по темам идёт из его записей в записи чужого проекта.
const PLINKS = new Map();
for (const [a, b] of DATA.graph?.edges || []) {
  const ka = byId.get(a)?.pkey, kb = byId.get(b)?.pkey;
  if (!ka || !kb || ka === kb) continue;
  for (const [x, y] of [[ka, kb], [kb, ka]]) {
    const m = PLINKS.get(x) || PLINKS.set(x, new Map()).get(x);
    m.set(y, (m.get(y) || 0) + 1);
  }
}
// Мосты карты проектов: у каждого проекта остаются только самые сильные соседи, иначе карта снова станет клубком.
const BRIDGES = [];
{
  const strongest = new Map();
  for (const [key, m] of PLINKS) {
    if (hubId(key)) strongest.set(key, new Set([...m].filter(([k]) => hubId(k)).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k]) => k)));
  }
  for (const [a, m] of PLINKS) for (const [b, w] of m) {
    if (a < b && strongest.has(a) && strongest.has(b) && (strongest.get(a).has(b) || strongest.get(b).has(a))) BRIDGES.push([a, b, w]);
  }
}

function renderGraph(arg) {
  if (!graph) initGraph();
  if (!graph) return;
  requestAnimationFrame(() => {
    graph.resize();
    const id = nodeId(arg);
    if (arg && graph.has(id) && graph.selected()?.id !== id) showOnGraph(id);
  });
}
// Выбрать узел и долететь до него; настоящая версия появляется вместе с графом.
let showOnGraph = () => {};
// Подсветить на графе всё, что находится по слову (тему): до первого открытия графа запрос ждёт в gf.q.
let graphFind = null;
function findOnGraph(text) { gf.q = text; if (graphFind) graphFind(); go("#graph"); }
document.addEventListener("click", (e) => { const b = e.target.closest("[data-gfind]"); if (b) findOnGraph(b.dataset.gfind); });
let resetGraphFilters = () => {};
const popovers = [];
const closePopovers = () => { let was = false; for (const p of popovers) was = p.close() || was; return was; };

function initGraph() {
  const box = $(".graph-box");
  if (!R.length) {
    if (box.querySelector(".graph-empty")) return;
    box.insertAdjacentHTML("beforeend", `<div class="graph-empty">${empty("Граф пока пустой", "Запусти <code>brain sync</code>: записи соберутся вокруг своих проектов.")}</div>`);
    $("#graph-sub").textContent = "";
    $(".graph-tools").hidden = true;
    $("#graph-controls").hidden = true;
    return;
  }
  const logMin = Math.log(500), logMax = Math.log(80000);
  const nodes = R.map((r) => ({
    id: r.id, kind: "record", colorVar: colorVar(r.source), label: r.title, pkey: r.pkey,
    r: 3 + 6 * Math.max(0, Math.min(1, (Math.log(Math.max(1, r.body_len)) - logMin) / (logMax - logMin))),
  }));
  for (const [key, count] of Object.entries(PCOUNT)) {
    if (hubId(key)) nodes.push({ id: hubId(key), kind: "project", label: projName(key), pkey: key, count, r: Math.min(16, 6 + Math.sqrt(count) * 1.4) });
  }
  const links = R.filter((r) => hubId(r.pkey)).map((r) => ({ s: r.id, t: hubId(r.pkey), w: 1, kind: "project" }));
  for (const [a, b, w] of DATA.graph.edges) if (byId.has(a) && byId.has(b)) links.push({ s: a, t: b, w, kind: "topic" });
  for (const [a, b, w] of BRIDGES) links.push({ s: hubId(a), t: hubId(b), w, kind: "bridge" });
  const topicCount = TOPIC_W.length;
  const summary = `${words(R.length, RECORD_FORMS)}, ${words(Object.keys(PCOUNT).length, ["проект", "проекта", "проектов"])}, ${words(topicCount, ["связь", "связи", "связей"])} по темам`;
  $("#graph-sub").textContent = `${summary}. Крупные узлы — проекты, малые — записи, линии — общие темы из карточек.`;
  const canvas = $("#graph");
  canvas.setAttribute("aria-label", `Граф: ${summary}. Те же записи списком на вкладках «Проекты» и «Записи».`);

  const c = $("#graph-controls");
  c.innerHTML = `
    <div class="graph-search">
      <input type="search" id="g-q" placeholder="Найти на графе" aria-label="Найти на графе" aria-describedby="g-found" autocomplete="off">
      <span class="graph-found" id="g-found" aria-live="polite"></span>
    </div>
    <div class="seg" id="g-mode" role="group" aria-label="Что показывать"></div>
    <div class="graph-filter">
      <button class="gbtn" type="button" id="gf-btn" aria-expanded="false" aria-controls="graph-filters">Фильтры<span id="gf-count"></span></button>
      <div class="graph-pop" id="graph-filters" hidden>
        <p class="label">Период</p>
        <div class="seg" id="g-period" role="group" aria-label="Период"></div>
        <p class="label">Источники</p>
        <div class="chips">${SRC.filter((s) => s.count).map((s) => `<button class="chip" type="button" data-src="${esc(s.source)}" aria-pressed="true">
          <span class="dot" style="background:${color(s.source)}"></span>${esc(s.title)} <span class="num">${n(s.count)}</span></button>`).join("")}</div>
      </div>
    </div>`;
  // Карта проектов есть только в 3D: в запасном 2D-графе показываем все записи, как раньше.
  const mapMode = () => canvas.dataset.graphMode === "3d" && gv.mode === "map";
  const computeOk = () => {
    const maxDays = gf.period === "all" ? Infinity : +gf.period;
    gf.ok = new Set(R.filter((r) => gf.sources.has(r.source) && daysAgo(r.updated_at) <= maxDays).map((r) => r.id));
    gf.live = new Set(R.filter((r) => gf.ok.has(r.id)).map((r) => r.pkey));
  };
  const nodeFilter = (node) => {
    if (node.kind !== "record") return gf.live.has(node.pkey);
    if (!gf.ok.has(node.id)) return false;
    return !mapMode() || !!graph?.isLocal() || node.pkey === gf.open;
  };
  computeOk();
  if (!topicCount) gv.topic = false;

  const panel = $("#graph-panel");
  graph = createDashboardGraph(canvas, {
    nodes, links, prefs: gv, filter: nodeFilter,
    // Панель выбранного закрывает часть холста: вписываем и центрируем граф в оставшееся место.
    inset() {
      if (panel.hidden) return {};
      const b = box.getBoundingClientRect(), p = panel.getBoundingClientRect();
      return p.top - b.top > 40 ? { bottom: b.bottom - p.top } : { right: b.right - p.left };
    },
    onOpen(node) { if (node.kind === "record") openRecord(node.id); else go(`#project/${encodeURIComponent(node.pkey)}`); },
    onSelect(node) {
      Charts.tip.hide();
      // На карте проектов выбор узла раскрывает его проект, снятие выбора сворачивает.
      if (mapMode() && !graph.isLocal()) {
        const key = node ? (hubId(node.pkey) ? node.pkey : gf.open) : null;
        // Свернули проект — возвращаемся к общему виду карты, а не остаёмся носом в пустом месте.
        if (key !== gf.open) { gf.open = key; apply(!key); }
      }
      renderPanel(node);
      hint();
      const hash = node ? `#graph/${encodeURIComponent(node.id)}` : "#graph";
      if (location.hash !== hash && current?.tab === "graph") {
        history.replaceState(null, "", hash);
        current = { tab: "graph", arg: node ? String(node.id) : "" };
      }
    },
    onHover(node, x, y) {
      if (!node) return Charts.tip.hide();
      if (node.kind === "project") {
        Charts.tip.show(`<b>${esc(node.label)}</b><small>проект, ${words(node.count, RECORD_FORMS)}</small>`, x, y);
      } else {
        const r = byId.get(node.id);
        Charts.tip.show(`<div class="row"><span class="swatch" style="background:${color(r.source)}"></span><b>${esc(r.title)}</b></div><small>${esc(srcName(r.source))}, ${esc(projName(r.pkey))}, ${esc(dayMonth(date(r.updated_at)))}</small>`, x, y);
      }
    },
  });

  // Поиск и фильтры поверх графа
  const search = () => {
    const q = terms(gf.q), found = $("#g-found");
    gf.at = -1;
    if (!q.length) { gf.hits = []; graph.highlight(null); found.textContent = ""; return; }
    gf.hits = R.filter((r) => gf.ok.has(r.id) && matchAll(r, q)).map((r) => r.id);
    const mark = new Set(gf.hits);
    // На карте записи свёрнуты в проекты: подсвечиваем проекты, где есть найденное.
    if (mapMode()) for (const id of gf.hits) { const hub = hubId(byId.get(id).pkey); if (hub) mark.add(hub); }
    graph.highlight(mark.size ? mark : new Set([-1]));
    found.textContent = gf.hits.length ? `${words(gf.hits.length, RECORD_FORMS)}, Enter — к следующей` : "ничего не нашлось";
  };
  const apply = (refit) => {
    computeOk();
    graph.setFilter(nodeFilter, refit);
    search();
    const changed = (gf.period !== "all") + (gf.sources.size < SRC.length);
    $("#gf-count").textContent = changed ? ` · ${changed}` : "";
  };
  const hint = () => {
    $("#graph-hint").textContent = graph.selected() ? ""
      : mapMode() ? "Клик по проекту раскрывает его записи" : "Клик выбирает узел, второй клик открывает";
  };
  const setMode = (mode) => {
    gv.mode = mode;
    savePrefs();
    $("#g-mode").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === mode));
    const sel = graph.selected();
    gf.open = mode === "map" && sel && hubId(sel.pkey) ? sel.pkey : null;
    graph.setPrefs(gv);
    apply(true);
    hint();
  };
  seg($("#g-mode"), [["map", "Проекты"], ["all", "Все записи"]], mapMode() ? "map" : "all", setMode);
  $("#g-mode").hidden = canvas.dataset.graphMode !== "3d";
  showOnGraph = (id) => {
    const rec = typeof id === "number" ? byId.get(id) : null;
    if (rec ? !gf.ok.has(id) : !graph.passes(id)) resetGraphFilters();
    if (rec && mapMode() && !graph.isLocal()) {
      if (!hubId(rec.pkey)) setMode("all");
      else if (gf.open !== rec.pkey) { gf.open = rec.pkey; apply(false); }
    }
    graph.select(id, { fly: true, zoom: 3 });
  };
  c.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    const s = chip.dataset.src, on = chip.getAttribute("aria-pressed") !== "true";
    chip.setAttribute("aria-pressed", on);
    if (on) gf.sources.add(s); else gf.sources.delete(s);
    apply(true);
  });
  seg($("#g-period"), [["all", "Всё время"], ["90", "90 дней"], ["30", "30 дней"]], gf.period, (v) => { gf.period = v; apply(true); });
  resetGraphFilters = () => {
    gf.sources = new Set(SRC.map((s) => s.source));
    gf.period = "all";
    c.querySelectorAll(".chip").forEach((b) => b.setAttribute("aria-pressed", "true"));
    $("#g-period").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === "all"));
    apply(false);
  };
  const q = $("#g-q");
  q.addEventListener("input", () => { gf.q = q.value; search(); });
  graphFind = () => { q.value = gf.q; search(); };
  if (gf.q) graphFind();
  q.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && q.value) { q.value = gf.q = ""; search(); e.stopPropagation(); return; }
    if (e.key !== "Enter" || !gf.hits.length) return;
    e.preventDefault();
    gf.at = (gf.at + (e.shiftKey ? -1 : 1) + gf.hits.length) % gf.hits.length;
    showOnGraph(gf.hits[gf.at]);
    $("#g-found").textContent = `${n(gf.at + 1)} из ${words(gf.hits.length, RECORD_FORMS)}, Shift+Enter — назад`;
  });

  // Масштаб и настройки вида
  box.querySelector(".graph-tools").addEventListener("click", (e) => {
    const z = e.target.closest("[data-zoom]")?.dataset.zoom;
    if (z === "in") graph.zoom(1.5); else if (z === "out") graph.zoom(1 / 1.5); else if (z === "fit") graph.fit();
  });
  markFull(false);
  $("#g-full").addEventListener("click", () => setGraphFull(!graphFull()));
  // Рамка графа меняет размер не только с окном: во весь экран и обратно. Следим за ней самой.
  let rz = 0;
  new ResizeObserver(() => {
    if (!rz) rz = requestAnimationFrame(() => { rz = 0; if (current?.tab === "graph") graph.resize(); });
  }).observe(box);
  const popover = (btn, panel) => {
    const show = (open) => { panel.hidden = !open; btn.setAttribute("aria-expanded", open); };
    const p = { btn, panel, show, close() { if (panel.hidden) return false; show(false); return true; } };
    btn.addEventListener("click", () => { const open = panel.hidden; closePopovers(); show(open); });
    panel.addEventListener("keydown", (e) => { if (e.key === "Escape") { show(false); btn.focus(); e.stopPropagation(); } });
    popovers.push(p);
    return p;
  };
  const viewPop = popover($("#gv-btn"), $("#graph-view"));
  popover($("#gf-btn"), $("#graph-filters"));
  popover($("#gh-btn"), $("#graph-info"));
  document.addEventListener("pointerdown", (e) => {
    for (const p of popovers) if (!p.panel.hidden && !p.panel.contains(e.target) && !p.btn.contains(e.target)) p.show(false);
  });
  const topicBox = $("#gv-topic"), minw = $("#gv-minw"), areas = $("#gv-areas");
  topicBox.checked = gv.topic;
  topicBox.disabled = !topicCount;
  if (!topicCount) $("#gv-topic-label").textContent = "Связи по темам появятся после пересказа";
  minw.value = gv.minW;
  areas.checked = gv.areas;
  const updateView = () => {
    const shownW = TOPIC_W.filter((w) => w >= gv.minW).length;
    $("#gv-minw-out").textContent = gv.minW ? `от ${String(gv.minW.toFixed(2)).replace(".", ",")}, ${words(shownW, ["связь", "связи", "связей"])}` : `любая, ${words(shownW, ["связь", "связи", "связей"])}`;
    minw.disabled = !gv.topic;
    graph.setPrefs(gv);
    savePrefs();
    if (graph.selected()) renderPanel(graph.selected());
  };
  topicBox.addEventListener("change", () => { gv.topic = topicBox.checked; updateView(); });
  minw.addEventListener("input", () => { gv.minW = +minw.value; updateView(); });
  areas.addEventListener("change", () => { gv.areas = areas.checked; updateView(); });
  seg($("#gv-labels"), [["less", "Меньше"], ["more", "Больше"]], gv.labels, (v) => { gv.labels = v; updateView(); });
  $("#gv-relayout").addEventListener("click", () => { graph.relayout(); viewPop.show(false); });
  updateView();

  // Панель выбранного
  panel.addEventListener("click", (e) => {
    const t = e.target;
    const sel = t.closest("[data-gsel]");
    if (sel) return showOnGraph(nodeId(sel.dataset.gsel));
    const open = t.closest("[data-open]");
    if (open) return openRecord(+open.dataset.open);
    if (t.closest("[data-local]")) {
      graph.setLocal(!graph.isLocal());
      renderPanel(graph.selected());
      return $("#graph-panel [data-local]")?.focus();
    }
    if (t.closest(".gp-close")) graph.select(null);
  });
  apply(false);
  hint();
}

// ---- Панель выбранного узла
const gpItem = (x, note) => `<li><button type="button" data-gsel="${x.id}"><span class="dot" style="background:${color(x.source)}"></span><span class="t">${esc(x.title)}</span><span class="m">${note}</span></button></li>`;
const topicHref = (t) => `#topic/${encodeURIComponent(t)}`;
const topicTags = (list) => (list.length ? `<div class="topics">${list.map((t) => `<a class="tag topic" href="${topicHref(t)}">${esc(t)}</a>`).join("")}</div>` : "");
// Почему два проекта связаны: темы, общие для пар их записей, соединённых на графе.
const SHARED = new Map();
function sharedTopics(a, b) {
  const id = a < b ? `${a}\n${b}` : `${b}\n${a}`;
  if (SHARED.has(id)) return SHARED.get(id);
  const count = new Map();
  for (const [x, y] of DATA.graph?.edges || []) {
    const rx = byId.get(x), ry = byId.get(y);
    if (!rx || !ry || !((rx.pkey === a && ry.pkey === b) || (rx.pkey === b && ry.pkey === a))) continue;
    const mine = new Set(rx.topics);
    for (const t of ry.topics) if (mine.has(t)) count.set(t, (count.get(t) || 0) + 1);
  }
  const top = [...count].sort((p, q) => q[1] - p[1]).slice(0, 3).map(([t]) => t);
  SHARED.set(id, top);
  return top;
}
const LINK_FORMS = ["связь", "связи", "связей"];
const related = (key, limit) => [...(PLINKS.get(key) || new Map())].sort((a, b) => b[1] - a[1]).slice(0, limit);
function panelHead(label) {
  return `<div class="gp-head"><p class="label">${label}</p>
    <button class="gp-close" type="button" aria-label="Снять выбор" title="Снять выбор (Esc)">
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
    </button></div>`;
}
function panelActions(main) {
  const local = graph.isLocal();
  return `<div class="gp-actions">${main}<button class="btn ghost" type="button" data-local aria-pressed="${local}">${local ? "Весь граф" : "Только связанные"}</button></div>`;
}
function panelRecord(r) {
  const hub = hubId(r.pkey);
  const proj = hub ? `<button class="gp-link" type="button" data-gsel="${esc(hub)}">${esc(projName(r.pkey))}</button>`
    : r.pkey ? `<a class="gp-link" href="#project/${encodeURIComponent(r.pkey)}">${esc(projName(r.pkey))}</a>` : "";
  // Почему записи связаны: общие темы из карточек.
  const mine = new Set(r.topics);
  const nb = neighbors(r.id).slice(0, 6).map(([o]) => {
    const x = byId.get(o), common = x.topics.filter((t) => mine.has(t)).slice(0, 3);
    return gpItem(x, esc([projName(x.pkey), common.join(", ")].filter(Boolean).join(" · ")));
  });
  return `
    ${panelHead(`<span class="dot" style="background:${color(r.source)}"></span>${esc(srcName(r.source))}, ${esc(dayMonth(date(r.updated_at)))}`)}
    <h2 class="gp-title">${esc(r.title)}</h2>
    ${proj ? `<p class="gp-proj">Проект ${proj}</p>` : ""}
    ${r.summary ? `<p class="gp-sum">${esc(r.summary)}</p>` : `<p class="gp-sum">${r.error ? "Модель не смогла пересказать эту запись." : "Карточки пока нет: запись ждёт пересказа."}</p>`}
    ${topicTags(r.topics.slice(0, 6))}
    ${panelActions(`<button class="btn" type="button" data-open="${r.id}">Открыть карточку</button>`)}
    <p class="label">Похожие по темам</p>
    ${nb.length ? `<ul class="gp-list">${nb.join("")}</ul>` : `<p class="gp-none">Общих тем с другими записями нет.</p>`}`;
}
function panelProject(key) {
  const meta = PROJECTS[key], recs = R.filter((r) => r.pkey === key);
  const rel = related(key, 5).map(([k, cnt]) =>
    `<li><button type="button" class="nodot" data-gsel="${esc(hubId(k) || R.find((r) => r.pkey === k).id)}"><span class="t">${esc(projName(k))}</span><span class="m wrap">${esc([words(cnt, LINK_FORMS), sharedTopics(key, k).join(", ")].filter(Boolean).join(" · "))}</span></button></li>`);
  return `
    ${panelHead(`Проект, ${words(recs.length, RECORD_FORMS)}`)}
    <h2 class="gp-title">${esc(meta.name)}</h2>
    <p class="paths">${meta.paths.slice(0, 2).map(esc).join("<br>")}${meta.paths.length > 2 ? `<br>и ещё ${meta.paths.length - 2}` : ""}</p>
    ${topicTags(meta.topics)}
    ${panelActions(`<a class="btn" href="#project/${encodeURIComponent(key)}">Страница проекта</a>`)}
    ${rel.length ? `<p class="label">Связан по темам</p><ul class="gp-list">${rel.join("")}</ul>` : ""}
    <p class="label">Последние записи</p>
    <ul class="gp-list">${recs.slice(0, 6).map((x) => gpItem(x, `${esc(srcName(x.source))}, ${esc(dayMonth(date(x.updated_at)))}`)).join("")}</ul>`;
}
function renderPanel(node) {
  const p = $("#graph-panel");
  if (!node) { p.hidden = true; p.innerHTML = ""; return; }
  p.innerHTML = node.kind === "record" ? panelRecord(byId.get(node.id)) : panelProject(node.pkey);
  p.hidden = false;
  p.scrollTop = 0;
}

// Клавиши на вкладке графа: + − 0 — масштаб, стрелки — сдвиг, Esc — снять выбор, Enter — открыть выбранное.
addEventListener("keydown", (e) => {
  if (current?.tab !== "graph" || !graph || $(".drawer") || side.classList.contains("open")) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  if (t.closest && t.closest("input, textarea, select, [contenteditable]")) return;
  const step = 80, k = e.key;
  if (k === "+" || k === "=") graph.zoom(1.5);
  else if (k === "-" || k === "_") graph.zoom(1 / 1.5);
  else if (k === "0") graph.fit();
  else if (e.code === "KeyF") setGraphFull(!graphFull());          // по коду клавиши: работает и в русской раскладке
  else if (k === "ArrowLeft") graph.pan(step, 0);
  else if (k === "ArrowRight") graph.pan(-step, 0);
  else if (k === "ArrowUp") graph.pan(0, step);
  else if (k === "ArrowDown") graph.pan(0, -step);
  else if (k === "Escape" && closePopovers()) { /* закрыли выпадающую панель */ }
  else if (k === "Escape" && graph.isLocal()) { graph.setLocal(false); renderPanel(graph.selected()); }
  else if (k === "Escape" && graph.selected()) graph.select(null);
  else if (k === "Escape" && graphFull()) setGraphFull(false);
  else if (k === "Enter" && graph.selected() && !(t.closest && t.closest("button, a"))) {
    const s = graph.selected();
    if (s.kind === "record") openRecord(s.id); else go(`#project/${encodeURIComponent(s.pkey)}`);
  } else return;
  e.preventDefault();
});

// ---- Граф во весь экран: вместе с фильтрами и поиском. Где браузер так не умеет (iPhone), растягиваем поверх страницы.
const ICON_EXPAND = `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const ICON_COLLAPSE = `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 2.5V6H2.5M13.5 6H10V2.5M10 13.5V10h3.5M2.5 10H6v3.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const fsElement = () => document.fullscreenElement || document.webkitFullscreenElement || null;
const graphFull = () => $("#graph-stage").classList.contains("full");
function setGraphFull(on) {
  const stage = $("#graph-stage");
  if (on === graphFull()) return;
  if (!on) {
    if (fsElement() === stage) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    else markFull(false);
    return;
  }
  const req = stage.requestFullscreen || stage.webkitRequestFullscreen;
  if (!req) return markFull(true, true);
  const fallback = () => { if (fsElement() !== stage && !graphFull()) markFull(true, true); };
  try {
    const p = req.call(stage);
    if (p && p.catch) p.catch(fallback);
  } catch { fallback(); }
  // Бывает, что браузер молча не разворачивает (встроенные окна, запрет на уровне страницы) — не ждём вечно.
  setTimeout(fallback, 700);
}
function markFull(on, fallback) {
  const stage = $("#graph-stage"), btn = $("#g-full");
  stage.classList.toggle("full", on);
  stage.classList.toggle("fallback", on && !!fallback);
  document.documentElement.classList.toggle("graph-full", on && !!fallback);
  btn.setAttribute("aria-pressed", on);
  btn.setAttribute("aria-label", on ? "Свернуть граф" : "Граф на весь экран");
  btn.title = on ? "Свернуть (F)" : "На весь экран (F)";
  btn.innerHTML = on ? ICON_COLLAPSE : ICON_EXPAND;
  // Подсказка при наведении тоже должна жить внутри развёрнутого элемента, иначе её не видно.
  (on ? stage : document.body).append($("#tip"));
}
for (const ev of ["fullscreenchange", "webkitfullscreenchange"]) {
  document.addEventListener(ev, () => {
    const stage = $("#graph-stage");
    if (fsElement() === stage) markFull(true, false);
    else if (graphFull() && !stage.classList.contains("fallback")) markFull(false);
  });
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
    .filter((p) => !q.length || q.every((w) => nrm(projName(p.key) + " " + PROJECTS[p.key].paths.join(" ")).includes(w)))
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
    ${topicTags(meta.topics)}
    ${related(key, 6).length ? `<div class="card">
      <h2 class="card-title">Связанные проекты</h2>
      <p class="card-sub">У их записей общие темы с записями этого проекта</p>
      <ul class="rel">${related(key, 6).map(([k, cnt]) => `<li><a class="t" href="#project/${encodeURIComponent(k)}">${esc(projName(k))}</a>
        <span class="mono">${words(cnt, LINK_FORMS)}</span>${topicTags(sharedTopics(key, k))}</li>`).join("")}</ul>
    </div>` : ""}
    <div class="card">
      <h2 class="card-title">Решения и задачи</h2>
      <p class="card-sub">Последние из карточек этого проекта</p>
      ${atoms.length ? `<ul class="atoms">${atoms.slice(0, 10).map((a) => `<li><span class="tag ${a.type === "todo" ? "wait" : ""}">${a.type === "todo" ? "задача" : "решение"}</span>${esc(a.text)}
        <button class="from" type="button" data-id="${a.r.id}">${esc(dayMonth(date(a.r.updated_at)))}, ${esc(a.r.title)}</button></li>`).join("")}</ul>`
        : `<p class="muted" style="margin:var(--space-3) 0 0">В карточках этого проекта решений и задач пока нет.</p>`}
      ${records.some((r) => r.atoms.length) ? `<p class="card-foot"><button class="btn sm" type="button" data-kp="${esc(key)}">Все знания проекта</button></p>` : ""}
    </div>
    <h2 class="card-title section-title">Записи</h2>
    <div class="feed">${[...months.values()].map((m) => `<div class="day"><h3>${esc(monthName(m.d))}</h3><div class="rows">${m.list.map(recordRow).join("")}</div></div>`).join("")}</div>`;
  view.style.display = "grid";
  view.style.gap = "var(--space-5)";
}

// ---- 04 Записи и здоровье базы
// ---- 04 Темы: тема как отдельная сущность — её записи, проекты, соседние темы
const allTopics = (r) => r.topics_all || r.topics;
let TOPICS = null, topicsReady = false, tsort = "size", tq = "";
function topicIndex() {
  if (TOPICS) return TOPICS;
  TOPICS = new Map();
  for (const r of R) for (const t of allTopics(r)) (TOPICS.get(t) || TOPICS.set(t, []).get(t)).push(r);
  return TOPICS;
}
function renderTopics(arg) {
  const list = $("#topics-list-view"), view = $("#topic-view");
  if (arg) { list.hidden = true; view.hidden = false; showTopic(arg); return; }
  list.hidden = false; view.hidden = true;
  if (topicsReady) return;
  topicsReady = true;
  $("#topics-sub").textContent = topicIndex().size ? "Темы берутся из ключевых слов карточек. По общим темам строятся связи на графе." : "";
  seg($("#topics-sort"), [["size", "Больше записей"], ["recent", "Недавние"]], tsort, (v) => { tsort = v; drawTopics(); });
  $("#q-topics").addEventListener("input", (e) => { tq = e.target.value; drawTopics(); });
  drawTopics();
}
function drawTopics() {
  const q = terms(tq), all = [...topicIndex()];
  // Тема из одной записи ничего не связывает: без фильтра такие не показываем.
  const shown = all.filter(([t, rs]) => (q.length ? q.every((w) => nrm(t).includes(w)) : rs.length >= 2))
    .sort(tsort === "size" ? (a, b) => b[1].length - a[1].length || (b[1][0].updated_at > a[1][0].updated_at ? 1 : -1) : (a, b) => (b[1][0].updated_at > a[1][0].updated_at ? 1 : -1));
  $("#topics-count").textContent = !all.length ? "" : q.length ? `Найдено ${n(shown.length)} из ${n(all.length)}`
    : `${words(shown.length, ["тема", "темы", "тем"])} хотя бы из двух записей, всего ${n(all.length)}`;
  $("#tgrid").innerHTML = shown.slice(0, 400).map(([t, rs]) => `<a class="trow" href="${topicHref(t)}">
    <span class="t">${hl(t, q)}</span><span class="mono">${esc(dayMonth(date(rs[0].updated_at)))}</span><span class="d">${n(rs.length)}</span></a>`).join("")
    || empty(all.length ? "Ничего не нашлось" : "Тем пока нет", all.length ? "Попробуй другое слово." : "Они появятся, когда записи получат карточки.");
}
function showTopic(name) {
  const view = $("#topic-view"), records = topicIndex().get(name);
  if (!records) {
    view.innerHTML = `<p class="crumb"><a href="#topics">Темы</a></p><h1 class="headline" tabindex="-1">Тема не найдена</h1>
      <p class="lede">Такой темы в этом снимке базы нет. Вернись к <a href="#topics">списку тем</a>.</p>`;
    return;
  }
  const near = new Map(), projects = new Map();
  for (const r of records) {
    for (const t of allTopics(r)) if (t !== name) near.set(t, (near.get(t) || 0) + 1);
    if (r.pkey) projects.set(r.pkey, (projects.get(r.pkey) || 0) + 1);
  }
  const top = (m, k) => [...m].sort((a, b) => b[1] - a[1]).slice(0, k);
  const atoms = [];
  for (const r of records) for (const a of r.atoms) { const p = parseAtom(a); if (p.type === "decision" || p.type === "todo") atoms.push({ ...p, r }); }
  const groups = [];
  for (const r of records.slice(0, 100)) {
    const label = dayLabel(date(r.updated_at));
    if (!groups.length || groups[groups.length - 1].label !== label) groups.push({ label, list: [] });
    groups[groups.length - 1].list.push(r);
  }
  const first = records[records.length - 1], last = records[0];
  view.innerHTML = `
    <header class="tab-head">
      <p class="crumb"><a href="#topics">Темы</a> / ${esc(name)}</p>
      <h1 class="headline" tabindex="-1">${esc(name)}</h1>
      <p class="lede">${words(records.length, RECORD_FORMS)}${projects.size ? ` в ${n(projects.size)} ${plural(projects.size, "проекте", "проектах", "проектах")}` : ""}, ${esc(dayMonth(date(first.updated_at)))} – ${esc(dayMonth(date(last.updated_at)))}</p>
      <p class="t-actions"><button class="btn sm" type="button" data-gfind="${esc(name)}">Показать на графе</button></p>
    </header>
    <div class="split">
      <div class="card">
        <h2 class="card-title">Проекты</h2>
        <p class="card-sub">Где эта тема встречается</p>
        ${projects.size ? `<div class="fresh">${top(projects, 8).map(([k, c]) => `<a class="wrow two" href="#project/${encodeURIComponent(k)}"><span class="t">${esc(projName(k))}</span><span class="d">${n(c)}</span></a>`).join("")}</div>`
          : `<p class="muted" style="margin:var(--space-3) 0 0">Записи с этой темой не привязаны к проектам.</p>`}
      </div>
      <div class="card">
        <h2 class="card-title">Соседние темы</h2>
        <p class="card-sub">Чаще всего стоят рядом в одних и тех же записях</p>
        ${near.size ? `<div class="topics" style="margin-top:var(--space-3)">${top(near, 14).map(([t, c]) => `<a class="tag topic" href="${topicHref(t)}">${esc(t)} <span class="num">${n(c)}</span></a>`).join("")}</div>`
          : `<p class="muted" style="margin:var(--space-3) 0 0">Других тем в этих записях нет.</p>`}
      </div>
    </div>
    ${atoms.length ? `<div class="card">
      <h2 class="card-title">Решения и задачи</h2>
      <p class="card-sub">Последние из записей с этой темой</p>
      <ul class="atoms">${atoms.slice(0, 10).map((a) => `<li><span class="tag ${a.type === "todo" ? "wait" : ""}">${a.type === "todo" ? "задача" : "решение"}</span>${esc(a.text)}
        <button class="from" type="button" data-id="${a.r.id}">${esc(dayMonth(date(a.r.updated_at)))}, ${esc(a.r.title)}</button></li>`).join("")}</ul>
    </div>` : ""}
    <h2 class="card-title section-title">Записи</h2>
    <div class="feed">${groups.map((g) => `<div class="day"><h3>${esc(g.label)}</h3><div class="rows">${g.list.map((r) => recordRow(r)).join("")}</div></div>`).join("")}</div>
    ${records.length > 100 ? `<p class="count" style="margin-top:var(--space-3)">Показаны последние 100 из ${n(records.length)}.</p>` : ""}`;
}

// ---- 05 Знания: все решения, задачи, команды и прочее из карточек одной лентой
const K_LIMIT = 60;
const kf = { type: "all", pkey: "", days: 0, q: "", limit: K_LIMIT };
let knowledgeReady = false;
function showKnowledge(f) {
  Object.assign(kf, { type: "all", pkey: "", days: 0, q: "", limit: K_LIMIT }, f);
  go("#knowledge");
}
document.addEventListener("click", (e) => { const b = e.target.closest("[data-kp]"); if (b) showKnowledge({ pkey: b.dataset.kp }); });
function knowledge() {
  const q = terms(kf.q), groups = [];
  let total = 0;
  for (const r of R) {
    if (kf.pkey && r.pkey !== kf.pkey) continue;
    if (kf.days && daysAgo(r.updated_at) > kf.days) continue;
    const x = index().get(r.id);
    let list = kf.type === "all" ? x.atoms : x.atoms.filter((a) => a.type === kf.type);
    // Слова из названия и проекта задают контекст, остальные должны быть в самом знании.
    const own = q.filter((w) => !x.head.includes(w));
    if (own.length) list = list.filter((a) => own.every((w) => a.n.includes(w)));
    if (list.length) { groups.push({ r, list }); total += list.length; }
  }
  return { groups, total, q };
}
const atomLabel = (type) => ATOM[type]?.[0] || "знание";
function knowledgeTitle() {
  return [kf.type === "all" ? "Знания" : ATOM_TITLE[kf.type] || kf.type, kf.pkey && `проект ${projName(kf.pkey)}`,
    kf.days && `за ${words(kf.days, ["день", "дня", "дней"])}`, kf.q.trim() && `«${kf.q.trim()}»`].filter(Boolean).join(" · ");
}
function knowledgeMarkdown() {
  const { groups } = knowledge();
  return `# ${knowledgeTitle()}\n\n` + groups.map(({ r, list }) =>
    `## ${r.title}\n${projName(r.pkey)}, ${fullDate(date(r.updated_at))}${/^https?:/.test(r.location || "") ? `, ${r.location}` : ""}\n\n${list.map((a) => `- **${atomLabel(a.type)}:** ${a.text}`).join("\n")}`).join("\n\n") + "\n";
}
function drawKnowledge() {
  const { groups, total, q } = knowledge();
  $("#k-count").textContent = total
    ? `${words(total, ["знание", "знания", "знаний"])} в ${n(groups.length)} ${plural(groups.length, "записи", "записях", "записях")}, свежие сверху` : "";
  $("#k-copy").hidden = !total;
  if (!total) {
    $("#k-feed").innerHTML = empty(R.length ? "Ничего не нашлось" : "Знаний пока нет", R.length ? "Попробуй другое слово или сбрось фильтры." : "Они появятся, когда записи получат карточки.");
    return;
  }
  $("#k-feed").innerHTML = groups.slice(0, kf.limit).map(({ r, list }) => `<article class="kgroup">
    <button class="khead" type="button" data-id="${r.id}">
      <span class="dot" style="background:${color(r.source)}" title="${esc(srcName(r.source))}"></span>
      <span class="t">${hl(r.title, q)}</span>
      <span class="mono">${esc(projName(r.pkey))} · ${esc(dayMonth(date(r.updated_at)))}</span>
    </button>
    <ul class="klist">${list.map((a) => `<li><span class="tag ${a.type === "todo" ? "wait" : ""}">${esc(atomLabel(a.type))}</span><span class="x">${hl(a.text, q)}</span>${copyBtn(a.text)}</li>`).join("")}</ul>
  </article>`).join("")
    + (groups.length > kf.limit ? `<button class="more" type="button">Показать ещё ${n(Math.min(K_LIMIT, groups.length - kf.limit))}</button>` : "");
}
function renderKnowledge() {
  const press = (id, v) => $(id).querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === String(v)));
  if (!knowledgeReady) {
    knowledgeReady = true;
    const counts = {};
    let all = 0;
    for (const r of R) for (const a of r.atoms) { const t = parseAtom(a).type; counts[t] = (counts[t] || 0) + 1; all++; }
    $("#k-sub").textContent = all ? "Всё, что модель вынесла из записей: решения, задачи, исправления, команды. Клик по записи открывает её карточку." : "";
    const change = () => { kf.limit = K_LIMIT; drawKnowledge(); };
    seg($("#k-type"), [["all", `Все ${n(all)}`], ...Object.keys(ATOM_TITLE).filter((t) => counts[t]).map((t) => [t, `${ATOM_TITLE[t]} ${n(counts[t])}`])], kf.type, (v) => { kf.type = v; change(); });
    seg($("#k-period"), [["0", "Всё время"], ["7", "Неделя"], ["30", "Месяц"], ["365", "Год"]], String(kf.days), (v) => { kf.days = +v; change(); });
    const withAtoms = new Set(R.filter((r) => r.atoms.length && r.pkey).map((r) => r.pkey));
    $("#k-project").innerHTML = `<option value="">Все проекты</option>` + [...withAtoms].sort((a, b) => PCOUNT[b] - PCOUNT[a])
      .map((k) => `<option value="${esc(k)}">${esc(projName(k))}</option>`).join("");
    $("#k-project").addEventListener("change", (e) => { kf.pkey = e.target.value; change(); });
    $("#q-knowledge").addEventListener("input", (e) => { kf.q = e.target.value; change(); });
    $("#k-copy").addEventListener("click", (e) => { e.currentTarget.dataset.copy = knowledgeMarkdown(); copyText(e.currentTarget); });
    $("#k-feed").addEventListener("click", (e) => {
      const copy = e.target.closest("[data-copy]");
      if (copy) return copyText(copy);
      if (e.target.closest(".more")) { kf.limit += K_LIMIT; drawKnowledge(); }
    });
  }
  // Сюда приходят и с готовым фильтром, например со страницы проекта.
  press("#k-type", kf.type); press("#k-period", kf.days);
  $("#k-project").value = kf.pkey;
  $("#q-knowledge").value = kf.q;
  drawKnowledge();
}

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
    // Синхронизация без ошибок ещё не значит, что источник что-то приносит: показываем, сколько он молчит.
    const sil = (DATA.silence || {})[s.source];
    const silence = !sil || sil.silent_days < 1 ? ""
      : sil.quiet ? ` <span class="tag wait" title="Обычная пауза — до ${n(sil.usual_days)} дн.">тихо ${n(sil.silent_days)} дн.</span>`
      : ` <span class="quiet-note">записей нет ${n(sil.silent_days)} дн.${sil.usual_days ? `, обычно до ${n(sil.usual_days)}` : ""}</span>`;
    const name = `<span class="src"><span class="dot" style="background:${color(s.source)}"></span>${esc(s.title)}</span>`;
    if (s.hint && !rs.length) return `<div class="hrow">${name}<span class="st">${status}</span><span class="hint">${esc(s.hint)}</span></div>`;
    return `<div class="hrow">${name}<span class="st">${status}${silence}</span>
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
  return !q.length || matchDeep(r, q);
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
  const list = R.filter(feedMatches), q = terms(rf.q);
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
  $("#feed").innerHTML = groups.map((g) => `<div class="day"><h3>${esc(g.label)}</h3><div class="rows">${g.list.map((r) => recordRow(r, q.length ? snippet(hit(r, q).sn, q) : "")).join("")}</div></div>`).join("")
    + (list.length > rf.limit ? `<button class="more" type="button">Показать ещё ${n(Math.min(100, list.length - rf.limit))}</button>` : "");
}

// ---- Карточка записи: закреплённая шапка, переходы по разделам, знания по типам, артефакты и разговор по репликам.
let lastFocus = null, navList = [];
const ATOM_ORDER = ["decision", "todo", "fix", "command", "error", "fact"];
const ATOM_TITLE = { decision: "Решения", todo: "Задачи", fix: "Исправления", command: "Команды", error: "Ошибки", fact: "Факты" };
const SPEAKERS = "Пользователь|Claude Code|Claude|Codex|Kimi Code|Kimi|ChatGPT";
const FILE_EXT = "py|js|mjs|cjs|ts|tsx|jsx|md|mdx|json|jsonl|html|css|scss|sh|yml|yaml|toml|swift|sql|txt|csv|pdf|png|jpe?g|webp|svg|mp4|mov|m4a|mp3|docx?|xlsx?|pptx?|zip|env|lock|plist|ipynb";
const NOT_FILES = /^(next|node|three|vue|react|d3|anime|express|nuxt|nest|chart|alpine|socket|p5)\.js$/i;
const CMD_START = /^(\$ )?(git|npm|npx|pnpm|yarn|bun|node|python3?|pip3?|uv|brain|bin\/brain|curl|gh|docker|brew|make|ffmpeg|claude|codex|vercel|ssh|cd|ls|cat|rm|mkdir|open|launchctl|sqlite3|psql|cargo|go|swift|xcodebuild)\b/;

// Ссылки, файлы и команды из текста записи и её атомов. Ничего не хранится в базе: считаем при открытии карточки.
function artifacts(r) {
  const text = [r.preview, ...r.atoms].join("\n");
  const links = new Map(), files = new Set(), commands = new Set();
  const noUrls = text.replace(/https?:\/\/[^\s<>"'`\]]+/g, (raw) => {
    let u = raw.replace(/[.,;:!?…)*]+$/, "");
    if ((u.match(/\(/g) || []).length > (u.match(/\)/g) || []).length) u = raw.replace(/[.,;:!?…*]+$/, "");
    try { const x = new URL(u); if (x.hostname.includes(".")) links.set(u, x); } catch { /* не ссылка */ }
    return " ";
  });
  if (/^https?:\/\//.test(r.location || "")) links.delete(r.location);    // источник и так в шапке
  for (const m of noUrls.matchAll(/\[вложения: ([^\]]+)\]/g)) for (const f of m[1].split(", ")) files.add(f.trim());
  const fileRe = new RegExp(`(?:~|\\.{1,2})?(?:/?[\\w.@+-]+/)*[\\w.@+-]+\\.(?:${FILE_EXT})(?![\\w/])`, "gi");
  for (const m of noUrls.replace(/\[вложения: [^\]]+\]/g, " ").matchAll(fileRe)) {
    const f = m[0];
    if (!NOT_FILES.test(f) && /[a-zа-я]/i.test(f.split(".")[0]) && f.length > 4) files.add(f);
  }
  const addCommand = (c) => { c = c.trim().replace(/^\$ /, ""); if (c.length > 2 && c.length < 300) commands.add(c); };
  for (const a of r.atoms) {
    const p = parseAtom(a);
    if (p.type !== "command") continue;
    const inline = [...p.text.matchAll(/`([^`\n]+)`/g)].map((m) => m[1]);
    (inline.length ? inline : [p.text]).forEach(addCommand);
  }
  for (const m of r.preview.matchAll(/```(?:bash|sh|shell|zsh|console)\n([\s\S]*?)```/g)) m[1].split("\n").filter((l) => l.trim() && !l.trim().startsWith("#")).forEach(addCommand);
  for (const m of r.preview.matchAll(/`([^`\n]{3,200})`/g)) if (CMD_START.test(m[1])) addCommand(m[1]);
  return { links: [...links].slice(0, 40), files: [...files].slice(0, 40), commands: [...commands].slice(0, 30) };
}
// Разговор по репликам: чаты — «Пользователь: … / Claude: …», почта — «Имя <адрес>, дата время:».
function turns(r) {
  const chat = new RegExp(`^(${SPEAKERS}): `);
  const mail = /^(.{1,120}), (\d{4}-\d{2}-\d{2} \d{2}:\d{2}):$/;
  const out = [];
  let cur = { who: "", text: [] }, blank = true;
  for (const line of r.preview.split("\n")) {
    const c = blank && line.match(chat), m = blank && !c && line.match(mail);
    if (c || m) {
      out.push(cur);
      cur = c ? { who: c[1], text: [line.slice(c[0].length)] } : { who: m[1].replace(/\s*<[^>]*>/, ""), when: m[2], text: [] };
    } else cur.text.push(line);
    blank = !line.trim();
  }
  out.push(cur);
  // Пустые строки подряд и «&#x20;» из редакторов только растягивают реплику.
  const tidy = (lines) => lines.join("\n").replace(/&#x20;/g, " ").replace(/\n{3,}/g, "\n\n").trim();
  const list = out.map((t) => ({ ...t, text: tidy(t.text) })).filter((t) => t.who || t.text);
  return list.filter((t) => t.who).length >= 1 ? list : null;
}
const copyBtn = (text, label = "Копировать") => `<button class="copy" type="button" data-copy="${esc(text)}">${label}</button>`;
function copyText(btn) {
  const done = () => { const was = btn.textContent; btn.textContent = "Скопировано"; setTimeout(() => { btn.textContent = was; }, 1200); };
  const text = btn.dataset.copy;
  if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(done, () => {});
  else {
    const t = document.createElement("textarea");
    t.value = text; btn.after(t); t.select();
    try { document.execCommand("copy"); done(); } catch { /* не вышло — не страшно */ }
    t.remove();
  }
}
function openRecord(id, list) {
  const r = byId.get(id);
  if (!r) return;
  const replacing = !!$(".drawer");
  if (list) navList = list;
  if (!replacing) lastFocus = document.activeElement;
  closeRecord(true);
  Charts.tip.hide();
  const at = navList.indexOf(id), prev = at > 0 ? navList[at - 1] : null, next = at >= 0 && at < navList.length - 1 ? navList[at + 1] : null;
  const notice = r.card ? "" : r.error
    ? `<p class="notice err">Модель не смогла пересказать эту запись: ${esc(r.error)}. Попробует ещё раз в одном из следующих прогонов.</p>`
    : `<p class="notice wait">Карточки пока нет: запись в очереди на пересказ.</p>`;
  // Знания по типам
  const groups = new Map();
  for (const a of r.atoms) { const p = parseAtom(a); (groups.get(p.type) || groups.set(p.type, []).get(p.type)).push(p.text); }
  const types = [...ATOM_ORDER.filter((t) => groups.has(t)), ...[...groups.keys()].filter((t) => !ATOM_ORDER.includes(t))];
  const atoms = types.map((t) => `<h4 class="dr-sub">${esc(ATOM_TITLE[t] || t || "Прочее")} <span class="num">${groups.get(t).length}</span></h4>
    <ul class="dr-list">${groups.get(t).map((x) => `<li class="a-${esc(t)}">${esc(x)}</li>`).join("")}</ul>`).join("");
  // Артефакты
  const art = artifacts(r), artCount = art.links.length + art.files.length + art.commands.length;
  const artHtml = [
    art.links.length ? `<h4 class="dr-sub">Ссылки <span class="num">${art.links.length}</span></h4><ul class="arts">${art.links.map(([u, x]) =>
      `<li><a href="${esc(u)}" target="_blank" rel="noopener noreferrer"><span class="host">${esc(x.hostname.replace(/^www\./, ""))}</span><span class="path">${esc(decodeURI(x.pathname + x.search).replace(/\/$/, "") || "/")}</span></a>${copyBtn(u)}</li>`).join("")}</ul>` : "",
    art.files.length ? `<h4 class="dr-sub">Файлы <span class="num">${art.files.length}</span></h4><ul class="arts">${art.files.map((f) =>
      `<li><code>${esc(f)}</code>${copyBtn(f)}</li>`).join("")}</ul>` : "",
    art.commands.length ? `<h4 class="dr-sub">Команды <span class="num">${art.commands.length}</span></h4><ul class="arts">${art.commands.map((c) =>
      `<li><code>${esc(c)}</code>${copyBtn(c)}</li>`).join("")}</ul>` : "",
  ].join("");
  // Разговор
  const cut = r.body_len > DATA.preview_chars;
  const talk = turns(r);
  const talkHtml = talk
    ? talk.map((t) => `<div class="turn${t.who === "Пользователь" || /^я$/i.test(t.who) ? " me" : ""}${t.who ? "" : " pre"}">
        ${t.who ? `<p class="who">${esc(t.who === "Пользователь" ? "Ты" : t.who)}${t.when ? `<span>${esc(t.when)}</span>` : ""}</p>` : ""}
        <div class="txt">${esc(t.text)}</div></div>`).join("")
    : `<pre class="transcript">${esc(r.preview)}</pre>`;
  const sim = neighbors(r.id).slice(0, 6).map(([o]) => {
    const x = byId.get(o), common = x.topics.filter((t) => r.topics.includes(t)).slice(0, 3);
    return `<li><button type="button" data-open="${o}"><span class="dot" style="background:${color(x.source)}"></span><span class="t">${esc(x.title)}</span><span class="w">${esc(projName(x.pkey))}${common.length ? ` · общее: ${esc(common.join(", "))}` : ""}</span></button></li>`;
  });
  const isUrl = /^https?:\/\//.test(r.location || "");
  const sections = [
    ["sum", "Кратко", null, `${notice}
      ${r.summary ? `<p class="dr-summary">${esc(r.summary)}</p>` : notice ? "" : `<p class="muted">Пересказа нет.</p>`}
      ${r.topics.length ? topicTags(r.topics) : ""}
      ${r.questions.length ? `<h4 class="dr-sub">Как это будут искать</h4><ul class="dr-list plain">${r.questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ul>` : ""}
      <details class="dr-props"><summary>Свойства записи</summary>
        <dl class="meta">
          <dt>Начат</dt><dd>${esc(stamp(date(r.started_at)))}</dd>
          <dt>Последнее сообщение</dt><dd>${esc(stamp(date(r.updated_at)))}</dd>
          <dt>Проект</dt><dd class="mono">${esc(r.project || "—")}</dd>
          <dt>Источник</dt><dd class="mono">${esc(r.location || "—")}</dd>
          <dt>В базе</dt><dd class="mono">id ${r.id}, ${n(r.body_len)} символов</dd>
          ${r.keywords ? `<dt>Ключевые слова</dt><dd>${esc(r.keywords)}</dd>` : ""}
        </dl></details>`],
    atoms ? ["atoms", "Знания", r.atoms.length, atoms] : null,
    artCount ? ["art", "Артефакты", artCount, artHtml + (cut ? `<p class="dr-note">Собраны по началу записи и её знаниям: в панели лежат первые ${n(DATA.preview_chars)} символов.</p>` : "")] : null,
    ["talk", talk ? "Разговор" : "Текст", null, `<p class="dr-note">${cut
      ? `Первые ${n(DATA.preview_chars)} из ${n(r.body_len)} символов. Целиком: <code>brain show ${r.id}</code> ${copyBtn(`brain show ${r.id}`)}`
      : `${n(r.body_len)} символов.`}</p>${talkHtml}`],
    sim.length ? ["sim", "Похожие", sim.length, `<ul class="similar">${sim.join("")}</ul>`] : null,
  ].filter(Boolean);
  const scrimEl = document.createElement("div");
  scrimEl.className = "scrim";
  scrimEl.addEventListener("click", () => closeRecord());
  const d = document.createElement("aside");
  d.className = "drawer" + (replacing ? " swap" : "");
  d.setAttribute("role", "dialog");
  d.setAttribute("aria-modal", "true");
  d.setAttribute("aria-label", r.title);
  d.dataset.id = r.id;
  const arrow = (to, label, path) => `<button class="icon-btn" type="button" data-open="${to ?? ""}" ${to == null ? "disabled" : ""} aria-label="${label}" title="${label}">
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="${path}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></button>`;
  d.innerHTML = `
    <header class="dr-head">
      <div class="dr-top">
        <p class="label"><span class="dot" style="background:${color(r.source)}"></span>${esc(srcName(r.source))} · ${esc(dayMonth(date(r.updated_at)))}${at >= 0 && navList.length > 1 ? ` · ${at + 1} из ${n(navList.length)}` : ""}</p>
        <div class="dr-btns">
          ${navList.length > 1 && at >= 0 ? arrow(prev, "Предыдущая запись (←)", "M10 3L5 8l5 5") + arrow(next, "Следующая запись (→)", "M6 3l5 5-5 5") : ""}
          <button class="icon-btn close" type="button" aria-label="Закрыть" title="Закрыть (Esc)">
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button>
        </div>
      </div>
      <h2>${esc(r.title)}</h2>
      <div class="drawer-actions">
        ${r.pkey ? `<a class="btn sm" href="#project/${encodeURIComponent(r.pkey)}">${esc(projName(r.pkey))}</a>` : ""}
        ${isUrl ? `<a class="btn sm" href="${esc(r.location)}" target="_blank" rel="noopener noreferrer">Открыть источник ↗</a>` : r.location ? copyBtn(r.location, "Копировать путь").replace('class="copy"', 'class="btn sm copy-btn"') : ""}
        <button class="btn sm" type="button" data-graph="${r.id}">На графе</button>
        <button class="btn sm" type="button" data-pin="${r.id}" aria-pressed="${isPinned(r.id)}" title="Закрепить на обзоре (S)">${pinLabel(r.id)}</button>
      </div>
      <nav class="dr-tabs" aria-label="Разделы карточки">${sections.map(([key, title, count], i) =>
        `<button type="button" data-sec="${key}" aria-current="${i === 0}">${title}${count ? ` <span class="num">${n(count)}</span>` : ""}</button>`).join("")}</nav>
    </header>
    <div class="dr-body">${sections.map(([key, title, , html]) => `<section class="dr-sec" data-sec="${key}"><h3>${title}</h3>${html}</section>`).join("")}</div>`;
  const tabs = [...d.querySelectorAll(".dr-tabs button")], secs = [...d.querySelectorAll(".dr-sec")];
  const headH = () => d.querySelector(".dr-head").offsetHeight;
  // Подсвечиваем раздел, который сейчас под шапкой.
  let jumping = 0;
  const spy = () => {
    if (Date.now() < jumping) return;
    const edge = d.getBoundingClientRect().top + headH() + 24;
    let on = secs[0];
    for (const sec of secs) if (sec.getBoundingClientRect().top <= edge) on = sec;
    if (d.scrollTop + d.clientHeight >= d.scrollHeight - 4) on = secs[secs.length - 1];
    tabs.forEach((b) => b.setAttribute("aria-current", b.dataset.sec === on.dataset.sec));
  };
  d.addEventListener("scroll", spy, { passive: true });
  d.addEventListener("click", (e) => {
    const t = e.target;
    if (t.closest(".close")) return closeRecord();
    const pin = t.closest("[data-pin]");
    if (pin) return togglePin(+pin.dataset.pin);
    const copy = t.closest("[data-copy]");
    if (copy) return copyText(copy);
    const tab = t.closest(".dr-tabs button");
    if (tab) {
      const sec = secs.find((x) => x.dataset.sec === tab.dataset.sec);
      tabs.forEach((b) => b.setAttribute("aria-current", b === tab));
      jumping = Date.now() + 600;
      d.scrollTo({ top: sec === secs[0] ? 0 : sec.offsetTop - headH() - 8, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      return;
    }
    const open = t.closest("[data-open]");
    if (open) {
      const label = open.getAttribute("aria-label");
      openRecord(+open.dataset.open);
      // Стрелкой листают подряд: фокус остаётся на той же стрелке.
      if (label) [...document.querySelectorAll(".drawer .dr-btns button")].find((b) => b.getAttribute("aria-label") === label && !b.disabled)?.focus();
      return;
    }
    const g = t.closest("[data-graph]");
    if (g) { closeRecord(true); go(`#graph/${g.dataset.graph}`); return; }
    if (t.closest("a[href^='#']")) closeRecord(true);
  });
  // Во весь экран браузер показывает только развёрнутый элемент, поэтому карточку кладём в него.
  (fsElement() || document.body).append(scrimEl, d);
  d.querySelector(".close").focus();
}
function closeRecord(silent) {
  document.querySelectorAll(".scrim, .drawer").forEach((el) => el.remove());
  if (!silent && lastFocus && document.contains(lastFocus)) lastFocus.focus();
  if (!silent) lastFocus = null;
}
// ← → листают записи списка, из которого открыта карточка.
addEventListener("keydown", (e) => {
  const d = $(".drawer");
  if (!d || e.metaKey || e.ctrlKey || e.altKey || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
  const at = navList.indexOf(+d.dataset.id), to = navList[at + (e.key === "ArrowLeft" ? -1 : 1)];
  if (at < 0 || to == null) return;
  e.preventDefault();
  openRecord(to);
});
addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if ($(".drawer")) closeRecord();
  else if (side.classList.contains("open")) closeMenu();
});
// Фокус не уходит из карточки клавишей Tab.
addEventListener("keydown", (e) => {
  const d = $(".drawer");
  if (e.key !== "Tab" || !d) return;
  const f = [...d.querySelectorAll("button:not([disabled]), a[href], summary, [tabindex]:not([tabindex='-1'])")];
  if (!f.length) return;
  if (e.shiftKey && document.activeElement === f[0]) { f[f.length - 1].focus(); e.preventDefault(); }
  else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { f[0].focus(); e.preventDefault(); }
});

let resizeTimer;
addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (current?.tab === "overview") drawWeekly();
  }, 100);
});

route();
