/* 3D-адаптер сохраняет интерфейс canvas-графа, которым пользуется app.js.
   Весь WebGL-стек находится в локальном graph-vendor.js; сеть не нужна. */
function createDashboardGraph(host, opts) {
  const probe = document.createElement("canvas");
  let context;
  try { context = probe.getContext("webgl2"); } catch { /* WebGL выключен */ }
  if (context && typeof BrainGraph3D !== "undefined") {
    context.getExtension("WEBGL_lose_context")?.loseContext();
    host.dataset.graphMode = "3d";
    return createGraph3D(host, opts);
  }
  const canvas = document.createElement("canvas");
  host.dataset.graphMode = "2d";
  host.append(canvas);
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", host.getAttribute("aria-label"));
  document.querySelector("#graph-help").textContent = "3D недоступен в этом браузере — показан 2D-граф. Перетаскивай фон для сдвига, колесо — масштаб.";
  // Мосты между проектами нужны только карте проектов, а она есть лишь в 3D.
  const graph = createGraph(canvas, { ...opts, links: opts.links.filter((link) => link.kind !== "bridge") });
  return { ...graph, setActive() {} };
}

function createGraph3D(host, opts) {
  const { ForceGraph3D, SpriteText, Group, Mesh, MeshLambertMaterial, MeshBasicMaterial, SphereGeometry, Vector3, forceCollide } = BrainGraph3D;
  const { nodes, links } = opts;
  const STORE = "brain.graph.layout.3d.v1";
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  const scheme = matchMedia("(prefers-color-scheme: dark)");
  const prefs = { topic: true, minW: 0, areas: true, labels: "less", mode: "all", ...opts.prefs };
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const inc = new Map(nodes.map((node) => [node.id, []]));
  const kids = new Map();
  const hubs = nodes.filter((node) => node.kind === "project").sort((a, b) => b.count - a.count);
  for (const link of links) {
    link.a = byId.get(link.s); link.b = byId.get(link.t);
    link.source = link.a; link.target = link.b;
    link.cross = link.a.pkey !== link.b.pkey;
    inc.get(link.s).push(link); inc.get(link.t).push(link);
    if (link.kind === "project") {
      link.a.hub = link.b;
      (kids.get(link.t) || kids.set(link.t, []).get(link.t)).push(link.a);
    }
  }
  const other = (link, id) => link.s === id ? link.b : link.a;
  const linkOn = (link) => link.kind === "project" || (link.kind === "bridge" ? prefs.mode === "map" : prefs.topic && link.w >= prefs.minW);
  let filter = opts.filter || (() => true), local = null, snapshot = null;
  let pinned = null, hover = null, marked = null, focus = null;
  let active = true, autoFit = true, follow = false, fitFrame = 0, labelTimer = 0, tickCount = 0;
  let width = 0, height = 0, theme = {}, pointer = { x: 0, y: 0 };
  const visible = (node) => filter(node) && (!local || local.has(node.id));
  const shown = (link) => linkOn(link) && visible(link.a) && visible(link.b);
  const duration = () => motion.matches ? 0 : 450;
  const objects = new Map(), areas = new Map();
  const sphere = new SphereGeometry(1, 12, 8);

  function readTheme() {
    const css = getComputedStyle(host);
    const value = (key) => css.getPropertyValue(key).trim();
    theme = { surface: value("--color-surface"), ink: value("--color-ink"),
      muted: value("--color-ink-muted"), line: value("--color-line-strong"),
      accent: value("--color-accent"), colors: {} };
    for (const node of nodes) if (node.colorVar) theme.colors[node.colorVar] = value(node.colorVar);
  }
  readTheme();
  const nodeColor = (node) => node.kind === "project" ? theme.muted : theme.colors[node.colorVar] || theme.muted;
  function linkColor(link) {
    const highlighted = focus?.has(link.s) && focus.has(link.t);
    const color = highlighted || link.kind !== "project" ? theme.accent : theme.muted;
    const opacity = highlighted ? 1 : focus ? 0.18 : link.kind === "project" ? 0.4
      : link.kind === "bridge" ? 0.3 + 0.5 * Math.min(1, link.w / 12) : 0.5 + 0.3 * Math.min(1, link.w);
    // CSS-токены заданы в hex; 3d-force-graph учитывает альфа-канал цвета отдельно для каждого ребра.
    return color + Math.round(opacity * 255).toString(16).padStart(2, "0");
  }

  function place(fresh = false) {
    let saved = {};
    if (!fresh) try { saved = JSON.parse(localStorage.getItem(STORE))?.pos || {}; } catch { /* нет сохранения */ }
    let seed = 42, known = 0;
    const rnd = () => ((seed = seed * 16807 % 2147483647) / 2147483647);
    const scatter = (i, total, radius) => {
      const y = 1 - 2 * (i + 0.5) / total, a = i * 2.399963;
      const r = Math.sqrt(1 - y * y) * radius;
      return [Math.cos(a) * r, y * radius, Math.sin(a) * r];
    };
    for (const node of nodes) {
      const pos = saved[node.id];
      node.placed = Array.isArray(pos) && pos.length === 3 && pos.every(Number.isFinite);
      if (node.placed) { [node.x, node.y, node.z] = pos; known++; }
      node.vx = node.vy = node.vz = 0;
      delete node.fx; delete node.fy; delete node.fz;
    }
    hubs.forEach((node, i) => {
      if (!node.placed) [node.x, node.y, node.z] = scatter(i, hubs.length, 110 * Math.cbrt(hubs.length) * (0.65 + 0.35 * rnd()));
    });
    nodes.forEach((node, i) => {
      if (node.placed || node.kind === "project") return;
      const anchor = node.hub;
      const pos = scatter(i, nodes.length, anchor ? 25 + 45 * rnd() : 130 * Math.cbrt(hubs.length + 1));
      [node.x, node.y, node.z] = pos.map((v, axis) => v + (anchor ? anchor[["x", "y", "z"][axis]] : 0));
    });
    return known / Math.max(1, nodes.length);
  }
  function saveLayout() {
    if (snapshot) return;
    const pos = Object.fromEntries(nodes.map((node) => [node.id, [node.x, node.y, node.z].map((v) => Math.round(v * 10) / 10)]));
    try { localStorage.setItem(STORE, JSON.stringify({ pos })); } catch { /* приватный режим или квота */ }
  }
  const known = place();

  function makeNode(node) {
    if (objects.has(node.id)) return objects.get(node.id).group;
    const group = new Group();
    const dim = (focus && !focus.has(node.id)) || (marked && !marked.has(node.id) && node.id !== pinned);
    const ball = new Mesh(sphere, new MeshLambertMaterial({
      color: nodeColor(node), transparent: true, opacity: dim ? 0.12 : 0.95,
      emissive: node.id === pinned || marked?.has(node.id) ? nodeColor(node) : "#000000", emissiveIntensity: 0.35,
    }));
    ball.scale.setScalar(node.r);
    // Зона попадания шире самой сферы: мелкий узел в 2–4 px иначе не поймать курсором.
    ball.raycast = (raycaster, intersects) => {
      const center = new Vector3(node.x, node.y, node.z), ray = raycaster.ray;
      const distance = center.clone().sub(ray.origin).dot(ray.direction);
      if (distance <= 0) return;
      const pixel = 2 * distance * Math.tan(camera.fov * Math.PI / 360) / Math.max(1, height);
      const reach = Math.max(ball.scale.x * 1.25, pixel * (node.kind === "project" ? 14 : 8));
      const miss = Math.sqrt(ray.distanceSqToPoint(center));
      if (miss > reach) return;
      // Точное попадание в сферу важнее запаса: соседний узел не перехватит клик, даже если он ближе к камере.
      const exact = miss <= ball.scale.x;
      intersects.push({ distance: exact ? distance : 1e6 + miss / pixel, point: center, object: ball });
    };
    group.add(ball);
    let ring;
    if (node.kind === "project") {
      ring = new Mesh(sphere, new MeshBasicMaterial({ color: nodeColor(node), wireframe: true, transparent: true, opacity: dim ? 0.05 : 0.35 }));
      ring.scale.setScalar(node.r * 1.45);
      ring.raycast = () => {};
      group.add(ring);
    }
    objects.set(node.id, { group, ball, ring, label: null });
    return group;
  }

  const view = new ForceGraph3D(host, { controlType: "orbit", rendererConfig: { antialias: true, alpha: true } })
    .backgroundColor(theme.surface).showNavInfo(false).numDimensions(3)
    // Перетаскивание вращает камеру и над узлами; клик выбирает узел.
    // DragControls библиотеки посылает synthetic pointerup без pointerId,
    // который конфликтует с учётом указателей в OrbitControls.
    .enableNodeDrag(false)
    .nodeThreeObject(makeNode).nodeLabel("").linkLabel("")
    .linkColor(linkColor).linkOpacity(1)
    // Экранная линия остаётся толщиной 1px и при отдалении камеры.
    .linkWidth(0).linkVisibility(shown)
    .d3AlphaDecay(0.028).d3VelocityDecay(0.35).cooldownTicks(motion.matches ? 0 : 160).cooldownTime(5000)
    .warmupTicks(motion.matches ? 160 : known > 0.8 ? 0 : 50)
    .onNodeClick((node) => { if (pinned === node.id) opts.onOpen?.(node); else select(node.id, { fly: true }); })
    .onBackgroundClick(() => select(null))
    .onNodeHover((node) => setHover(node))
    .onEngineTick(() => {
      if (++tickCount % 8 === 0) updateAreas();
      if (autoFit && tickCount % 24 === 0) fitNow(0);
      // Пока раскладка успокаивается, выбранный узел уезжает: камера идёт за ним.
      else if (follow && pinned != null && tickCount % 24 === 0) flyTo(byId.get(pinned), Math.min(300, duration()));
    })
    .onEngineStop(() => {
      updateAreas(); updateLabels(); saveLayout();
      if (autoFit) fitNow(duration()); else if (follow && pinned != null) flyTo(byId.get(pinned));
      follow = false;
    });
  const controls = view.controls();
  controls.enableDamping = !motion.matches;
  controls.dampingFactor = 0.12;
  controls.minDistance = 25;
  controls.maxDistance = 18000;
  view.renderer().setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  const camera = view.camera();
  camera.far = 50000;
  camera.updateProjectionMatrix();
  view.d3Force("charge").strength((node) => node.kind === "project" ? -220 : -38).distanceMax(650);
  view.d3Force("link").distance((link) => link.kind === "project" ? 28 + link.a.r + link.b.r : link.kind === "bridge" ? 150 : link.cross ? 180 : 55)
    .strength((link) => link.kind === "project" ? 0.55 : link.kind === "bridge" ? 0.02 + 0.06 * Math.min(1, link.w / 12)
      : link.cross ? 0.015 + link.w * 0.035 : 0.06 + link.w * 0.1);
  view.d3Force("collide", forceCollide((node) => node.r + 3).iterations(1));
  // Слабое притяжение удерживает изолированные записи рядом с облаком.
  view.d3Force("bounds", (alpha) => {
    for (const node of view.graphData().nodes) {
      node.vx -= node.x * 0.002 * alpha;
      node.vy -= node.y * 0.002 * alpha;
      node.vz -= node.z * 0.002 * alpha;
    }
  });

  const areaGroup = new Group();
  view.scene().add(areaGroup);
  for (const hub of hubs) {
    const mesh = new Mesh(sphere, new MeshBasicMaterial({ color: theme.muted, transparent: true, opacity: 0.035, depthWrite: false }));
    mesh.raycast = () => {};
    areaGroup.add(mesh);
    areas.set(hub.id, mesh);
  }
  function updateAreas() {
    areaGroup.visible = prefs.areas;
    for (const hub of hubs) {
      const mesh = areas.get(hub.id), children = (kids.get(hub.id) || []).filter(visible);
      mesh.visible = visible(hub) && children.length > 1 && (!focus || focus.has(hub.id));
      if (!mesh.visible) continue;
      let radius = hub.r * 2;
      for (const node of children) radius = Math.max(radius, Math.hypot(node.x - hub.x, node.y - hub.y, node.z - hub.z) + node.r);
      mesh.position.set(hub.x, hub.y, hub.z);
      mesh.scale.setScalar(radius);
    }
  }

  function updateLabels() {
    clearTimeout(labelTimer);
    labelTimer = 0;
    const chosen = new Set();
    if (pinned != null) chosen.add(pinned);
    if (hover) chosen.add(hover.id);
    const candidates = nodes.filter(visible).map((node) => {
      const position = new Vector3(node.x, node.y, node.z);
      const distance = camera.position.distanceTo(position);
      const projected = position.project(camera);
      return { node, distance, projected, pixels: node.r * height / (2 * distance * Math.tan(camera.fov * Math.PI / 360)) };
    }).filter(({ projected }) => Math.abs(projected.x) < 0.92 && Math.abs(projected.y) < 0.88 && projected.z > -1 && projected.z < 1)
      .sort((a, b) => Number(marked?.has(b.node.id) || false) - Number(marked?.has(a.node.id) || false) || b.pixels - a.pixels);
    // На карте проектов узлов мало — подписей можно показать больше.
    const limit = prefs.labels === "more" ? 65 : candidates.length < 400 ? 48 : 24;
    const rects = [];
    for (const item of candidates) {
      const { node, pixels, projected } = item;
      if (chosen.size >= limit) break;
      if (focus && !focus.has(node.id)) continue;
      if (node.kind !== "project" && (prefs.labels !== "more" || pixels < 3)) continue;
      const x = (projected.x + 1) * width / 2, y = (1 - projected.y) * height / 2;
      const w = Math.min(node.label.length, 38) * 3.5;
      if (rects.some((r) => Math.abs(r.x - x) < r.w + w && Math.abs(r.y - y) < 24)) continue;
      chosen.add(node.id); rects.push({ x, y, w });
    }
    for (const node of nodes) {
      const object = objects.get(node.id);
      if (!object) continue;
      const show = visible(node) && chosen.has(node.id);
      if (show && !object.label) {
        const text = node.label.length > 40 ? node.label.slice(0, 39) + "…" : node.label;
        const label = new SpriteText(text, 8, theme.ink);
        label.fontFace = "system-ui, sans-serif";
        label.fontSize = 42;
        label.fontWeight = node.kind === "project" ? "600" : "400";
        label.backgroundColor = theme.surface;
        label.padding = [2, 1]; label.borderRadius = 3;
        label.material.depthWrite = false;
        label.raycast = () => {};
        object.label = label; object.group.add(label);
      }
      if (object.label) {
        object.label.visible = show;
        if (show) {
          const distance = camera.position.distanceTo(new Vector3(node.x, node.y, node.z));
          object.label.textHeight = Math.max(4, 13 * 2 * distance * Math.tan(camera.fov * Math.PI / 360) / Math.max(1, height));
          object.label.position.set(0, object.ball.scale.x + object.label.textHeight, 0);
        }
      }
      // Мелкие записи различимы и в обзоре большой базы.
      const distance = camera.position.distanceTo(new Vector3(node.x, node.y, node.z));
      const pixelSize = 2 * distance * Math.tan(camera.fov * Math.PI / 360) / Math.max(1, height);
      const radius = Math.max(node.r, pixelSize * (node.kind === "project" ? 4 : 1.8));
      object.ball.scale.setScalar(radius);
      if (object.ring) object.ring.scale.setScalar(radius * 1.45);
    }
  }
  function scheduleLabels() { if (!labelTimer) labelTimer = setTimeout(updateLabels, 100); }

  function refocus() {
    const id = pinned ?? hover?.id;
    focus = id == null ? null : new Set([id]);
    if (focus) {
      for (const link of inc.get(id)) if (shown(link)) focus.add(other(link, id).id);
      if (byId.get(id).kind === "project") for (const child of kids.get(id) || []) {
        if (!visible(child)) continue;
        for (const link of inc.get(child.id)) if (link.kind === "topic" && shown(link)) {
          const neighbor = other(link, child.id);
          focus.add(neighbor.id); if (neighbor.hub) focus.add(neighbor.hub.id);
        }
      }
    }
    for (const node of nodes) {
      const object = objects.get(node.id);
      if (!object) continue;
      const dim = (focus && !focus.has(node.id)) || (marked && !marked.has(node.id) && node.id !== pinned);
      object.ball.material.opacity = dim ? 0.12 : 0.95;
      object.ball.material.emissive.set(node.id === pinned || marked?.has(node.id) ? nodeColor(node) : "#000000");
      object.ball.material.emissiveIntensity = 0.35;
      if (object.ring) object.ring.material.opacity = dim ? 0.05 : 0.35;
    }
    // Окрестность выделяем цветом; остальные связи остаются видны фоном.
    view.linkVisibility(shown).linkColor(linkColor);
    updateAreas(); updateLabels();
  }
  function setHover(node) {
    if (hover === node) return;
    hover = node;
    host.style.cursor = node ? "pointer" : "grab";
    opts.onHover?.(node, pointer.x, pointer.y);
    refocus();
  }
  host.addEventListener("pointermove", (event) => {
    pointer = { x: event.clientX, y: event.clientY };
    if (hover) opts.onHover?.(hover, pointer.x, pointer.y);
  });
  host.addEventListener("pointerleave", () => setHover(null));
  controls.addEventListener("start", () => { autoFit = false; follow = false; setHover(null); });
  controls.addEventListener("change", scheduleLabels);

  // Центр проекции переносится в свободную часть холста рядом с панелью.
  function resize() {
    width = host.clientWidth; height = host.clientHeight;
    if (!width || !height) return;
    view.width(width).height(height);
    const inset = opts.inset?.() || {};
    const dx = ((inset.left || 0) - (inset.right || 0)) / 2;
    const dy = ((inset.top || 0) - (inset.bottom || 0)) / 2;
    camera.setViewOffset(width, height, -dx, -dy, width, height);
    camera.updateProjectionMatrix();
    if (autoFit) requestFit();
    scheduleLabels();
  }
  function fitNow(ms = duration()) {
    const list = nodes.filter(visible);
    if (!list.length || !width || !height) return;
    const min = new Vector3(Infinity, Infinity, Infinity), max = new Vector3(-Infinity, -Infinity, -Infinity);
    for (const node of list) {
      min.min(new Vector3(node.x - node.r, node.y - node.r, node.z - node.r));
      max.max(new Vector3(node.x + node.r, node.y + node.r, node.z + node.r));
    }
    const center = min.clone().add(max).multiplyScalar(0.5);
    const inset = opts.inset?.() || {};
    const tanY = Math.tan(camera.fov * Math.PI / 360);
    const tanX = tanY * width / height;
    const freeX = Math.max(0.15, (width - (inset.left || 0) - (inset.right || 0) - 100) / width);
    const freeY = Math.max(0.15, (height - (inset.top || 0) - (inset.bottom || 0) - 130) / height);
    const inverse = camera.quaternion.clone().invert();
    let distance = 100;
    for (const node of list) {
      const point = new Vector3(node.x, node.y, node.z).sub(center).applyQuaternion(inverse);
      distance = Math.max(distance, point.z + Math.max((Math.abs(point.x) + node.r) / (tanX * freeX),
        (Math.abs(point.y) + node.r) / (tanY * freeY)));
    }
    distance = Math.min(17000, distance);
    const direction = camera.position.clone().sub(controls.target).normalize();
    view.cameraPosition(center.clone().addScaledVector(direction, distance), center, ms);
    scheduleLabels();
  }
  function requestFit() {
    if (!fitFrame) fitFrame = requestAnimationFrame(() => { fitFrame = 0; if (autoFit) fitNow(); });
  }
  function fit() { autoFit = true; resize(); requestFit(); }
  function flyTo(node, ms = duration()) {
    autoFit = false;
    const point = new Vector3(node.x, node.y, node.z);
    const direction = camera.position.clone().sub(controls.target).normalize();
    let distance = 190;
    if (node.kind === "project") {
      // Проект показываем целиком: вместе с облаком его записей.
      let radius = 60;
      for (const child of kids.get(node.id) || []) {
        if (visible(child)) radius = Math.max(radius, Math.hypot(child.x - node.x, child.y - node.y, child.z - node.z) + child.r);
      }
      distance = Math.max(250, radius * 3.4);
    }
    view.cameraPosition(point.clone().addScaledVector(direction, distance), point, ms);
  }

  function around(id) {
    const node = byId.get(id), set = new Set([id]);
    let ring = node.kind === "project" ? kids.get(id) || [] : [node];
    ring.forEach((item) => set.add(item.id));
    for (let depth = node.kind === "project" ? 1 : 2; depth > 0; depth--) {
      ring = ring.flatMap((item) => inc.get(item.id).filter((link) => link.kind === "topic" && linkOn(link)).map((link) => other(link, item.id)))
        .filter((item) => !set.has(item.id));
      ring.forEach((item) => set.add(item.id));
    }
    for (const key of [...set]) if (byId.get(key).hub) set.add(byId.get(key).hub.id);
    return set;
  }
  function refreshData() {
    setHover(null);
    const next = nodes.filter(visible);
    // Пока записи скрыты, проекты успевают разъехаться: вернувшиеся записи ставим рядом со своим проектом,
    // а не там, где их оставила прошлая раскладка.
    if (!snapshot) {
      const before = new Set(view.graphData().nodes.map((node) => node.id));
      for (const node of next) {
        const hub = node.hub;
        if (!hub || before.has(node.id)) continue;
        const spread = 30 + 14 * Math.sqrt((kids.get(hub.id) || []).length);
        if (Math.hypot(node.x - hub.x, node.y - hub.y, node.z - hub.z) < spread * 1.6) continue;
        const a = Math.random() * Math.PI * 2, y = Math.random() * 2 - 1, r = Math.sqrt(1 - y * y), d = spread * (0.4 + 0.6 * Math.random());
        node.x = hub.x + Math.cos(a) * r * d; node.y = hub.y + y * d; node.z = hub.z + Math.sin(a) * r * d;
        node.vx = node.vy = node.vz = 0;
      }
    }
    view.graphData({ nodes: next, links: links.filter(shown) });
    refocus();
  }
  function enterLocal(id) {
    if (!snapshot) snapshot = new Map(nodes.map((node) => [node.id, [node.x, node.y, node.z]]));
    local = around(id);
    const center = snapshot.get(id);
    for (const node of nodes) {
      const pos = snapshot.get(node.id);
      [node.x, node.y, node.z] = pos.map((v, i) => (v - center[i]) * 0.4);
      node.vx = node.vy = node.vz = 0;
    }
    refreshData(); fit();
  }
  function leaveLocal() {
    if (!snapshot) return;
    for (const node of nodes) { [node.x, node.y, node.z] = snapshot.get(node.id); node.vx = node.vy = node.vz = 0; }
    snapshot = null; local = null;
    refreshData(); fit();
  }
  function select(id, options = {}) {
    if (id != null && !byId.has(id)) return;
    pinned = id;
    if (local) { if (id == null) leaveLocal(); else enterLocal(id); }
    opts.onSelect?.(id == null ? null : byId.get(id));
    resize(); refocus();
    follow = id != null && !!options.fly;
    if (follow) flyTo(byId.get(id));
  }

  function updateTheme() {
    readTheme(); view.backgroundColor(theme.surface);
    for (const node of nodes) {
      const object = objects.get(node.id);
      if (!object) continue;
      object.ball.material.color.set(nodeColor(node));
      if (object.ring) object.ring.material.color.set(nodeColor(node));
      if (object.label) { object.label.color = theme.ink; object.label.backgroundColor = theme.surface; }
    }
    for (const mesh of areas.values()) mesh.material.color.set(theme.muted);
    refocus();
  }
  scheme.addEventListener("change", updateTheme);
  motion.addEventListener("change", () => {
    controls.enableDamping = !motion.matches;
    view.cooldownTicks(motion.matches ? 0 : 160).warmupTicks(motion.matches ? 160 : 0);
  });
  function syncActivity() {
    if (active && !document.hidden) view.resumeAnimation(); else { view.pauseAnimation(); saveLayout(); }
  }
  document.addEventListener("visibilitychange", syncActivity);
  addEventListener("pagehide", saveLayout);
  refreshData(); resize(); requestFit();
  return {
    resize, fit, select,
    zoom(factor) {
      autoFit = false;
      const offset = camera.position.clone().sub(controls.target);
      offset.setLength(Math.max(controls.minDistance, Math.min(controls.maxDistance, offset.length() / factor)));
      view.cameraPosition(controls.target.clone().add(offset), controls.target, duration());
    },
    pan(dx, dy) {
      autoFit = false;
      const distance = camera.position.distanceTo(controls.target);
      const scale = 2 * distance * Math.tan(camera.fov * Math.PI / 360) / Math.max(1, height);
      const delta = new Vector3(-dx * scale, dy * scale, 0).applyQuaternion(camera.quaternion);
      view.cameraPosition(camera.position.clone().add(delta), controls.target.clone().add(delta), duration());
    },
    setFilter(fn, refit) {
      filter = fn;
      if (pinned != null && !filter(byId.get(pinned))) select(null);
      refreshData(); if (refit) fit();
    },
    setPrefs(next) {
      const relink = next.topic !== prefs.topic || next.minW !== prefs.minW || next.mode !== prefs.mode;
      Object.assign(prefs, next);
      if (relink) { if (local && pinned != null) enterLocal(pinned); else refreshData(); }
      refocus();
    },
    highlight(ids) { marked = ids?.size ? ids : null; refocus(); },
    selected: () => pinned == null ? null : byId.get(pinned),
    has: (id) => byId.has(id),
    passes: (id) => byId.has(id) && filter(byId.get(id)),
    setLocal(on) { if (on && pinned != null) enterLocal(pinned); else leaveLocal(); return !!local; },
    isLocal: () => !!local,
    relayout() {
      snapshot = null; local = null; place(true); refreshData(); fit();
      opts.onSelect?.(pinned == null ? null : byId.get(pinned));
    },
    setActive(on) { active = on; syncActivity(); },
  };
}
