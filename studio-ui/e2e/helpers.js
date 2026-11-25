// Shared end-to-end helpers: opening the Studio against the real engine, casting a portrait,
// axe scans, and holding or faking engine requests.
import { expect } from '@playwright/test';
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);
export const AXE = require.resolve('axe-core/axe.min.js');
export const PORTRAIT = path.resolve(import.meta.dirname, '../../examples/source_image/people_0.png');
export const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

export async function axeViolations(page) {
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async (tags) => {
    const res = await window.axe.run(document, { runOnly: { type: 'tag', values: tags } });
    return res.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help,
      nodes: v.nodes.slice(0, 3).map((n) => n.target.join(' ')) }));
  }, WCAG_TAGS);
}

export async function openStudio(page) {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Step 1 of 3: Cast' })).toBeVisible();
  await expect(page.getByText(/Computer: (?!Detecting)/)).toBeVisible();
  return errors;
}

export const tokenBase = () => `http://127.0.0.1:${Number(process.env.GLADTALKER_E2E_PORT || 7863) + 1}`;

/** Holds the next request matching `pattern` until the returned release() is called;
 * `release.reached` resolves once that request has arrived and is being held. Playwright tries
 * the most recently added matching route first, so add any fallback route for the same URL
 * *before* calling this. */
export async function hold(page, pattern, answer = (route) => route.continue()) {
  let release;
  let arrived;
  const released = new Promise((r) => (release = r));
  const reached = new Promise((r) => (arrived = r));
  await page.route(pattern, async (route) => {
    arrived();
    await released;
    await answer(route);
  }, { times: 1 });
  release.reached = reached;
  return release;
}

/** A pretend render job `id`, so render bookkeeping can be tested without minutes of GPU time.
 * The WebSocket ticket is refused, so the UI follows it by polling `snapshot()`. */
export async function fakeRender(page, id, snapshot) {
  await page.route(`**/jobs/${id}/ws-ticket`, (route) => route.fulfill({ status: 503, json: { detail: 'busy' } }));
  await page.route(`**/jobs/${id}`, (route) => route.fulfill({ json: snapshot() }));
}

export const renderSnapshot = (id, state, extra = {}) => ({ id, kind: 'render', title: 'Render (Ann)', state, progress: state === 'succeeded' ? 1 : 0.2,
  message: state === 'succeeded' ? 'Done' : 'Rendering Ann: animating frame 5 of 50', stage: 'render:Ann',
  stages: ['audio', 'render:Ann', 'compose'], cancel_requested: false, eta_s: null, elapsed_s: 3, ...extra });

export async function readyToRoll(page, script = 'Ann: Hello there.') {
  await castPortrait(page);
  await page.getByRole('button', { name: 'Next: Script' }).click();
  await page.getByLabel('Paste a script').fill(script);
  await page.getByRole('button', { name: 'Import', exact: true }).click();
  await page.getByRole('button', { name: 'Next: Roll' }).click();
  await expect(page.getByText(/Everything is ready/)).toBeVisible();
}

export const SRT = ['1', '00:00:00,000 --> 00:00:02,000', 'Ann: Hello there.', ''].join('\n');
export const VTT = ['WEBVTT', '', '00:00.000 --> 00:02.000', 'Ann: Hello there.', ''].join('\n');

export const savedProject = (page) => page.evaluate(() => JSON.parse(localStorage.getItem('gladtalker.project.v1')));

export async function castPortrait(page) {
  await page.getByLabel('Scene image').setInputFiles(PORTRAIT);
  await expect(page.getByRole('img', { name: /Scene: people_0\.png/ })).toBeVisible();
  await page.getByRole('button', { name: /Auto-Detect Cast/ }).click();
  await expect(page.getByRole('group', { name: /Cast member 1/ })).toBeVisible();
  const name = page.getByLabel('Name', { exact: true });
  await name.fill('Ann');
}
