/* Граф записей и проектов: раскладка, отрисовка на canvas и управление.
   Узлы отталкиваются друг от друга, рёбра тянут как пружины, слабая сила держит всё у центра. По горизонтали
   она слабее, чтобы облако ложилось в широкую рамку, а не в круг посередине. До 600 узлов отталкивание
   считается попарно, больше — через квадродерево (Barnes-Hut).
   Готовая раскладка запоминается в браузере: при следующем открытии граф сразу стоит на своих местах,
   а новые записи встают рядом со своим проектом. Рисуем пачками, одна линия или заливка на цвет. */
function createGraph(canvas, opts) {
  const { nodes, links } = opts;
  const ctx = canvas.getContext("2d");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const TAU = Math.PI * 2, ALPHA_MIN = 0.004, STORE = "brain.graph.layout";
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const inc = new Map(nodes.map((n) => [n.id, []]));     // рёбра узла
  const kids = new Map();                                // записи проекта
  for (const l of links) {
    l.a = byId.get(l.s);
    l.b = byId.get(l.t);
    l.cross = l.kind === "topic" && l.a.pkey !== l.b.pkey;
    inc.get(l.s).push(l);
    inc.get(l.t).push(l);
    if (l.kind === "project") {
      l.a.hub = l.b;
      (kids.get(l.t) || kids.set(l.t, []).get(l.t)).push(l.a);
    }
  }
  const other = (l, id) => (l.s === id ? l.b : l.a);
  const prefs = { topic: true, minW: 0, areas: true, labels: "less", ...opts.prefs };

  // Цвета берём из CSS-переменных и перечитываем при смене темы.
  let theme = {};
  function readTheme() {
    const cs = getComputedStyle(canvas);
    const v = (name) => cs.getPropertyValue(name).trim();
    theme = {
      surface: v("--color-surface"), sunken: v("--color-surface-sunken"), line: v("--color-line"),
      lineStrong: v("--color-line-strong"), ink: v("--color-ink"), muted: v("--color-ink-muted"),
      subtle: v("--color-ink-subtle"), accent: v("--color-accent"), vars: {},
    };
    for (const n of nodes) if (n.colorVar && !theme.vars[n.colorVar]) theme.vars[n.colorVar] = v(n.colorVar) || theme.subtle;
  }
  readTheme();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readTheme(); kick(); });

  // ---- Начальные позиции: сохранённые, иначе проекты по золотой спирали, записи рядом со своим проектом.
  let seed = 42;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  let savedAspect = 0;
  function place(fresh) {
    let saved = {};
    if (!fresh) try { ({ aspect: savedAspect, pos: saved } = JSON.parse(localStorage.getItem(STORE)) || {}); } catch { /* нет раскладки */ }
    saved = saved || {};
    let known = 0;
    for (const n of nodes) {
      n.vx = 0; n.vy = 0; n.placed = false;
      n.charge = n.kind === "project" ? -90 - 6 * Math.min(40, n.count) : -24;
      const s = saved[n.id];
      if (Array.isArray(s)) { n.x = s[0]; n.y = s[1]; n.placed = true; known++; }
    }
    const hubs = nodes.filter((n) => n.kind === "project").sort((a, b) => b.count - a.count);
    hubs.forEach((p, i) => {
      if (p.placed) return;
      const ks = (kids.get(p.id) || []).filter((k) => k.placed);
      if (ks.length) {
        p.x = ks.reduce((s, k) => s + k.x, 0) / ks.length;
        p.y = ks.reduce((s, k) => s + k.y, 0) / ks.length;
      } else {
        const a = i * 2.39996, d = 80 * Math.sqrt(i + 0.5);
        p.x = Math.cos(a) * d * 1.6; p.y = Math.sin(a) * d;
      }
      p.placed = true;
    });
    for (const n of nodes) {
      if (n.placed) continue;
      const anchor = n.hub || inc.get(n.id).map((l) => other(l, n.id)).find((o) => o.placed);
      const a = rnd() * TAU, d = anchor ? 20 + rnd() * 30 : 120 + rnd() * 320;
      n.x = (anchor ? anchor.x : 0) + Math.cos(a) * d * (anchor ? 1 : 1.6);
      n.y = (anchor ? anchor.y : 0) + Math.sin(a) * d;
      n.placed = true;
    }
    return nodes.length ? known / nodes.length : 0;
  }
  function saveLayout() {
    const pos = {};
    for (const n of nodes) pos[n.id] = [Math.round(n.x), Math.round(n.y)];
    try { localStorage.setItem(STORE, JSON.stringify({ aspect, pos })); } catch { /* приватный режим: просто не запомним */ }
  }
  let known = place(false), alpha = 1, dirty = true, aspect = 1.6;

  // ---- Физика. Считается для активного набора: обычно это весь граф, в режиме окрестности — только она.
  const THETA2 = 0.81, DMAX2 = 360 * 360, G = 0.008;
  let simNodes = nodes, simLinks = links, center = [0, 0], snap = null;   // snap — общая раскладка, пока видна окрестность
  function tick() {
    const ns = simNodes;
    if (ns.length <= 600) {
      for (let i = 0; i < ns.length; i++) {
        const a = ns[i];
        for (let j = i + 1; j < ns.length; j++) {
          const b = ns[j];
          let dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy;
          if (d2 > DMAX2) continue;
          if (d2 < 1) { dx = rnd() - 0.5; dy = rnd() - 0.5; d2 = 1; }
          const f = alpha / d2;
          a.vx += dx * b.charge * f; a.vy += dy * b.charge * f;
          b.vx -= dx * a.charge * f; b.vy -= dy * a.charge * f;
          const min = a.r + b.r + 3;             // не даём кругам налезать
          if (d2 < min * min) {
            const d = Math.sqrt(d2), push = (min - d) / d * 0.5;
            a.vx -= dx * push; a.vy -= dy * push; b.vx += dx * push; b.vy += dy * push;
          }
        }
      }
    } else {
      const root = quadtree(ns);
      for (const n of ns) repel(root, n);
    }
    for (const l of simLinks) {
      const { a, b } = l;
      let dx = b.x + b.vx - a.x - a.vx, dy = b.y + b.vy - a.y - a.vy;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      // Проектные пружины короткие и тугие — держат кучку. Темы внутри проекта стягивают похожие записи,
      // темы между проектами длинные и слабые: показывают мостик, но не сминают проекты в один ком.
      // В окрестности связи по темам и есть смысл картинки, поэтому там они короче и туже.
      let target, k;
      if (l.kind === "project") { target = 14 + a.r + b.r; k = 0.7; }
      else if (snap) { target = 60; k = 0.04 + 0.12 * l.w; }
      else if (l.cross) { target = 140; k = 0.006 + 0.04 * l.w; }
      else { target = 30; k = 0.04 + 0.1 * l.w; }
      k *= alpha * (d - target) / d;
      dx *= k; dy *= k;
      b.vx -= dx * 0.5; b.vy -= dy * 0.5; a.vx += dx * 0.5; a.vy += dy * 0.5;
    }
    // Притяжение к центру эллиптическое: облако вытягивается под пропорции рамки.
    const g = snap ? 3 * G : G, gx = g * alpha / (aspect * aspect), gy = g * alpha * aspect;
    for (const n of ns) {
      n.vx -= (n.x - center[0]) * gx; n.vy -= (n.y - center[1]) * gy;
      if (n.fx != null) { n.x = n.fx; n.y = n.fy; n.vx = n.vy = 0; continue; }
      n.vx *= 0.6; n.vy *= 0.6;
      n.x += n.vx; n.y += n.vy;
    }
    alpha += (0 - alpha) * 0.0228;
  }

  function quadtree(ns) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of ns) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); }
    const root = cell(x0, y0, Math.max(x1 - x0, y1 - y0) + 1);
    for (const n of ns) insert(root, n, 0);
    sum(root);
    return root;
  }
  function cell(x, y, s) { return { x, y, s, q: 0, cx: 0, cy: 0, items: null, kids: null }; }
  function insert(c, n, depth) {
    if (!c.kids) {
      if (!c.items || depth > 24) { (c.items ||= []).push(n); return; }
      c.kids = [null, null, null, null];
      const old = c.items; c.items = null;
      for (const o of old) put(c, o, depth);
    }
    put(c, n, depth);
  }
  function put(c, n, depth) {
    const h = c.s / 2, i = (n.x >= c.x + h ? 1 : 0) + (n.y >= c.y + h ? 2 : 0);
    if (!c.kids[i]) c.kids[i] = cell(c.x + (i & 1 ? h : 0), c.y + (i & 2 ? h : 0), h);
    insert(c.kids[i], n, depth + 1);
  }
  function sum(c) {
    let q = 0, sx = 0, sy = 0;
    if (c.items) for (const n of c.items) { q += n.charge; sx += n.x * n.charge; sy += n.y * n.charge; }
    if (c.kids) for (const k of c.kids) if (k) { sum(k); q += k.q; sx += k.cx * k.q; sy += k.cy * k.q; }
    c.q = q; c.cx = q ? sx / q : c.x; c.cy = q ? sy / q : c.y;
  }
  function repel(c, n) {
    if (!c || !c.q) return;
    const dx = c.cx - n.x, dy = c.cy - n.y, d2 = Math.max(1, dx * dx + dy * dy);
    if (c.kids && (c.s * c.s) / d2 < THETA2) { n.vx += dx * c.q * alpha / d2; n.vy += dy * c.q * alpha / d2; return; }
    if (c.kids) { for (const k of c.kids) repel(k, n); return; }
    for (const o of c.items) {
      if (o === n) continue;
      const ox = o.x - n.x, oy = o.y - n.y, o2 = Math.max(1, ox * ox + oy * oy);
      if (o2 > DMAX2) continue;
      n.vx += ox * o.charge * alpha / o2; n.vy += oy * o.charge * alpha / o2;
      const min = o.r + n.r + 3;
      if (o2 < min * min) { const d = Math.sqrt(o2), push = (min - d) / d * 0.5; n.vx -= ox * push; n.vy -= oy * push; }
    }
  }

  // ---- Видимость, выбор, подсветка
  const view = { x: 0, y: 0, k: 1 };
  let W = 0, H = 0, raf = 0, lastFrame = 0;
  let auto = true, follow = null, cam = null;    // auto: вид вписывается сам, пока ты его не двигал
  let filter = () => true, local = null, marked = null, pinned = null, hover = null;
  let fset = null, fid = null, famt = 0, ftarget = 0;   // подсвеченная окрестность, её центр и сила подсветки 0…1
  const visible = (n) => filter(n) && (!local || local.has(n.id));
  const linkOn = (l) => l.kind === "project" || (prefs.topic && l.w >= prefs.minW);
  const shown = (l) => linkOn(l) && visible(l.a) && visible(l.b);

  function focusOf(id) {
    if (id == null || !byId.has(id)) return null;
    const set = new Set([id]);
    for (const l of inc.get(id)) if (shown(l)) set.add(other(l, id).id);
    // У проекта светятся и проекты, с которыми его записи делят темы: видно, с чем он связан.
    if (byId.get(id).kind === "project") {
      for (const k of kids.get(id) || []) {
        if (!visible(k)) continue;
        for (const l of inc.get(k.id)) if (l.kind === "topic" && shown(l)) { const o = other(l, k.id); set.add((o.hub || o).id); }
      }
    }
    return set;
  }
  function refocus() {
    const s = focusOf(hover ? hover.id : pinned);
    if (s) { fset = s; fid = hover ? hover.id : pinned; ftarget = 1; } else ftarget = 0;
    if (reduce) { famt = ftarget; if (!s) fset = null; }
    kick();
  }
  function stepFade(dt) {
    if (famt === ftarget) return false;
    famt = ftarget > famt ? Math.min(1, famt + dt / 140) : Math.max(0, famt - dt / 200);
    if (famt === 0) fset = null;
    return famt !== ftarget;
  }
  // Окрестность для режима «только связанные»: запись, её похожие и их похожие; для проекта — его записи
  // и их похожие. Плюс проекты всех попавших записей, чтобы было видно, откуда они.
  function around(id) {
    const n = byId.get(id), set = new Set([id]);
    const topicN = (x) => inc.get(x.id).filter((l) => l.kind === "topic" && linkOn(l)).map((l) => other(l, x.id));
    let ring = n.kind === "project" ? kids.get(id) || [] : [n];
    ring.forEach((x) => set.add(x.id));
    for (let depth = n.kind === "project" ? 1 : 2; depth > 0; depth--) {
      ring = ring.flatMap(topicN).filter((x) => !set.has(x.id));
      ring.forEach((x) => set.add(x.id));
    }
    for (const x of [...set]) { const h = byId.get(x).hub; if (h) set.add(h.id); }
    return set;
  }

  // ---- Вид и камера
  // Радиус в мире, но не меньше 2.5px на экране, иначе при обзоре всей базы точки пропадают.
  const worldR = (nd) => Math.max(nd.r, 2.5 / view.k);
  const screenR = (nd) => worldR(nd) * view.k;
  const clampK = (k) => Math.max(0.1, Math.min(10, k));
  function free() {
    const i = (opts.inset && opts.inset()) || {};
    return { x0: i.left || 0, y0: i.top || 0, x1: W - (i.right || 0), y1: H - (i.bottom || 0) };
  }
  function fitView(maxK = 4) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of nodes) {
      if (!visible(n)) continue;
      x0 = Math.min(x0, n.x - n.r); y0 = Math.min(y0, n.y - n.r); x1 = Math.max(x1, n.x + n.r); y1 = Math.max(y1, n.y + n.r);
    }
    if (x0 === Infinity || !W) return null;
    const f = free(), padX = 40, padTop = 64, padBottom = 44;
    const w = Math.max(40, f.x1 - f.x0 - padX * 2), h = Math.max(40, f.y1 - f.y0 - padTop - padBottom);
    const k = Math.max(0.1, Math.min(maxK, w / Math.max(1, x1 - x0), h / Math.max(1, y1 - y0)));
    return { k, x: f.x0 + padX + w / 2 - ((x0 + x1) / 2) * k, y: f.y0 + padTop + h / 2 - ((y0 + y1) / 2) * k };
  }
  function freeCenter() { const f = free(); return [(f.x0 + f.x1) / 2, (f.y0 + f.y1) / 2]; }
  // Полёт камеры: центр по прямой, масштаб по логарифму — так приближение выглядит равномерным.
  function flyTo(to, node, then) {
    if (!to) return;
    auto = false; follow = null;
    const [mx, my] = freeCenter();
    const at = (v) => ({ cx: (mx - v.x) / v.k, cy: (my - v.y) / v.k, k: v.k });
    const b = node ? { cx: node.x, cy: node.y, k: to.k } : at(to);
    if (reduce || !W) {
      view.k = b.k; view.x = mx - b.cx * b.k; view.y = my - b.cy * b.k;
      if (node) follow = node;
      if (then) then();
      kick();
      return;
    }
    cam = { t0: performance.now(), dur: 480, a: at(view), b, node, mx, my, then };
    kick();
  }
  function stepCam(now) {
    if (!cam) return false;
    const t = Math.min(1, (now - cam.t0) / cam.dur), e = 1 - (1 - t) ** 3;
    const bx = cam.node ? cam.node.x : cam.b.cx, by = cam.node ? cam.node.y : cam.b.cy;
    const k = cam.a.k * (cam.b.k / cam.a.k) ** e;
    view.k = k;
    view.x = cam.mx - (cam.a.cx + (bx - cam.a.cx) * e) * k;
    view.y = cam.my - (cam.a.cy + (by - cam.a.cy) * e) * k;
    if (t < 1) return true;
    const { node, then } = cam;
    cam = null;
    if (node) follow = node;       // пока раскладка не успокоилась, держим узел в центре
    if (then) then();
    return false;
  }
  function stopCamera() { auto = false; follow = null; cam = null; }
  function zoomAt(f, sx, sy) {
    const k = clampK(view.k * f);
    view.x = sx - (sx - view.x) * (k / view.k);
    view.y = sy - (sy - view.y) * (k / view.k);
    view.k = k;
    stopCamera();
    kick();
  }
  function pan(dx, dy) { view.x += dx; view.y += dy; stopCamera(); kick(); }
  // Вписать: камера плавно подтягивается к рамке всего видимого и держит её, пока раскладка движется.
  function fit() { follow = null; cam = null; auto = true; kick(); }
  const fitK = () => (local ? 2.5 : 4);
  // Шаг плавного вписывания; true — пока не доехали.
  function easeFit(snapNow) {
    const to = fitView(fitK());
    if (!to) return false;
    const [mx, my] = freeCenter();
    const ax = (mx - view.x) / view.k, ay = (my - view.y) / view.k, bx = (mx - to.x) / to.k, by = (my - to.y) / to.k;
    const s = snapNow || reduce ? 1 : 0.2;
    const k = view.k * (to.k / view.k) ** s, cx = ax + (bx - ax) * s, cy = ay + (by - ay) * s;
    view.k = k; view.x = mx - cx * k; view.y = my - cy * k;
    return Math.abs(to.k / k - 1) > 0.002 || Math.abs(to.x - view.x) > 0.5 || Math.abs(to.y - view.y) > 0.5;
  }

  // ---- Окрестность: раскладывается заново вокруг выбранного, общая раскладка ждёт в snap.
  function enterLocal(id) {
    const prev = local;
    if (!snap) snap = new Map(nodes.map((n) => [n.id, [n.x, n.y]]));
    local = around(id);
    const p = byId.get(id), [gx, gy] = snap.get(id), cx = p.x, cy = p.y;
    for (const n of nodes) {
      const [sx, sy] = snap.get(n.id);
      n.vx = n.vy = 0;
      if (!local.has(n.id)) { n.x = sx; n.y = sy; continue; }
      if (prev && prev.has(n.id)) continue;            // уже стоит в окрестности — не дёргаем
      // Сжимаем общую раскладку к выбранному: направления сохраняются, расстояния в пять раз короче.
      n.x = cx + (sx - gx) * 0.2; n.y = cy + (sy - gy) * 0.2;
    }
    simNodes = nodes.filter((n) => local.has(n.id));
    simLinks = links.filter((l) => local.has(l.s) && local.has(l.t));
    center = [cx, cy];
    alpha = 1;
    fit();
  }
  function leaveLocal() {
    if (!snap) return;
    for (const n of nodes) { const [x, y] = snap.get(n.id); n.x = x; n.y = y; n.vx = n.vy = 0; }
    snap = null; local = null;
    simNodes = nodes; simLinks = links; center = [0, 0];
    alpha = dirty ? 0.1 : 0;                 // общая раскладка не успела успокоиться — пусть досчитается
    fit();
  }

  // ---- Отрисовка
  const widths = new Map();
  function measure(font, text) {
    const key = font + "\n" + text;
    let w = widths.get(key);
    if (w === undefined) { ctx.font = font; w = ctx.measureText(text).width; widths.set(key, w); }
    return w;
  }
  if (document.fonts) document.fonts.ready.then(() => { widths.clear(); kick(); });
  const FONT_REC = "400 12px 'Golos Text', system-ui, sans-serif";
  const projFont = (n) => `500 ${n.count >= 30 ? 15 : n.count >= 8 ? 13 : 12}px 'Golos Text', system-ui, sans-serif`;

  function strokeAll(list, color, width, a) {
    if (!list.length || a <= 0.01) return;
    ctx.globalAlpha = a; ctx.strokeStyle = color; ctx.lineWidth = width;
    ctx.beginPath();
    for (const l of list) { ctx.moveTo(l.a.x, l.a.y); ctx.lineTo(l.b.x, l.b.y); }
    ctx.stroke();
  }
  function fillCircles(list, color, a) {
    ctx.globalAlpha = a; ctx.fillStyle = color;
    ctx.beginPath();
    for (const [x, y, r] of list) { ctx.moveTo(x + r, y); ctx.arc(x, y, r, 0, TAU); }
    ctx.fill();
  }

  function draw() {
    if (!W) return;
    const dpr = devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    ctx.translate(view.x, view.y);
    ctx.scale(view.k, view.k);
    // В окрестности второй круг связей приглушаем мягче: он и есть то, что ты пришёл посмотреть.
    const px = 1 / view.k, f = fset && famt > 0 ? fset : null, dimA = 1 - (local ? 0.55 : 0.86) * famt;
    const lit = (n) => !f || f.has(n.id);
    const alphaOf = (n) => {
      let a = lit(n) ? 1 : dimA;
      if (marked && n.kind === "record" && !marked.has(n.id)) a = Math.min(a, 0.16);
      return a;
    };

    // Области проектов: плоская подложка под кучкой записей, чтобы проекты читались как острова.
    if (prefs.areas) {
      const on = [], off = [];
      for (const h of nodes) {
        if (h.kind !== "project" || !visible(h)) continue;
        // Радиус по 90-й доле расстояний: одна отбившаяся запись не раздувает остров.
        const ds = [];
        for (const k of kids.get(h.id) || []) if (visible(k)) ds.push(Math.hypot(k.x - h.x, k.y - h.y) + worldR(k));
        if (ds.length < 3) continue;
        ds.sort((a, b) => a - b);
        (lit(h) ? on : off).push([h.x, h.y, ds[Math.floor((ds.length - 1) * 0.9)] + 10]);
      }
      fillCircles(off, theme.sunken, f ? dimA : 1);
      fillCircles(on, theme.sunken, 1);
    }

    // Рёбра пачками: связи с проектом, темы по силе в три ступени, подсвеченные — отдельно поверх.
    // При обзоре темы между проектами сводятся в мосты: одна линия на пару проектов, толщина — число связей.
    // С приближением мосты плавно расходятся обратно на отдельные линии между записями. Считаем приближение
    // от вписанного вида: в узкой рамке весь граф помещается при масштабе 0,3, в широкой — при 0,8.
    const rel = view.k / ((fitView() || view).k || 1);
    const agg = local ? 0 : Math.max(0, Math.min(1, (2.2 - rel) / 0.6));
    // При приближении линия, уходящая за край экрана, рисуется как слабая, а та, у которой оба конца
    // за краем, почти гаснет: иначе вид перечёркнут линиями к записям, которых не видно.
    const vx0 = -view.x * px, vy0 = -view.y * px, vx1 = (W - view.x) * px, vy1 = (H - view.y) * px;
    const inView = (n) => n.x > vx0 && n.x < vx1 && n.y > vy0 && n.y < vy1;
    const spokes = [], weak = [], mid = [], strong = [], far = [], hot = [], hotSpokes = [], near = [], bridges = new Map();
    for (const l of links) {
      if (!shown(l)) continue;
      // Акцентом — только линии из самого выбранного узла; линии между его соседями остаются обычными.
      if (f && (l.s === fid || l.t === fid)) { (l.kind === "project" ? hotSpokes : hot).push(l); continue; }
      if (f && f.has(l.a.id) && f.has(l.b.id)) { near.push(l); continue; }
      if (l.kind === "project") { spokes.push(l); continue; }
      if (agg > 0 && l.cross) {
        const a = l.a.hub || l.a, b = l.b.hub || l.b, key = String(a.id) < String(b.id) ? a.id + "|" + b.id : b.id + "|" + a.id;
        (bridges.get(key) || bridges.set(key, { a, b, c: 0 }).get(key)).c++;
      }
      const seen = (inView(l.a) ? 1 : 0) + (inView(l.b) ? 1 : 0);
      if (seen === 0) far.push(l);
      else (seen === 1 || l.w < 0.2 ? weak : l.w < 0.35 ? mid : strong).push(l);
    }
    const fade = (f ? dimA : 1) * (marked ? 0.45 : 1), solo = fade * (1 - agg), joined = fade * agg;
    strokeAll(spokes, theme.line, px, fade);
    strokeAll(weak, theme.lineStrong, px, 0.07 * solo);
    strokeAll(mid, theme.lineStrong, px, 0.15 * solo);
    strokeAll(strong, theme.lineStrong, 1.2 * px, 0.3 * solo);
    strokeAll(far, theme.lineStrong, px, 0.035 * solo);
    const steps = [[], [], [], []], hotSteps = [[], [], [], []], WIDTH = [1, 1.5, 2.25, 3];
    for (const br of bridges.values()) {
      const i = br.c >= 9 ? 3 : br.c >= 4 ? 2 : br.c >= 2 ? 1 : 0;
      (f && (br.a.id === fid || br.b.id === fid) ? hotSteps : steps)[i].push(br);
    }
    [0.09, 0.22, 0.3, 0.4].forEach((a, i) => strokeAll(steps[i], theme.lineStrong, WIDTH[i] * px, a * joined));
    hotSteps.forEach((list, i) => strokeAll(list, theme.accent, WIDTH[i] * px, (0.25 + 0.6 * famt) * agg));
    // Связь с проектом подсвечиваем тише связей по темам: у большого проекта их десятки.
    strokeAll(near, theme.lineStrong, px, 0.3);
    strokeAll(hotSpokes, theme.accent, px, 0.1 + 0.3 * famt);
    strokeAll(hot, theme.accent, 1.5 * px, 0.2 + 0.7 * famt);

    // Записи: одна заливка на цвет источника и яркость; тусклые снизу, яркие сверху.
    const groups = new Map();
    for (const n of nodes) {
      if (n.kind !== "record" || !visible(n)) continue;
      const a = Math.round(alphaOf(n) * 20) / 20, key = n.colorVar + "|" + a;
      (groups.get(key) || groups.set(key, { color: theme.vars[n.colorVar], a, list: [] }).get(key)).list.push([n.x, n.y, worldR(n)]);
    }
    ctx.lineWidth = 1.5 * px; ctx.strokeStyle = theme.surface;
    for (const g of [...groups.values()].sort((x, y) => x.a - y.a)) {
      fillCircles(g.list, g.color, g.a);
      ctx.stroke();
    }
    // Проекты: кольцо с точкой в центре.
    for (const n of nodes) {
      if (n.kind !== "project" || !visible(n)) continue;
      const r = worldR(n);
      ctx.globalAlpha = alphaOf(n);
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, TAU);
      ctx.fillStyle = theme.surface; ctx.fill();
      ctx.lineWidth = (n.count >= 10 ? 2 : 1.5) * px; ctx.strokeStyle = theme.muted; ctx.stroke();
      ctx.beginPath(); ctx.arc(n.x, n.y, Math.max(1.2 * px, r * 0.3), 0, TAU);
      ctx.fillStyle = theme.muted; ctx.fill();
    }
    // Выбранное и найденное — акцентным кольцом.
    ctx.globalAlpha = 1; ctx.strokeStyle = theme.accent; ctx.lineWidth = 2 * px;
    ctx.beginPath();
    for (const n of nodes) {
      if (!visible(n) || !(n.id === pinned || (marked && marked.has(n.id)))) continue;
      const r = worldR(n) + 3.5 * px;
      ctx.moveTo(n.x + r, n.y); ctx.arc(n.x, n.y, r, 0, TAU);
    }
    ctx.stroke();
    ctx.restore();
    drawLabels(f, rel);
  }

  // Подписи — в экранных координатах, без наложений: сначала наведённое и выбранное, потом крупные проекты.
  function drawLabels(f, rel) {
    const more = prefs.labels === "more", strongFocus = f && famt > 0.5;
    const cand = [];
    for (const nd of nodes) {
      if (!visible(nd)) continue;
      const lit = strongFocus && f.has(nd.id);
      if (strongFocus && !lit) continue;
      const proj = nd.kind === "project";
      const special = nd.id === pinned || (hover && hover.id === nd.id);
      const show = special || (proj
        ? more || nd.count >= 5 || rel >= 1.8 || lit
        : view.k >= (more ? 1.2 : 2) || rel >= (more ? 3 : 5) || (lit && f.size <= 24) || (marked && marked.has(nd.id) && marked.size <= 24));
      if (!show) continue;
      cand.push([special ? 1e6 : proj ? 1000 + nd.count : lit ? 500 : nd.r, nd]);
    }
    cand.sort((a, b) => b[0] - a[0]);
    const placed = [];
    ctx.textAlign = "center"; ctx.textBaseline = "bottom"; ctx.lineJoin = "round";
    for (const [prio, nd] of cand) {
      const proj = nd.kind === "project";
      const font = proj ? projFont(nd) : FONT_REC;
      const max = proj ? 30 : 42, text = nd.label.length > max ? nd.label.slice(0, max - 1) + "…" : nd.label;
      const w = measure(font, text), size = proj ? parseInt(font.slice(4), 10) : 12;
      const sx = nd.x * view.k + view.x, sy = nd.y * view.k + view.y - screenR(nd) - 5;
      const box = [sx - w / 2 - 3, sy - size - 3, sx + w / 2 + 3, sy + 1];
      if (box[2] < 0 || box[0] > W || box[3] < 0 || box[1] > H) continue;
      if (prio < 1e6 && placed.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) continue;
      placed.push(box);
      ctx.font = font;
      ctx.globalAlpha = 0.92; ctx.lineWidth = 4; ctx.strokeStyle = theme.surface; ctx.strokeText(text, sx, sy);
      ctx.globalAlpha = 1;
      ctx.fillStyle = proj ? (nd.count >= 5 || prio >= 1e6 ? theme.ink : theme.muted) : prio >= 500 ? theme.ink : theme.muted;
      ctx.fillText(text, sx, sy);
    }
  }

  // ---- Цикл кадров: физика в пределах 8 мс на кадр, камера, затухание подсветки, отрисовка.
  function kick() { if (!raf && W) raf = requestAnimationFrame(loop); }
  function heat(a) { alpha = Math.max(alpha, a); dirty = true; kick(); }
  function loop(now) {
    raf = 0;
    const dt = lastFrame ? Math.min(64, now - lastFrame) : 16;
    lastFrame = now;
    if (alpha > ALPHA_MIN) {
      // Окрестность маленькая и считается мгновенно, поэтому там не больше 4 шагов за кадр: видно, как она раскрывается.
      const t0 = performance.now(), cap = snap && !reduce ? 4 : Infinity;
      let i = 0;
      do tick(); while (alpha > ALPHA_MIN && ++i < cap && performance.now() - t0 < 8);
    }
    if (alpha <= ALPHA_MIN && dirty && !snap) { dirty = false; saveLayout(); }
    const flying = stepCam(now), fading = stepFade(dt);
    let easing = false;
    if (!cam) {
      if (follow) { const [mx, my] = freeCenter(); view.x = mx - follow.x * view.k; view.y = my - follow.y * view.k; }
      else if (auto) easing = easeFit(false);
    }
    draw();
    if (alpha > ALPHA_MIN || flying || fading || easing) raf = requestAnimationFrame(loop);
    else lastFrame = 0;
  }

  // ---- Указатель: наведение, клик, двойной клик, перетаскивание, щипок, колесо
  const toWorld = (sx, sy) => [(sx - view.x) / view.k, (sy - view.y) / view.k];
  function hit(sx, sy) {
    const [x, y] = toWorld(sx, sy);
    let best = null, bd = Infinity;
    for (const n of nodes) {
      if (!visible(n)) continue;
      const d = Math.hypot(n.x - x, n.y - y);
      if (d < worldR(n) + 5 / view.k && d < bd) { best = n; bd = d; }
    }
    return best;
  }
  const pointers = new Map();
  let drag = null, pinch = null, lastTap = { id: null, t: 0 };
  const localXY = (e) => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  function setHover(n) {
    if (n === hover) return;
    hover = n;
    canvas.classList.toggle("pointer", !!n);
    refocus();
  }

  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId);
    const [sx, sy] = localXY(e);
    pointers.set(e.pointerId, [sx, sy]);
    if (pointers.size === 2) {
      const [[ax, ay], [bx, by]] = [...pointers.values()];
      pinch = { d: Math.hypot(ax - bx, ay - by) };
      if (drag && drag.node) drag.node.fx = drag.node.fy = null;
      drag = null;
      return;
    }
    const node = hit(sx, sy);
    drag = { node, sx, sy, vx: view.x, vy: view.y, moved: false };
    canvas.classList.add("dragging");
  });
  canvas.addEventListener("pointermove", (e) => {
    const [sx, sy] = localXY(e);
    if (pointers.has(e.pointerId)) pointers.set(e.pointerId, [sx, sy]);
    if (pinch && pointers.size === 2) {
      const [[ax, ay], [bx, by]] = [...pointers.values()];
      const d = Math.hypot(ax - bx, ay - by);
      zoomAt(d / pinch.d, (ax + bx) / 2, (ay + by) / 2);
      pinch.d = d;
      return;
    }
    if (drag) {
      if (!drag.moved && Math.hypot(sx - drag.sx, sy - drag.sy) <= 4) return;
      drag.moved = true;
      opts.onHover(null);
      if (drag.node) {
        const [x, y] = toWorld(sx, sy);
        drag.node.fx = x; drag.node.fy = y;
        stopCamera();                     // иначе вид уезжал бы из-под пальца
        heat(0.12);
      } else {
        view.x = drag.vx + sx - drag.sx; view.y = drag.vy + sy - drag.sy;
        stopCamera();
        kick();
      }
      return;
    }
    const n = hit(sx, sy);
    setHover(n);
    opts.onHover(n, e.clientX, e.clientY);
  });
  function release(e) {
    pointers.delete(e.pointerId);
    if (pointers.size < 2) pinch = null;
    canvas.classList.remove("dragging");
    if (!drag) return;
    const { node, moved } = drag;
    drag = null;
    if (node) node.fx = node.fy = null;
    if (moved || e.type !== "pointerup") return;
    // Клик выбирает, второй клик по тому же узлу открывает его. Клик по пустому месту снимает выбор.
    const now = performance.now();
    if (node && lastTap.id === node.id && now - lastTap.t < 450) {
      lastTap = { id: null, t: 0 };
      opts.onOpen(node);
    } else if (node) {
      lastTap = { id: node.id, t: now };
      select(node.id);
    } else if (!local) select(null);
  }
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);
  canvas.addEventListener("pointerleave", () => { if (!drag) setHover(null); opts.onHover(null); });
  // Тачпад: два пальца двигают, щипок меняет масштаб. Колесо мыши — масштаб.
  // Колесо отличаем по wheelDeltaY: у тачпада он ровно −3 × deltaY, у колеса — нет.
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const [sx, sy] = localXY(e);
    const trackpad = e.wheelDeltaY !== undefined ? e.wheelDeltaY === -3 * e.deltaY || (e.deltaY === 0 && e.deltaX !== 0) : e.deltaX !== 0;
    if (e.ctrlKey || e.metaKey || !trackpad) {
      const unit = e.deltaMode === 1 ? 16 : 1;
      zoomAt(Math.exp(-e.deltaY * unit * (e.ctrlKey ? 0.01 : 0.0015)), sx, sy);
    } else pan(-e.deltaX, -e.deltaY);
  }, { passive: false });
  // Safari присылает щипок тачпада жестами, а не колесом.
  let gscale = 1;
  canvas.addEventListener("gesturestart", (e) => { e.preventDefault(); gscale = 1; });
  canvas.addEventListener("gesturechange", (e) => {
    e.preventDefault();
    const [sx, sy] = localXY(e);
    zoomAt(e.scale / gscale, sx, sy);
    gscale = e.scale;
  });

  // ---- Выбор и окрестность
  function select(id, how = {}) {
    const n = id == null ? null : byId.get(id) || null;
    if (n && !filter(n)) return false;
    const same = n && n.id === pinned;
    pinned = n ? n.id : null;
    if (!n) leaveLocal();
    else if (local && !same) enterLocal(n.id);   // в режиме окрестности она следует за выбором
    refocus();
    if (opts.onSelect) opts.onSelect(n);
    if (!n || local) return true;
    // how.zoom — во сколько раз ближе вписанного вида, чтобы в узкой и широкой рамке приближение было одинаковым.
    if (how.fly) flyTo({ k: Math.max(view.k, Math.min(4, (fitView() || view).k * (how.zoom || 1))) }, n);
    else {
      // Узел ушёл под панель или за край — подвозим его к центру, не меняя масштаб.
      const f = free(), sx = n.x * view.k + view.x, sy = n.y * view.k + view.y, m = 32;
      if (sx < f.x0 + m || sx > f.x1 - m || sy < f.y0 + m || sy > f.y1 - m) flyTo({ k: view.k }, n);
    }
    return true;
  }

  function resize() {
    const r = canvas.getBoundingClientRect(), dpr = devicePixelRatio || 1;
    if (!r.width) return;
    const first = !W;
    W = r.width; H = r.height;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    if (first) {
      aspect = Math.max(0.7, Math.min(2.4, W / H));
      // Почти всё на сохранённых местах — только даём новым записям встать, без разлёта всего графа.
      // Рамка с тех пор заметно сменила пропорции — даём раскладке растянуться под новую.
      if (known > 0.8) alpha = Math.abs(Math.log(aspect / (savedAspect || aspect))) > 0.25 ? 0.3 : 0.08;
      // Первые шаги считаем сразу, чтобы не показывать разлёт из начальных позиций.
      const t0 = performance.now();
      if (reduce) while (alpha > ALPHA_MIN) tick();
      else while (alpha > 0.3 && performance.now() - t0 < 180) tick();
    }
    if (auto && !cam) easeFit(first);
    draw();
    kick();
  }

  return {
    resize,
    fit,
    zoom(f) {
      const [mx, my] = freeCenter(), k = clampK(view.k * f);
      flyTo({ k, x: mx - ((mx - view.x) / view.k) * k, y: my - ((my - view.y) / view.k) * k });
    },
    pan,
    setFilter(fn, refit) {
      filter = fn;
      if (pinned != null && !filter(byId.get(pinned))) select(null);
      if (hover && !visible(hover)) setHover(null);
      if (refit) fit(); else kick();
    },
    setPrefs(p) {
      const relink = local && pinned != null && (p.topic !== prefs.topic || p.minW !== prefs.minW);
      Object.assign(prefs, p);
      if (relink) enterLocal(pinned);
      refocus();
    },
    highlight(ids) { marked = ids && ids.size ? ids : null; kick(); },
    select,
    selected: () => (pinned == null ? null : byId.get(pinned)),
    has: (id) => byId.has(id),
    passes: (id) => byId.has(id) && filter(byId.get(id)),
    setLocal(on) {
      if (on && pinned != null) enterLocal(pinned); else leaveLocal();
      refocus();
      return !!local;
    },
    isLocal: () => !!local,
    relayout() {
      if (local) { leaveLocal(); refocus(); if (opts.onSelect) opts.onSelect(pinned == null ? null : byId.get(pinned)); }
      seed = 42;
      known = place(true);
      alpha = 1; dirty = true;
      fit();
    },
  };
}
