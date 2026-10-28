/* Данные, вкладки, адреса, поиск и карточка записи. */
const META = Object.fromEntries(DATA.sources_meta.map((s) => [s.source, s]));
const SOURCES = DATA.sources_meta.map((s) => [s.source, s.title]);
const SOURCE_NAME = Object.fromEntries(SOURCES);
const ATOM_NAME = { decision: "решение", fix: "исправление", error: "ошибка", command: "команда", fact: "факт", todo: "задача" };
const WEEKS = 26;

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = new Intl.NumberFormat("ru-RU");
const n = (x) => nf.format(x);
const date = (iso) => (iso ? new Date(iso) : null);
const dayMonth = (d) => d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", "");
const fullDate = (d) => d.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
const clock = (d) => d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
const stamp = (d) => (d ? `${fullDate(d)}, ${clock(d)}` : "—");

function ago(d) {
  const m = Math.round((Date.now() - d.getTime()) / 60000);
  if (m < 1) return "только что";
  if (m < 60) return `${m} мин назад`;
  if (m < 60 * 24) return `${Math.round(m / 60)} ч назад`;
  return `${Math.round(m / 1440)} дн назад`;
}

function plural(x, one, few, many) {
  const a = Math.abs(x) % 100, b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}

function shortPath(p) {
  if (!p) return "";
  const parts = p.split("/").filter(Boolean);
  return parts.length > 3 ? "…/" + parts.slice(-2).join("/") : p;
}

const recs = DATA.records;
const st = DATA.status || {};
const total = recs.length;
const carded = recs.filter((r) => r.card).length;
const pending = total - carded;
const failed = recs.filter((r) => !r.card && r.error).length;
const syncErrors = Object.entries(DATA.sync).filter(([, s]) => s && s.last_error);

/* Шапка */
$("#db-path").textContent = DATA.db_path.replace(/^\/Users\/[^/]+/, "~");
(function lede() {
  const gen = date(DATA.generated_at);
  const last = date(st.last_sync_at);
  const p = st.progress || {};
  const now = {
    sync: "Прямо сейчас забираю новые разговоры.",
    summarize: `Прямо сейчас пересказываю ${Math.min((p.done || 0) + 1, p.total || 0)} из ${p.total || 0}.`,
    error: "Последний прогон завершился ошибкой.",
    interrupted: "Последний прогон прервался, следующий начнётся по расписанию.",
    none: "Синхронизация ещё не запускалась.",
  }[DATA.state] || (pending ? "Сейчас всё спокойно, очередь пересказа разбирается каждые 15 минут." : "Всё забрано и пересказано.");
  const parts = [`Панель собрана в <b>${clock(gen)}</b>.`];
  if (last) parts.push(`Последняя синхронизация в <b>${clock(last)}</b>, ${ago(last)}.`);
  parts.push(now);
  $("#lede").innerHTML = parts.join(" ");
})();

/* 01 Сводка */
(function tiles() {
  const bySource = SOURCES.map(([k, name]) => `${name} ${n(recs.filter((r) => r.source === k).length)}`).join(" · ");
  const pct = total ? Math.round((carded / total) * 100) : 0;
  const runs = Math.ceil(pending / DATA.batch_limit);
  const items = [
    ["Разговоров", n(total), bySource],
    ["С карточкой", n(carded), `${pct}% базы`, pct],
    ["Без карточки", n(pending), pending
      ? `Пока ищутся только по тексту. Карточки сделает ${runs === 1 ? "следующая синхронизация" : `ближайшие ${n(runs)} ${plural(runs, "синхронизация", "синхронизации", "синхронизаций")}`} (каждые 15 минут, до ${DATA.batch_limit} за раз)`
      : "у всех разговоров есть карточки"],
    ["Ошибки", n(failed + syncErrors.length), failed || syncErrors.length
      ? [failed && `${n(failed)} не удалось пересказать`, syncErrors.length && `${syncErrors.length} в источниках`].filter(Boolean).join(", ")
      : "ни одной"],
  ];
  $("#tiles").innerHTML = items.map(([label, value, note, bar]) => `
    <div class="card tile">
      <p class="label" style="margin:0">${esc(label)}</p>
      <p class="value">${esc(value)}</p>
      <p class="note">${esc(note)}</p>
      ${bar !== undefined ? `<div class="bar" role="progressbar" aria-valuenow="${bar}" aria-valuemin="0" aria-valuemax="100"><span style="width:${bar}%"></span></div>` : ""}
    </div>`).join("");
})();

