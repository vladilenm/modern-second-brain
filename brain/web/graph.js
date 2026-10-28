/* Граф записей и проектов: физика и отрисовка на canvas.
   Узлы отталкиваются друг от друга, рёбра тянут как пружины, слабая сила держит всё у центра.
   До 600 узлов отталкивание считается попарно, больше — через квадродерево (Barnes-Hut). */
function createGraph(canvas, opts) {
  const { nodes, links } = opts;
  const ctx = canvas.getContext("2d");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const adj = new Map(nodes.map((n) => [n.id, new Set()]));
  for (const l of links) {
    l.a = byId.get(l.s);
    l.b = byId.get(l.t);
    adj.get(l.s).add(l.t);
    adj.get(l.t).add(l.s);
  }

  // Цвета берём из CSS-переменных и перечитываем при смене темы.
  let theme = {};
  function readTheme() {
    const cs = getComputedStyle(canvas);
    const v = (name) => cs.getPropertyValue(name).trim();
    theme = {
      surface: v("--color-surface"), line: v("--color-line"), lineStrong: v("--color-line-strong"),
      ink: v("--color-ink"), muted: v("--color-ink-muted"), subtle: v("--color-ink-subtle"), accent: v("--color-accent"),
      vars: {},
    };
    for (const n of nodes) if (n.colorVar && !theme.vars[n.colorVar]) theme.vars[n.colorVar] = v(n.colorVar) || theme.subtle;
  }
  readTheme();
  const scheme = matchMedia("(prefers-color-scheme: dark)");
  const onScheme = () => { readTheme(); draw(); };
  scheme.addEventListener("change", onScheme);

  // Начальные позиции: проекты по золотой спирали, записи рядом со своим проектом.
  let seed = 42;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const projects = nodes.filter((n) => n.kind === "project").sort((a, b) => b.count - a.count);
  projects.forEach((p, i) => {
    const a = i * 2.39996, d = 40 * Math.sqrt(i + 0.5);
    p.x = Math.cos(a) * d * 2; p.y = Math.sin(a) * d * 2;
  });
  for (const n of nodes) {
    n.vx = 0; n.vy = 0;
    n.charge = n.kind === "project" ? -90 - 6 * Math.min(40, n.count) : -22;
    if (n.kind === "record") {
      const p = byId.get("p:" + n.pkey);
      const a = rnd() * Math.PI * 2, d = 20 + rnd() * 30;
      n.x = (p ? p.x : 0) + Math.cos(a) * d; n.y = (p ? p.y : 0) + Math.sin(a) * d;
    }
  }

  // ---- Физика
  let alpha = 1;
  const THETA2 = 0.81, DMAX2 = 360 * 360;
  function tick() {
    const ns = nodes;
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
    for (const l of links) {
      const { a, b } = l;
      let dx = b.x + b.vx - a.x - a.vx, dy = b.y + b.vy - a.y - a.vy;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      // Проектные пружины короткие и тугие — держат кучку; тематические длинные и слабые — тянут мостики.
      const target = l.kind === "project" ? 14 + a.r + b.r : 90;
      const k = (l.kind === "project" ? 0.7 : 0.03 + 0.12 * l.w) * alpha * (d - target) / d;
      dx *= k; dy *= k;
      b.vx -= dx * 0.5; b.vy -= dy * 0.5; a.vx += dx * 0.5; a.vy += dy * 0.5;
    }
    for (const n of ns) {
      n.vx -= n.x * 0.008 * alpha; n.vy -= n.y * 0.008 * alpha;
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
    }
  }

  // ---- Вид, фильтры, подсветка
  const view = { x: 0, y: 0, k: 1 };
  let W = 0, H = 0, raf = 0, fitted = false, follow = null;
  let visible = () => true, topicLinks = true, marked = null, pinned = null, hover = null;

  function focusSet() {
    const id = hover ? hover.id : pinned;
    if (id == null) return null;
    const set = new Set([id, ...adj.get(id)]);
    const n = byId.get(id);
    if (n.kind === "record" && !topicLinks) for (const o of adj.get(id)) if (byId.get(o).kind === "record") set.delete(o);
    return set;
  }

  // Радиус в мире, но не меньше 2.5px на экране, иначе при обзоре всей базы точки пропадают.
  const worldR = (nd) => Math.max(nd.r, 2.5 / view.k);
  const screenR = (nd) => worldR(nd) * view.k;

  function draw() {
    if (!W) return;
    const dpr = devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    ctx.translate(view.x, view.y);
    ctx.scale(view.k, view.k);
    const focus = focusSet(), px = 1 / view.k;

    ctx.lineWidth = px;
    for (const l of links) {
      if (!visible(l.a) || !visible(l.b)) continue;
      const topic = l.kind === "topic";
      if (topic && !topicLinks) continue;
      const lit = focus && focus.has(l.a.id) && focus.has(l.b.id);
      ctx.globalAlpha = focus ? (lit ? 0.9 : 0.05) : topic ? 0.25 + 0.55 * l.w : 0.55;
      ctx.strokeStyle = topic ? (lit ? theme.accent : theme.lineStrong) : theme.line;
      ctx.beginPath(); ctx.moveTo(l.a.x, l.a.y); ctx.lineTo(l.b.x, l.b.y); ctx.stroke();
    }
    for (const pass of ["record", "project"]) {
      for (const n of nodes) {
        if (n.kind !== pass || !visible(n)) continue;
        const dim = (focus && !focus.has(n.id)) || (marked && !marked.has(n.id) && n.kind === "record");
        ctx.globalAlpha = dim ? 0.14 : 1;
        ctx.beginPath(); ctx.arc(n.x, n.y, worldR(n), 0, Math.PI * 2);
        if (n.kind === "record") {
          ctx.fillStyle = theme.vars[n.colorVar]; ctx.fill();
          ctx.lineWidth = 1.5 * px; ctx.strokeStyle = theme.surface; ctx.stroke();
        } else {
          ctx.fillStyle = theme.surface; ctx.fill();
          ctx.lineWidth = 2 * px; ctx.strokeStyle = theme.muted; ctx.stroke();
        }
        if ((marked && marked.has(n.id)) || n.id === pinned) {
          ctx.globalAlpha = 1; ctx.lineWidth = 2 * px; ctx.strokeStyle = theme.accent;
          ctx.beginPath(); ctx.arc(n.x, n.y, worldR(n) + 3 * px, 0, Math.PI * 2); ctx.stroke();
        }
      }
    }
    ctx.restore();

    // Подписи — в экранных координатах, без наложений: сначала наведённое и крупные проекты, потом остальное.
    // Проекты от 5 записей подписаны всегда, остальные проекты с масштаба 1.5, записи с 2.5.
    const cand = [];
    for (const nd of nodes) {
      if (!visible(nd)) continue;
      const lit = focus && focus.has(nd.id);
      if (focus && !lit) continue;
      const proj = nd.kind === "project";
      const show = proj
        ? nd.count >= 5 || view.k >= 1.5 || lit
        : view.k >= 2.5 || (lit && focus.size <= 16) || (marked && marked.has(nd.id) && marked.size <= 16);
      if (!show) continue;
      const prio = (hover && hover.id === nd.id) || nd.id === pinned ? 1e6 : proj ? 1000 + nd.count : lit ? 500 : nd.r;
      cand.push([prio, nd]);
    }
    cand.sort((a, b) => b[0] - a[0]);
    const placed = [];
    ctx.textAlign = "center"; ctx.textBaseline = "bottom"; ctx.lineJoin = "round";
    for (const [prio, nd] of cand) {
      const proj = nd.kind === "project";
      ctx.font = proj ? "500 12px 'IBM Plex Mono', monospace" : "400 13px 'Golos Text', sans-serif";
      const text = nd.label.length > 42 ? nd.label.slice(0, 41) + "…" : nd.label;
      const w = ctx.measureText(text).width, sx = nd.x * view.k + view.x, sy = nd.y * view.k + view.y - screenR(nd) - 4;
      const box = [sx - w / 2 - 3, sy - 15, sx + w / 2 + 3, sy + 1];
      if (box[2] < 0 || box[0] > W || box[3] < 0 || box[1] > H) continue;
      if (prio < 1e6 && placed.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) continue;
      placed.push(box);
      ctx.lineWidth = 3; ctx.strokeStyle = theme.surface; ctx.strokeText(text, sx, sy);
      ctx.fillStyle = proj ? theme.muted : theme.ink; ctx.fillText(text, sx, sy);
    }
  }

  function loop() {
    raf = 0;
    for (let i = 0; i < 3 && alpha > 0.004; i++) tick();
    if (follow) center(follow);
    else if (!fitted) fit(true);       // пока ты не двигал вид, граф вписан в рамку
    draw();
    if (alpha > 0.004) raf = requestAnimationFrame(loop);
  }
  function heat(a) { alpha = Math.max(alpha, a); if (!raf) raf = requestAnimationFrame(loop); }

  function bounds(all) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of nodes) {
      if (!all && !visible(n)) continue;
      x0 = Math.min(x0, n.x - n.r); y0 = Math.min(y0, n.y - n.r); x1 = Math.max(x1, n.x + n.r); y1 = Math.max(y1, n.y + n.r);
    }
    return x0 === Infinity ? null : { x0, y0, x1, y1 };
  }
  function fit(silent) {
    const b = bounds(false);
    if (!b || !W) return;
    const padX = 40, padTop = 64, padBottom = 48;
    const k = Math.min(4, (W - padX * 2) / (b.x1 - b.x0 || 1), (H - padTop - padBottom) / (b.y1 - b.y0 || 1));
    view.k = Math.max(0.15, k);
    view.x = W / 2 - ((b.x0 + b.x1) / 2) * view.k;
    view.y = padTop + (H - padTop - padBottom) / 2 - ((b.y0 + b.y1) / 2) * view.k;
    if (!silent) draw();
  }
  function center(nd) { view.x = W / 2 - nd.x * view.k; view.y = H / 2 - nd.y * view.k; }
  function zoomAt(f, sx, sy) {
    const k = Math.max(0.15, Math.min(8, view.k * f));
    view.x = sx - (sx - view.x) * (k / view.k);
    view.y = sy - (sy - view.y) * (k / view.k);
    view.k = k;
    fitted = true;
    follow = null;
    draw();
  }

  // ---- Указатель: наведение, клик, перетаскивание, щипок
  const toWorld = (sx, sy) => [(sx - view.x) / view.k, (sy - view.y) / view.k];
  function hit(sx, sy) {
    const [x, y] = toWorld(sx, sy);
    let best = null, bd = Infinity;
    for (const n of nodes) {
      if (!visible(n)) continue;
      const d = Math.hypot(n.x - x, n.y - y);
      if (d < n.r + 5 / view.k && d < bd) { best = n; bd = d; }
    }
    return best;
  }
  const pointers = new Map();
  let drag = null, pinch = null;
  const local = (e) => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };

  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId);
    const [sx, sy] = local(e);
    pointers.set(e.pointerId, [sx, sy]);
    if (pointers.size === 2) {
      const [[ax, ay], [bx, by]] = [...pointers.values()];
      pinch = { d: Math.hypot(ax - bx, ay - by) };
      if (drag && drag.node) { drag.node.fx = drag.node.fy = null; }
      drag = null;
      return;
    }
    const node = hit(sx, sy);
    drag = { node, sx, sy, vx: view.x, vy: view.y, moved: false };
    if (node) { node.fx = node.x; node.fy = node.y; }
    canvas.classList.add("dragging");
  });
  canvas.addEventListener("pointermove", (e) => {
    const [sx, sy] = local(e);
    if (pointers.has(e.pointerId)) pointers.set(e.pointerId, [sx, sy]);
    if (pinch && pointers.size === 2) {
      const [[ax, ay], [bx, by]] = [...pointers.values()];
      const d = Math.hypot(ax - bx, ay - by);
      zoomAt(d / pinch.d, (ax + bx) / 2, (ay + by) / 2);
      pinch.d = d;
      return;
    }
    if (drag) {
      if (Math.hypot(sx - drag.sx, sy - drag.sy) > 4) drag.moved = true;
      if (!drag.moved) return;
      opts.onHover(null);
      if (drag.node) {
        const [x, y] = toWorld(sx, sy);
        drag.node.fx = x; drag.node.fy = y;
        fitted = true;                   // иначе вид уезжал бы из-под пальца
        follow = null;
        heat(0.2);
      } else {
        view.x = drag.vx + sx - drag.sx; view.y = drag.vy + sy - drag.sy;
        fitted = true;
        follow = null;
        draw();
      }
      return;
    }
    const n = hit(sx, sy);
    if (n !== hover) { hover = n; draw(); }
    canvas.classList.toggle("pointer", !!n);
    opts.onHover(n, e.clientX, e.clientY);
  });
  function release(e) {
    pointers.delete(e.pointerId);
    if (pointers.size < 2) pinch = null;
    canvas.classList.remove("dragging");
    if (!drag) return;
    const { node, moved } = drag;
    drag = null;
    if (node) { node.fx = node.fy = null; }
    if (node && !moved && e.type === "pointerup") {
      if (node.kind === "record") opts.onOpen(node.id); else opts.onProject(node.pkey);
    }
  }
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);
  canvas.addEventListener("pointerleave", () => { if (!drag && hover) { hover = null; draw(); } opts.onHover(null); });
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const [sx, sy] = local(e);
    zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), sx, sy);
  }, { passive: false });

  function resize() {
    const r = canvas.getBoundingClientRect(), dpr = devicePixelRatio || 1;
    if (!r.width) return;
    const first = !W;
    W = r.width; H = r.height;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    if (first) {
      if (reduce) { while (alpha > 0.004) tick(); fit(true); draw(); }
      else { for (let i = 0; i < 40; i++) tick(); heat(alpha); }
    } else if (!fitted) fit();
    else draw();
  }

  return {
    resize,
    fit: () => { fitted = true; follow = null; fit(); },
    zoom: (f) => zoomAt(f, W / 2, H / 2),
    setFilter(fn) { visible = fn; draw(); },
    setTopicLinks(on) { topicLinks = on; draw(); },
    highlight(ids) { marked = ids && ids.size ? ids : null; draw(); },
    focus(id) {
      const n = byId.get(id);
      if (!n) return;
      pinned = id;
      // Приближаем в 1,6 раза от вписанного вида и держим узел в центре, пока раскладка не успокоится.
      if (!fitted) fit(true);
      view.k = Math.min(8, Math.max(2, view.k * 1.6));
      fitted = true;
      follow = n;
      center(n);
      draw();
    },
    unpin() { pinned = null; follow = null; draw(); },
    stats: () => ({ nodes: nodes.length, links: links.length }),
  };
}
