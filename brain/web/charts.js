/* Графики в SVG: сложенные столбцы, полосы, мини-графики, подсказка.
   Цвета — CSS-переменные, поэтому графики сами переключаются со светлой темы на тёмную. */
const Charts = (() => {
  const nf = new Intl.NumberFormat("ru-RU");
  const tipEl = document.getElementById("tip");

  const tip = {
    show(html, x, y) {
      tipEl.innerHTML = html;
      tipEl.hidden = false;
      const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
      tipEl.style.left = Math.max(8, Math.min(innerWidth - w - 8, x + 14)) + "px";
      tipEl.style.top = (y - h - 12 < 8 ? y + 18 : y - h - 12) + "px";
    },
    hide() { tipEl.hidden = true; },
  };

  function niceMax(v) {
    if (v <= 4) return 4;
    const pow = 10 ** Math.floor(Math.log10(v));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * pow >= v) return m * pow;
    return 10 * pow;
  }

  // Столбец со скруглённым верхом и прямым низом.
  function topRounded(x, y, w, h, r) {
    r = Math.min(r, h, w / 2);
    return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
  }

  /** buckets: [{label, tip, values: {key: n}}], series: [{key, title, color}] в порядке стопки снизу вверх. */
  function stackedBars(el, buckets, series, opts = {}) {
    const W = el.clientWidth, H = el.clientHeight;
    if (!W || !H) return;
    const totals = buckets.map((b) => series.reduce((a, s) => a + (b.values[s.key] || 0), 0));
    if (!totals.some(Boolean)) {
      el.innerHTML = `<div class="chart-empty">${opts.empty || "За этот период записей нет"}</div>`;
      return;
    }
    const pad = { top: 22, right: 0, bottom: 28, left: 40 };
    const pw = W - pad.left - pad.right, ph = H - pad.top - pad.bottom;
    const N = buckets.length;
    const max = niceMax(Math.max(...totals));
    const slot = pw / N, bw = Math.max(2, Math.min(slot * 0.66, 28));
    const y = (v) => pad.top + ph - (v / max) * ph;
    const peak = totals.indexOf(Math.max(...totals));
    const labelEvery = Math.max(1, Math.ceil(N / Math.max(1, Math.floor(pw / 64))));
    let svg = `<svg viewBox="0 0 ${W} ${H}" aria-hidden="true">`;
    for (const t of [...new Set([0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(max * f)))]) {
      svg += `<line class="grid" x1="${pad.left}" x2="${W}" y1="${y(t) + 0.5}" y2="${y(t) + 0.5}"/>`;
      svg += `<text class="axis" x="${pad.left - 8}" y="${y(t) + 4}" text-anchor="end">${nf.format(t)}</text>`;
    }
    buckets.forEach((b, i) => {
      const x = pad.left + i * slot + (slot - bw) / 2;
      let base = pad.top + ph, marks = "";
      const present = series.filter((s) => b.values[s.key]);
      present.forEach((s, j) => {
        const h = (b.values[s.key] / max) * ph;
        const gap = j > 0 ? 2 : 0;           // 2px зазор цвета поверхности между сегментами
        const top = base - h, segH = Math.max(1, h - gap);
        marks += j === present.length - 1
          ? `<path d="${topRounded(x, top, bw, segH, 4)}" fill="${s.color}"/>`
          : `<rect x="${x}" y="${top}" width="${bw}" height="${segH}" fill="${s.color}"/>`;
        base = top;
      });
      const label = totals[i] && (i === peak || i === N - 1)
        ? `<text class="value-label" x="${x + bw / 2}" y="${base - 6}" text-anchor="middle">${nf.format(totals[i])}</text>` : "";
      svg += `<g class="slot" data-i="${i}"><rect class="hit" x="${pad.left + i * slot}" y="${pad.top}" width="${slot}" height="${ph}" rx="4"/>${marks}${label}</g>`;
      if ((N - 1 - i) % labelEvery === 0) {
        svg += `<text class="axis" x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${b.label}</text>`;
      }
    });
    el.innerHTML = svg + "</svg>";
    if (opts.aria) el.setAttribute("aria-label", opts.aria);

    el.onmousemove = (e) => {
      const g = e.target.closest(".slot");
      el.querySelectorAll(".slot.on").forEach((s) => s !== g && s.classList.remove("on"));
      if (!g) return tip.hide();
      g.classList.add("on");
      const i = +g.dataset.i, b = buckets[i];
      const rows = series.filter((s) => b.values[s.key]).reverse()
        .map((s) => `<div class="row"><span class="swatch" style="background:${s.color}"></span>${s.title}<span class="num">${nf.format(b.values[s.key])}</span></div>`).join("");
      tip.show(`<div>${b.tip}</div>${rows || "<small>записей нет</small>"}${rows ? `<div class="row"><b>Всего</b><span class="num">${nf.format(totals[i])}</span></div>` : ""}`, e.clientX, e.clientY);
    };
    el.onmouseleave = () => { tip.hide(); el.querySelectorAll(".slot.on").forEach((s) => s.classList.remove("on")); };
  }

  /** rows: [{label, value, title}] — горизонтальные полосы одного цвета; клик отдаёт строку. */
  function hbars(el, rows, onClick) {
    const max = Math.max(1, ...rows.map((r) => r.value));
    el.innerHTML = rows.map((r, i) => `
      <button class="hbar" type="button" data-i="${i}" title="${r.title || ""}">
        <span class="name">${r.label}</span>
        <span><span class="bar" style="display:block;width:${(r.value / max) * 100}%"></span></span>
        <span class="num">${nf.format(r.value)}</span>
      </button>`).join("");
    el.onclick = (e) => { const b = e.target.closest(".hbar"); if (b) onClick(rows[+b.dataset.i]); };
  }

  /** Мини-график: столбики 4px, высота 24px; values — по порядку времени. */
  function sparkline(values, label) {
    const max = Math.max(1, ...values), w = 4, gap = 2, h = 24;
    const bars = values.map((v, i) => {
      const bh = v ? Math.max(2, (v / max) * h) : 1;
      return `<rect class="${v ? "" : "zero"}" x="${i * (w + gap)}" y="${h - bh}" width="${w}" height="${bh}" rx="1"/>`;
    }).join("");
    return `<svg class="spark" width="${values.length * (w + gap) - gap}" height="${h}" role="img" aria-label="${label}">${bars}</svg>`;
  }

  /** Полоса долей: parts — [{value, color, title}]. */
  function shareBar(parts) {
    const total = parts.reduce((a, p) => a + p.value, 0) || 1;
    return `<div class="share" role="img" aria-label="${parts.map((p) => `${p.title} ${p.value}`).join(", ")}">${parts
      .filter((p) => p.value).map((p) => `<span style="flex:${p.value / total} 1 0;background:${p.color}" title="${p.title}: ${p.value}"></span>`).join("")}</div>`;
  }

  return { tip, stackedBars, hbars, sparkline, shareBar, niceMax };
})();