/* 02 Источники */
(function sources() {
  $("#sources").innerHTML = SOURCES.map(([key, name]) => {
    const rs = recs.filter((r) => r.source === key);
    const cards = rs.filter((r) => r.card).length;
    const latest = rs.length ? date(rs[0].updated_at) : null;
    const s = DATA.sync[key];
    const seen = DATA.files_seen[key];
    const service = seen === undefined ? null : Math.max(0, seen - rs.length);
    let sync;
    if (!s) sync = `<span class="tag">не запускалась</span>`;
    else if (s.last_error) sync = `<span class="tag err">ошибка</span> <span class="muted">${esc(s.last_error)}</span>`;
    else sync = `<span class="tag ok">ок</span> <span class="muted">${esc(ago(date(s.last_success_at)))}</span>`;
    if (META[key].hint && !rs.length) {
      return `<tr><td>${esc(name)}</td><td colspan="6" class="muted">${esc(META[key].hint)}</td><td>${sync}</td></tr>`;
    }
    return `<tr>
      <td>${esc(name)}</td>
      <td class="n">${n(rs.length)}</td>
      <td class="n">${n(cards)}</td>
      <td class="n">${n(rs.length - cards)}</td>
      <td class="n">${n(DATA.waiting[key] || 0)}</td>
      <td class="n">${service === null ? "—" : n(service)}</td>
      <td class="muted">${latest ? esc(dayMonth(latest)) : "—"}</td>
      <td>${sync}</td>
    </tr>`;
  }).join("");
})();

/* 03 Активность: столбцы по неделям (понедельник — начало недели) */
function weekStart(d) {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
  return x;
}
const thisWeek = weekStart(new Date());
const weeks = Array.from({ length: WEEKS }, (_, i) => {
  const start = new Date(thisWeek);
  start.setDate(start.getDate() - (WEEKS - 1 - i) * 7);
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  return { start, end, count: 0 };
});
for (const r of recs) {
  const d = date(r.updated_at);
  if (!d) continue;
  const i = Math.round((weekStart(d) - weeks[0].start) / (7 * 864e5));
  if (i >= 0 && i < WEEKS) weeks[i].count++;
}
// Пустые недели до начала истории не показываем, но меньше 8 столбцов не делаем.
const firstActive = weeks.findIndex((w) => w.count > 0);
const shown = weeks.slice(firstActive < 0 ? WEEKS - 8 : Math.max(0, Math.min(firstActive - 1, WEEKS - 8)));
const inShown = shown.reduce((a, w) => a + w.count, 0);
$("#chart-sub").textContent = `С ${dayMonth(shown[0].start)} — ${n(inShown)} ${plural(inShown, "запись", "записи", "записей")}, по неделе последнего сообщения`;

function niceMax(v) {
  if (v <= 4) return 4;
  const pow = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * pow >= v) return m * pow;
  return 10 * pow;
}

function drawChart() {
  const box = $("#chart");
  const W = box.clientWidth, H = box.clientHeight;
  const pad = { top: 20, right: 0, bottom: 28, left: 36 };
  const pw = W - pad.left - pad.right, ph = H - pad.top - pad.bottom;
  const N = shown.length;
  const max = niceMax(Math.max(...shown.map((w) => w.count), 1));
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => Math.round(max * t));
  const slot = pw / N, bw = Math.max(2, Math.min(slot * 0.68, 48));
  const every = N > 16 ? 4 : N > 8 ? 2 : 1;
  const y = (v) => pad.top + ph - (v / max) * ph;
  const latest = shown[N - 1];
  const peak = shown.reduce((a, w) => (w.count > a.count ? w : a), shown[0]);
  let svg = `<svg viewBox="0 0 ${W} ${H}" aria-hidden="true">`;
  for (const t of [...new Set(ticks)]) {
    svg += `<line class="grid" x1="${pad.left}" x2="${W}" y1="${y(t) + 0.5}" y2="${y(t) + 0.5}"/>`;
    svg += `<text class="axis" x="${pad.left - 8}" y="${y(t) + 4}" text-anchor="end">${t}</text>`;
  }
  shown.forEach((w, i) => {
    const x = pad.left + i * slot + (slot - bw) / 2;
    const h = (w.count / max) * ph, top = pad.top + ph - h, r = Math.min(4, h, bw / 2);
    const mark = w.count
      ? `<path class="mark" d="M${x},${pad.top + ph} V${top + r} Q${x},${top} ${x + r},${top} H${x + bw - r} Q${x + bw},${top} ${x + bw},${top + r} V${pad.top + ph} Z"/>`
      : "";
    const label = w.count && (w === peak || w === latest)
      ? `<text class="value-label" x="${x + bw / 2}" y="${top - 6}" text-anchor="middle">${w.count}</text>` : "";
    svg += `<g class="slot" data-i="${i}">${mark}${label}<rect class="hit" x="${pad.left + i * slot}" y="${pad.top}" width="${slot}" height="${ph}"/></g>`;
    if ((N - 1 - i) % every === 0) {
      svg += `<text class="axis" x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${dayMonth(w.start)}</text>`;
    }
  });
  svg += `</svg>`;
  box.innerHTML = svg;
  box.setAttribute("aria-label", `Столбчатый график: записи по неделям, больше всего ${peak.count} за неделю с ${dayMonth(peak.start)}`);
}

