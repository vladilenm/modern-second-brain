// Real WebGL + user interactions, using only synthetic data and an isolated browser.
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
import { chromium } from 'playwright';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const out = await mkdtemp(join(tmpdir(), 'brain-graph-test-'));
execFileSync(process.env.PYTHON || 'python3', [join(root, 'tests/dashboard_fixture.py'), out], { cwd: root });
const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: true });
const failures = [];
const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
// Fonts are optional; no network requests leave the test browser.
await context.route(/^https?:/, route => route.fulfill({ contentType: 'text/css', body: '' }));
const url = pathToFileURL(join(out, 'dashboard.html')).href;
async function open(options = {}) {
  const page = await context.newPage();
  page.on('pageerror', error => failures.push(error.stack));
  page.on('console', message => { if (message.type() === 'error') failures.push(message.text()); });
  if (options.noWebGL) await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (kind, ...args) {
      return kind.startsWith('webgl') ? null : original.call(this, kind, ...args);
    };
  });
  if (options.reduced) await page.emulateMedia({ reducedMotion: 'reduce' });
  // Most checks exercise the full record graph; the project map (the default) has its own section.
  if (!options.map) await page.addInitScript(() => {
    if (!localStorage.getItem('brain.graph.prefs')) localStorage.setItem('brain.graph.prefs', JSON.stringify({ mode: 'all' }));
  });
  await page.goto(options.empty ? pathToFileURL(join(out, 'empty.html')).href : url);
  // Capture the real library instance without adding test hooks to production code.
  await page.evaluate(() => {
    const Original = BrainGraph3D.ForceGraph3D;
    BrainGraph3D = { ...BrainGraph3D, ForceGraph3D: class extends Original {
      constructor(...args) { super(...args); globalThis.testView = this; }
    } };
  });
  await page.locator('.nav a[data-tab="graph"]').click();
  return page;
}
async function stats(page) {
  return page.evaluate(() => ({
    nodes: testView.graphData().nodes.length,
    records: testView.graphData().nodes.filter(n => n.kind === 'record').length,
    links: testView.graphData().links.length,
    position: testView.camera().position.toArray(),
    distance: testView.camera().position.distanceTo(testView.controls().target),
  }));
}
async function search(page, query) {
  await page.locator('#g-q').fill(query);
  await page.locator('#g-q').press('Enter');
  await page.waitForFunction(() => graph.selected() !== null);
  await page.waitForTimeout(600);
}
try {
  const page = await open();
  await page.waitForFunction(() => globalThis.testView?.graphData().nodes.length === 54);
  await page.waitForFunction(() => localStorage.getItem('brain.graph.layout.3d.v1'));
  assert.equal((await stats(page)).records, 48);
  assert(await page.evaluate(() => testView.graphData().nodes.every(n => [n.x, n.y, n.z].every(Number.isFinite))));
  assert(await page.evaluate(() => new Set(testView.graphData().nodes.map(n => Math.round(n.z))).size > 10));
  const initial = await stats(page);
  const box = await page.locator('#graph').boundingBox();
  await page.mouse.move(box.x + 70, box.y + 170);
  await page.mouse.down();
  await page.mouse.move(box.x + 200, box.y + 230, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(700);
  assert.notDeepEqual((await stats(page)).position, initial.position, 'drag rotates camera');
  const rotated = await stats(page);
  await page.locator('[data-zoom="in"]').click();
  await page.waitForTimeout(600);
  assert((await stats(page)).distance < rotated.distance, 'zoom changes camera distance');

  await search(page, 'Запись 01');
  assert.equal(await page.evaluate(() => graph.selected().id), 1);
  assert.equal(await page.locator('#graph-panel').isVisible(), true);
  assert(await page.evaluate(() => testView.graphData().links.every(link => link.__lineObj?.visible)),
    'selecting a node preserves other connections as context');
  assert(await page.evaluate(() => {
    const links = testView.graphData().links;
    const connected = links.find(link => link.s === 1 && link.t === 2).__lineObj;
    const unrelated = links.find(link => link.s === 17 && link.t === 18).__lineObj;
    return connected.isLine && connected.material.opacity > unrelated.material.opacity && unrelated.material.opacity > 0;
  }), 'selected connections are brighter while background lines remain visible at any zoom');
  await page.evaluate(() => testView.onNodeHover()(testView.graphData().nodes.find(node => node.id === 17)));
  await page.waitForTimeout(150);
  assert(await page.evaluate(() => {
    const links = testView.graphData().links;
    return links.find(link => link.s === 1 && link.t === 2).__lineObj.material.opacity >
      links.find(link => link.s === 17 && link.t === 18).__lineObj.material.opacity;
  }), 'hovering another node preserves the selected connections');
  await page.evaluate(() => testView.onNodeHover()(null));
  const screen = await page.evaluate(() => {
    const node = graph.selected(), point = testView.graph2ScreenCoords(node.x, node.y, node.z);
    const canvas = document.querySelector('#graph').getBoundingClientRect();
    const panel = document.querySelector('#graph-panel').getBoundingClientRect();
    return { x: canvas.x + point.x, y: canvas.y + point.y, panelX: panel.x };
  });
  assert(screen.x < screen.panelX - 30, 'selected node stays beside the panel');
  await page.mouse.move(screen.x, screen.y);
  await page.waitForTimeout(200);
  await page.mouse.click(screen.x, screen.y);
  await page.locator('.drawer').waitFor();
  await page.keyboard.press('Escape');
  await page.locator('[data-local]').click();
  await page.waitForTimeout(400);
  assert.equal(await page.evaluate(() => graph.isLocal()), true);
  assert((await stats(page)).nodes < initial.nodes, 'local mode limits graph data and physics');
  await page.locator('[data-local]').click();
  await page.waitForFunction(() => testView.graphData().nodes.length === 54);
  await page.locator('#graph-panel .gp-close').click();
  await page.locator('#g-q').fill('');

  await page.locator('#gf-btn').click();
  await page.locator('.chip[data-src="codex"]').click();
  await page.waitForFunction(() => testView.graphData().nodes.filter(n => n.kind === 'record').length === 24);
  assert.match(await page.locator('#gf-count').innerText(), /1/, 'filter button shows active filters');
  await page.locator('.chip[data-src="codex"]').click();
  await page.locator('#g-period [data-v="30"]').click();
  await page.waitForFunction(() => testView.graphData().nodes.filter(n => n.kind === 'record').length === 36);
  await page.locator('#g-period [data-v="all"]').click();
  const stage = await page.locator('.graph-box').boundingBox();
  assert(stage.height > 1100 - 40, 'graph canvas fills the window height');

  // Project map: only projects first, a click on a project reveals its records.
  await page.locator('#g-mode [data-v="map"]').click();
  await page.waitForFunction(() => testView.graphData().nodes.length === 6);
  assert(await page.evaluate(() => testView.graphData().links.some(l => l.kind === 'bridge')), 'map links projects by shared topics');
  await page.evaluate(() => showOnGraph(9));
  await page.waitForFunction(() => testView.graphData().nodes.filter(n => n.kind === 'record').length === 8);
  assert.equal(await page.evaluate(() => graph.selected().id), 9, 'a record opens its project on the map');
  await page.locator('#graph-panel .gp-close').click();
  await page.waitForFunction(() => testView.graphData().nodes.length === 6);
  await search(page, 'Запись 17');
  assert.equal(await page.evaluate(() => graph.selected().id), 17, 'graph search reaches records hidden inside projects');
  await page.locator('#graph-panel .gp-close').click();
  await page.locator('#g-q').fill('');
  await page.locator('#g-mode [data-v="all"]').click();
  await page.waitForFunction(() => testView.graphData().nodes.length === 54);
  assert(await page.evaluate(() => testView.graphData().links.every(l => l.kind !== 'bridge')), 'bridges belong to the map only');
  await page.locator('#gv-btn').click();
  await page.locator('#gv-topic').uncheck();
  await page.waitForFunction(() => testView.graphData().links.every(l => l.kind === 'project'));
  await page.locator('#gv-topic').check();
  await page.locator('#gv-minw').fill('0.6');
  await page.waitForFunction(() => testView.graphData().links.every(l => l.kind === 'project' || l.w >= 0.6));
  await page.locator('#gv-areas').uncheck();
  await page.locator('#gv-labels [data-v="more"]').click();
  await page.locator('#gv-relayout').click();
  await page.waitForTimeout(600);

  await page.emulateMedia({ colorScheme: 'dark' });
  await page.waitForTimeout(300);
  assert(await page.evaluate(() => testView.backgroundColor() === getComputedStyle(document.querySelector('#graph')).getPropertyValue('--color-surface').trim()));
  await page.locator('#g-full').click();
  await page.waitForTimeout(300);
  assert.equal(await page.locator('#g-full').getAttribute('aria-pressed'), 'true');
  await page.locator('#g-full').click();
  await page.locator('.nav a[data-tab="overview"]').click();
  await page.locator('section[data-tab="graph"]').waitFor({ state: 'hidden' });
  const frame = await page.evaluate(() => testView.renderer().info.render.frame);
  await page.waitForTimeout(250);
  assert.equal(await page.evaluate(() => testView.renderer().info.render.frame), frame, 'hidden tab pauses rendering');
  await page.locator('.nav a[data-tab="graph"]').click();
  await page.waitForTimeout(200);
  assert((await page.evaluate(() => testView.renderer().info.render.frame)) > frame);

  await page.setViewportSize({ width: 390, height: 844 });
  await search(page, 'Запись 01');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  const mobile = await page.evaluate(() => {
    const node = graph.selected(), point = testView.graph2ScreenCoords(node.x, node.y, node.z);
    return { y: document.querySelector('#graph').getBoundingClientRect().y + point.y,
      panelTop: document.querySelector('#graph-panel').getBoundingClientRect().top };
  });
  assert(mobile.y < mobile.panelTop, 'mobile panel leaves selected node visible');

  const reduced = await open({ reduced: true });
  await reduced.waitForFunction(() => globalThis.testView?.graphData().nodes.length === 54);
  assert.equal(await reduced.evaluate(() => testView.controls().enableDamping), false);
  assert.equal(await reduced.evaluate(() => testView.cooldownTicks()), 0);
  await search(reduced, 'Запись 09');
  assert.equal(await reduced.evaluate(() => graph.selected().id), 9);
  await reduced.reload();
  await reduced.waitForFunction(() => graph?.selected()?.id === 9);
  assert(await reduced.evaluate(() => graph.selected().z !== 0));

  const fallback = await open({ noWebGL: true });
  await fallback.waitForFunction(() => document.querySelector('#graph-help').textContent.includes('2D'));
  assert.match(await fallback.locator('#graph-help').innerText(), /2D/);
  await search(fallback, 'Запись 01');
  assert.equal(await fallback.evaluate(() => graph.selected().id), 1);
  const home = await open();
  await home.locator('.nav a[data-tab="overview"]').click();
  await home.locator('#q-global').fill('Запись 05');
  await home.locator('#q-global').press('Enter');
  await home.locator('.drawer').waitFor();
  // Record card: section tabs, typed knowledge, artifacts, turns, prev/next.
  assert(await home.locator('.dr-tabs button').count() >= 2, 'record card has section tabs');
  await home.locator('.dr-tabs button[data-sec="talk"]').click();
  await home.waitForFunction(() => document.querySelector('.dr-tabs button[data-sec="talk"]').getAttribute('aria-current') === 'true');
  const found = await home.evaluate(() => artifacts({ location: 'https://example.com/src', atoms: ['[command] Запусти `brain sync` после правок'],
    preview: 'См. https://example.com/a/b. Источник https://example.com/src, файл src/app.ts и Next.js.\n[вложения: Договор от 02.11.pdf]\nСобери: `npm run build`' }));
  assert.deepEqual(found.links.map(l => l[0]), ['https://example.com/a/b'], 'links: trailing dot dropped, source link not repeated');
  assert.deepEqual(found.files, ['Договор от 02.11.pdf', 'src/app.ts'], 'files: attachments and paths, not library names');
  assert.deepEqual(found.commands, ['brain sync', 'npm run build']);
  const talk = await home.evaluate(() => turns({ preview: 'Пользователь: привет\n\nкак дела\n\nClaude: хорошо' }));
  assert.deepEqual(talk.map(t => [t.who, t.text]), [['Пользователь', 'привет\n\nкак дела'], ['Claude', 'хорошо']]);
  await home.keyboard.press('Escape');
  await home.locator('#fresh button[data-id]').first().click();
  const first = await home.locator('.drawer').getAttribute('data-id');
  await home.keyboard.press('ArrowRight');
  await home.waitForFunction((id) => document.querySelector('.drawer').dataset.id !== id, first);
  await home.keyboard.press('ArrowLeft');
  await home.waitForFunction((id) => document.querySelector('.drawer').dataset.id === id, first);
  await home.keyboard.press('Escape');
  assert.equal(await home.locator('#week-projects .wrow').count() > 0, true, 'home lists projects active this week');
  // Search: knowledge and word forms, snippet with the match, filters, recent queries.
  await home.locator('#q-global').fill('субтитров');
  const top = home.locator('#q-results li.k-record').first();
  assert.match(await top.locator('.t').innerText(), /Запись 07/, 'a word form finds the record by its knowledge');
  assert.equal(await top.locator('.sn mark').first().innerText(), 'Субтитры', 'the snippet highlights the match');
  assert.match(await top.locator('.sn .tag').innerText(), /решение/i);
  await home.locator('#q-filters button[data-k="type"][data-v="todo"]').click();
  assert.equal(await home.locator('#q-results li.k-record').count(), 0, 'knowledge type narrows the search');
  await home.locator('#q-results [data-reset]').click();
  await home.locator('#q-global').fill('шрифт');
  await home.locator('#q-filters button[data-k="type"][data-v="todo"]').click();
  assert.match(await home.locator('#q-results li.k-record .sn').first().innerText(), /шрифт на телефоне/);
  await home.locator('#q-filters button[data-k="days"][data-v="30"]').click();
  await home.locator('#q-global').press('Enter');
  await home.locator('.drawer').waitFor();
  await home.keyboard.press('Escape');
  assert.deepEqual(await home.locator('#recent [data-q]').allInnerTexts(), ['шрифт', 'Запись 05'], 'picked queries are remembered');
  assert.deepEqual(await home.evaluate(() => [terms('Субтитры субтитров РИЛС ёлки'), hl('Ёлки и субтитрами', terms('елки субтитры'))]),
    [['субтитр', 'субтитр', 'рилс', 'елки'], '<mark>Ёлки</mark> и <mark>субтитрами</mark>']);
  // Topics: tags are links, a topic has its own page, related projects explain the link.
  await home.locator('.nav a[data-tab="topics"]').click();
  await home.locator('#tgrid .trow').first().waitFor();
  assert.equal(await home.locator('#tgrid .trow').count(), 2, 'topics list');
  await home.locator('#tgrid .trow').first().click();
  await home.locator('#topic-view h1').waitFor();
  assert.match(await home.locator('#topic-view .lede').innerText(), /48 записей в 6 проектах/);
  assert.equal(await home.locator('#topic-view .feed .rrow').count(), 48);
  assert.deepEqual(await home.evaluate(() => sharedTopics(byId.get(8).pkey, byId.get(9).pkey)), ['граф', 'камера'], 'shared topics explain a project link');
  assert.equal(await home.locator('#topic-view .wrow').count(), 6, 'topic page lists its projects');
  await home.evaluate(() => go('#project/' + encodeURIComponent(byId.get(8).pkey)));
  await home.locator('#project-view .rel li').first().waitFor();
  assert.match(await home.locator('#project-view .rel li').first().innerText(), /1 связь/);
  await home.locator('#project-view .rel a.tag.topic').first().click();
  await home.locator('#topic-view [data-gfind]').click();
  await home.waitForFunction(() => location.hash === '#graph' && document.querySelector('#g-q')?.value && /запис/.test(document.querySelector('#g-found').textContent));
  // Knowledge tab: every atom in one feed, narrowed by type and words, copied as Markdown.
  await home.locator('.nav a[data-tab="knowledge"]').click();
  await home.locator('#k-feed .kgroup').waitFor();
  assert.equal(await home.locator('#k-feed .klist li').count(), 2, 'knowledge lists every atom');
  await home.locator('#k-type button[data-v="todo"]').click();
  assert.deepEqual(await home.locator('#k-feed .klist .x').allInnerTexts(), ['Проверить шрифт на телефоне']);
  await home.locator('#q-knowledge').fill('субтитров');
  assert.match(await home.locator('#k-feed').innerText(), /Ничего не нашлось/);
  await home.locator('#k-type button[data-v="all"]').click();
  assert.equal(await home.locator('#k-feed .klist mark').innerText(), 'Субтитры');
  assert.match(await home.evaluate(() => knowledgeMarkdown()), /^# Знания · «субтитров»\n\n## Запись 07\n.*\n\n- \*\*решение:\*\* Субтитры вшиваем/);
  await home.locator('#k-feed .khead').click();
  await home.locator('.drawer').waitFor();
  await home.keyboard.press('Escape');
  await home.locator('.nav a[data-tab="projects"]').click();
  await home.locator('#h-projects').waitFor();
  await home.keyboard.press('/');
  await home.locator('.palette #q-global').waitFor();
  await home.keyboard.press('Escape');
  assert.equal(await home.locator('.palette').count(), 0, 'Esc closes the search palette');
  const empty = await open({ empty: true });
  assert.match(await empty.locator('.graph-empty').innerText(), /пустой/);
  assert.deepEqual(failures, [], 'no browser errors');
  console.log('PASS: offline 3D, rotation, zoom, selection, cards, filters, local mode, labels, theme, fullscreen, pause, mobile, reduced motion, deep links, 2D fallback, empty state');
} finally {
  await browser.close();
  await rm(out, { recursive: true, force: true });
}
