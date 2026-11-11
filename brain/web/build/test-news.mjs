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
  await page.locator('#news').waitFor({ timeout: 5000 });
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

  assert.deepEqual(failures, [], 'нет ошибок в браузере');
  console.log('PASS: что нового — периоды, группы, раскрытие, запись по клику, пустая база, узкий экран, отметка просмотра, хранилище');
} finally {
  await browser.close();
  await rm(out, { recursive: true, force: true });
}