const tip = $("#tip");
$("#chart").addEventListener("mousemove", (e) => {
  const g = e.target.closest(".slot");
  document.querySelectorAll(".slot.on").forEach((el) => el !== g && el.classList.remove("on"));
  if (!g) { tip.hidden = true; return; }
  g.classList.add("on");
  const w = shown[+g.dataset.i];
  tip.innerHTML = `${dayMonth(w.start)} — ${dayMonth(w.end)}<br><span class="num">${w.count}</span> ${plural(w.count, "запись", "записи", "записей")}`;
  tip.hidden = false;
  const tw = tip.offsetWidth;
  tip.style.left = Math.min(window.innerWidth - tw - 8, e.clientX + 14) + "px";
  tip.style.top = e.clientY - tip.offsetHeight - 12 + "px";
});
$("#chart").addEventListener("mouseleave", () => {
  tip.hidden = true;
  document.querySelectorAll(".slot.on").forEach((el) => el.classList.remove("on"));
});
drawChart();
let resizeTimer;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(drawChart, 100); });

/* 04 Проекты */
(function projects() {
  const groups = new Map();
  for (const r of recs) {
    const key = r.project || "";
    const g = groups.get(key) || { key, count: 0, cards: 0, latest: r.updated_at };
    g.count++;
    if (r.card) g.cards++;
    if (r.updated_at > g.latest) g.latest = r.updated_at;
    groups.set(key, g);
  }
  const top = [...groups.values()].sort((a, b) => b.count - a.count).slice(0, 15);
  const max = top.length ? top[0].count : 1;
  $("#projects").innerHTML = top.map((g) => `
    <tr class="clickable" tabindex="0" data-project="${esc(g.key)}">
      <td class="mono" title="${esc(g.key)}">${esc(shortPath(g.key) || "без проекта")}</td>
      <td class="n">${n(g.count)}</td>
      <td style="vertical-align:middle"><div class="inline-bar" style="width:${(g.count / max) * 100}%"></div></td>
      <td class="n">${n(g.cards)}</td>
      <td class="muted">${esc(dayMonth(date(g.latest)))}</td>
    </tr>`).join("");
  const pick = (el) => {
    filters.q = el.dataset.project ? shortPath(el.dataset.project).replace(/^…\//, "") : "";
    $("#q").value = filters.q;
    renderRecords();
    $("#records-section").scrollIntoView({ behavior: "smooth" });
  };
  $("#projects").addEventListener("click", (e) => { const tr = e.target.closest("tr"); if (tr) pick(tr); });
  $("#projects").addEventListener("keydown", (e) => { if (e.key === "Enter") { const tr = e.target.closest("tr"); if (tr) pick(tr); } });
})();

/* 05 Записи */
const filters = { q: "", source: "all", status: "all" };
function seg(el, key, options) {
  el.innerHTML = options.map(([v, label]) => `<button type="button" data-v="${v}" aria-pressed="${filters[key] === v}">${label}</button>`).join("");
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    filters[key] = b.dataset.v;
    el.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    renderRecords();
  });
}
seg($("#f-source"), "source", [["all", "Все"], ...SOURCES]);
seg($("#f-status"), "status", [["all", "Любые"], ["card", "С карточкой"], ["wait", "Без карточки"], ["err", "Ошибка"]]);
$("#q").addEventListener("input", (e) => { filters.q = e.target.value; renderRecords(); });

function statusTag(r) {
  if (r.card) return `<span class="tag ok">есть</span>`;
  if (r.error) return `<span class="tag err">ошибка</span>`;
  return `<span class="tag wait">ждёт</span>`;
}

function matches(r) {
  if (filters.source !== "all" && r.source !== filters.source) return false;
  if (filters.status === "card" && !r.card) return false;
  if (filters.status === "wait" && (r.card || r.error)) return false;
  if (filters.status === "err" && (r.card || !r.error)) return false;
  const q = filters.q.trim().toLowerCase();
  if (!q) return true;
  const hay = [r.title, r.project, r.keywords, r.summary].join(" ").toLowerCase();
  return q.split(/\s+/).every((w) => hay.includes(w));
}

function renderRecords() {
  const list = recs.filter(matches);
  $("#count").textContent = list.length === total
    ? `Все ${n(total)} ${plural(total, "запись", "записи", "записей")}, свежие сверху`
    : `Найдено ${n(list.length)} из ${n(total)}`;
  $("#records").innerHTML = list.length ? list.map((r) => `
    <tr class="clickable" tabindex="0" data-id="${r.id}">
      <td class="mono muted" style="white-space:nowrap">${esc(dayMonth(date(r.updated_at)))}</td>
      <td><span class="tag">${esc(SOURCE_NAME[r.source] || r.source)}</span></td>
      <td class="title"><div class="t">${esc(r.title)}</div>${r.summary ? `<div class="s">${esc(r.summary)}</div>` : ""}</td>
      <td class="mono muted" title="${esc(r.project)}">${esc(shortPath(r.project))}</td>
      <td>${statusTag(r)}</td>
    </tr>`).join("") : `<tr><td colspan="5" class="empty">Ничего не нашлось. Попробуй другое слово или сбрось фильтры.</td></tr>`;
}
renderRecords();

const byId = new Map(recs.map((r) => [r.id, r]));
const openFrom = (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) openRecord(byId.get(+tr.dataset.id)); };
$("#records").addEventListener("click", openFrom);
$("#records").addEventListener("keydown", (e) => { if (e.key === "Enter") openFrom(e); });

/* Карточка записи */
let lastFocus = null;
function openRecord(r) {
  closeRecord();
  lastFocus = document.activeElement;
  const started = date(r.started_at), updated = date(r.updated_at);
  const notice = r.card ? "" : r.error
    ? `<p class="notice err">Модель не смогла пересказать этот разговор: ${esc(r.error)}. Попробует ещё раз в одном из следующих прогонов.</p>`
    : `<p class="notice wait">Карточки пока нет: разговор в очереди на пересказ.</p>`;
  const atoms = r.atoms.map((a) => {
    const m = a.match(/^\[(\w+)\]\s*(.*)$/s);
    return m ? `<li><span class="tag">${esc(ATOM_NAME[m[1]] || m[1])}</span>${esc(m[2])}</li>` : `<li>${esc(a)}</li>`;
  }).join("");
  const scrim = document.createElement("div");
  scrim.className = "scrim";
  scrim.addEventListener("click", closeRecord);
  const d = document.createElement("aside");
  d.className = "drawer";
  d.setAttribute("role", "dialog");
  d.setAttribute("aria-modal", "true");
  d.setAttribute("aria-label", r.title);
  d.innerHTML = `
    <button class="close" type="button">Закрыть</button>
    <p class="label">${esc(SOURCE_NAME[r.source] || r.source)} · id ${r.id}</p>
    <h2>${esc(r.title)}</h2>
    <dl class="meta">
      <dt>Начат</dt><dd>${esc(stamp(started))}</dd>
      <dt>Последнее сообщение</dt><dd>${esc(stamp(updated))}</dd>
      <dt>Проект</dt><dd class="mono">${esc(r.project || "—")}</dd>
      <dt>Файл</dt><dd class="mono">${esc(r.location || "—")}</dd>
    </dl>
    ${notice}
    ${r.summary ? `<p class="label" style="margin-top:var(--space-6)">Кратко</p><p>${esc(r.summary)}</p>` : ""}
    ${atoms ? `<p class="label" style="margin-top:var(--space-6)">Атомы</p><ul>${atoms}</ul>` : ""}
    ${r.questions.length ? `<p class="label" style="margin-top:var(--space-6)">Как это будут искать</p><ul>${r.questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ul>` : ""}
    ${r.keywords ? `<p class="label" style="margin-top:var(--space-6)">Ключевые слова</p><p class="muted" style="font-size:14px">${esc(r.keywords)}</p>` : ""}
    <p class="label" style="margin-top:var(--space-6)">Расшифровка</p>
    <p class="muted" style="font-size:14px;margin-bottom:var(--space-3)">${r.body_len > DATA.preview_chars
      ? `Первые ${n(DATA.preview_chars)} из ${n(r.body_len)} символов. Целиком: <code>brain show ${r.id}</code> или «Последние карточки» в значке.`
      : `${n(r.body_len)} символов.`}</p>
    <pre class="transcript">${esc(r.preview)}</pre>`;
  d.querySelector(".close").addEventListener("click", closeRecord);
  document.body.append(scrim, d);
  d.querySelector(".close").focus();
}
function closeRecord() {
  document.querySelectorAll(".scrim, .drawer").forEach((el) => el.remove());
  if (lastFocus) { lastFocus.focus(); lastFocus = null; }
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeRecord(); });
