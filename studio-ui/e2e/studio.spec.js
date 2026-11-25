// End-to-end: the built Studio UI against a real engine (see playwright.config.js).
import { test, expect } from '@playwright/test';
import { E2E_TOKEN } from '../playwright.config.js';
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);
const AXE = require.resolve('axe-core/axe.min.js');
const PORTRAIT = path.resolve(import.meta.dirname, '../../examples/source_image/people_0.png');
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

async function axeViolations(page) {
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async (tags) => {
    const res = await window.axe.run(document, { runOnly: { type: 'tag', values: tags } });
    return res.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help,
      nodes: v.nodes.slice(0, 3).map((n) => n.target.join(' ')) }));
  }, WCAG_TAGS);
}

async function openStudio(page) {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Step 1 of 3: Cast' })).toBeVisible();
  await expect(page.getByText(/Computer: (?!Detecting)/)).toBeVisible();
  return errors;
}

const tokenBase = () => `http://127.0.0.1:${Number(process.env.GLADTALKER_E2E_PORT || 7863) + 1}`;

/** Holds the next request matching `pattern` until the returned release() is called;
 * `release.reached` resolves once that request has arrived and is being held. Playwright tries
 * the most recently added matching route first, so add any fallback route for the same URL
 * *before* calling this. */
async function hold(page, pattern, answer = (route) => route.continue()) {
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
async function fakeRender(page, id, snapshot) {
  await page.route(`**/jobs/${id}/ws-ticket`, (route) => route.fulfill({ status: 503, json: { detail: 'busy' } }));
  await page.route(`**/jobs/${id}`, (route) => route.fulfill({ json: snapshot() }));
}

const renderSnapshot = (id, state, extra = {}) => ({ id, kind: 'render', title: 'Render (Ann)', state, progress: state === 'succeeded' ? 1 : 0.2,
  message: state === 'succeeded' ? 'Done' : 'Rendering Ann: animating frame 5 of 50', stage: 'render:Ann',
  stages: ['audio', 'render:Ann', 'compose'], cancel_requested: false, eta_s: null, elapsed_s: 3, ...extra });

async function readyToRoll(page, script = 'Ann: Hello there.') {
  await castPortrait(page);
  await page.getByRole('button', { name: 'Next: Script' }).click();
  await page.getByLabel('Paste a script').fill(script);
  await page.getByRole('button', { name: 'Import', exact: true }).click();
  await page.getByRole('button', { name: 'Next: Roll' }).click();
  await expect(page.getByText(/Everything is ready/)).toBeVisible();
}

const SRT = ['1', '00:00:00,000 --> 00:00:02,000', 'Ann: Hello there.', ''].join('\n');
const VTT = ['WEBVTT', '', '00:00.000 --> 00:02.000', 'Ann: Hello there.', ''].join('\n');

const savedProject = (page) => page.evaluate(() => JSON.parse(localStorage.getItem('gladtalker.project.v1')));

async function castPortrait(page) {
  await page.getByLabel('Scene image').setInputFiles(PORTRAIT);
  await expect(page.getByRole('img', { name: /Scene: people_0\.png/ })).toBeVisible();
  await page.getByRole('button', { name: /Auto-Detect Cast/ }).click();
  await expect(page.getByRole('group', { name: /Cast member 1/ })).toBeVisible();
  const name = page.getByLabel('Name', { exact: true });
  await name.fill('Ann');
}

test.describe('axe-core scans', () => {
  // The engine's Content-Security-Policy (correctly) blocks injected scripts; only the scan needs
  // that relaxed. Every other test runs under the real policy.
  test.use({ bypassCSP: true });

  for (const scheme of ['light', 'dark']) {
  test(`no WCAG 2.2 A/AA violations on any step (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await openStudio(page);
    await castPortrait(page);
    for (const step of ['Cast', 'Script', 'Roll']) {
      await page.getByRole('navigation', { name: 'Steps' }).getByRole('button', { name: new RegExp(step) }).click();
      await expect(page.getByRole('heading', { level: 2, name: new RegExp(`Step \\d of 3: ${step}`) })).toBeFocused();
      expect(await axeViolations(page), `${step} step (${scheme})`).toEqual([]);
    }
  });
  }
});

test('cast → script → preview mix, with keyboard-only editing', async ({ page }) => {
  const errors = await openStudio(page);
  await castPortrait(page);

  // Keyboard region editing: Arrow moves 1 px, Shift+Arrow 10 px, Alt+Arrow resizes.
  const x = page.getByLabel('X', { exact: true });
  const h = page.getByLabel('H', { exact: true });
  const x0 = Number(await x.inputValue());
  const h0 = Number(await h.inputValue());
  const region = page.getByRole('button', { name: /^Region 1, Ann:/ });
  await region.focus();
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('Shift+ArrowLeft');
  await expect(x).toHaveValue(String(x0 - 11));
  await page.keyboard.press('Alt+ArrowUp');
  await expect(h).toHaveValue(String(h0 - 1));
  await expect(region).toBeFocused();

  // Script: import, fix pronunciation, reorder by keyboard.
  await page.getByRole('button', { name: 'Next: Script' }).click();
  await page.getByLabel('Paste a script').fill("Ann: Hello there.\nAnn: You're doing great.");
  await page.getByRole('button', { name: 'Import', exact: true }).click();
  await expect(page.getByText('Imported 2 lines')).toBeVisible();
  await page.getByRole('button', { name: 'Auto-Fix line 2' }).click();
  await expect(page.getByLabel('What is said in line 2')).toHaveValue('your doing great.');

  await page.getByLabel('What is said in line 1').focus();
  await page.keyboard.press('Alt+ArrowDown');
  await expect(page.getByLabel('What is said in line 2')).toHaveValue('Hello there.');
  await expect(page.getByLabel('What is said in line 2')).toBeFocused();

  // Full audio mix (real TTS) with a pacing readout.
  await page.getByRole('button', { name: /Preview full audio mix/ }).click();
  await expect(page.getByLabel('Full audio mix')).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(/Speaking pace: 1\.00× \(Natural\)/)).toBeVisible();

  // Roll: everything ready.
  await page.getByRole('button', { name: 'Next: Roll' }).click();
  await expect(page.getByText(/Everything is ready: 1 cast member, 2 lines/)).toBeVisible();
  await expect(page.getByRole('button', { name: /Render video/ })).toBeEnabled();

  // Autosave: a reload restores the project.
  await page.reload();
  await page.getByRole('navigation', { name: 'Steps' }).getByRole('button', { name: /Script/ }).click();
  await expect(page.getByLabel('What is said in line 1')).toHaveValue('your doing great.');

  expect(errors).toEqual([]);
});

test('reorder buttons keep keyboard focus at the top and bottom of the list (WCAG 2.4.3)', async ({ page }) => {
  await openStudio(page);
  await castPortrait(page);
  await page.getByRole('button', { name: 'Next: Script' }).click();
  await page.getByLabel('Paste a script').fill('Ann: One.\nAnn: Two.\nAnn: Three.');
  await page.getByRole('button', { name: 'Import', exact: true }).click();
  await expect(page.getByText('Imported 3 lines')).toBeVisible();

  // Line 2 up with Space: it becomes line 1, whose Up button is now disabled.
  await page.getByRole('button', { name: 'Move line 2 up' }).focus();
  await page.keyboard.press('Space');
  await expect(page.getByLabel('What is said in line 1')).toHaveValue('Two.');
  await expect(page.getByRole('button', { name: 'Move line 1 down' })).toBeFocused();

  // Down twice with Enter: it ends up last, and focus stays on that line (its Up button).
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: 'Move line 2 down' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByLabel('What is said in line 3')).toHaveValue('Two.');
  await expect(page.getByRole('button', { name: 'Move line 3 up' })).toBeFocused();
});

test('a slow import never lands in a project opened meanwhile', async ({ page }) => {
  await openStudio(page);
  await castPortrait(page);
  await page.getByRole('button', { name: 'Next: Script' }).click();

  let releaseParse;
  const parseHeld = new Promise((r) => (releaseParse = r));
  await page.route('**/script/parse', async (route) => {
    await parseHeld;
    await route.continue();
  });
  await page.getByLabel('Paste a script').fill('Ann: This belongs to project A.');
  await page.getByRole('button', { name: 'Import', exact: true }).click();

  // While project A's import is still waiting for the engine, open project B.
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Open…' }).click();
  await (await chooser).setFiles({ name: 'b.gladtalker.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({
    version: 1,
    cast: [{ id: 'cb', name: 'Bea', region: { x: 0, y: 0, w: 64, h: 64 } }],
    script: [{ id: 'lb', castId: 'cb', text: 'Project B line.' }],
  })) });
  await expect(page.getByRole('heading', { name: 'Step 1 of 3: Cast' })).toBeVisible();

  const parsed = page.waitForResponse('**/script/parse');
  releaseParse();
  await parsed;
  await page.waitForTimeout(700); // past the autosave delay
  await page.getByRole('navigation', { name: 'Steps' }).getByRole('button', { name: /Script/ }).click();
  await expect(page.getByLabel('What is said in line 1')).toHaveValue('Project B line.');
  await expect(page.getByLabel('What is said in line 2')).toHaveCount(0);
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('gladtalker.project.v1')));
  expect(saved.script.map((l) => l.text)).toEqual(['Project B line.']);
});

test('token mode: the documented #token= link loads the app and authenticates it', async ({ page, request }) => {
  const base = tokenBase();
  // Without the token, the shell loads but the engine's data doesn't.
  expect((await request.get(`${base}/`)).status()).toBe(200);
  expect((await request.get(`${base}/hardware`)).status()).toBe(401);

  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${base}/#token=${E2E_TOKEN}`);
  await expect(page.getByRole('heading', { name: 'Step 1 of 3: Cast' })).toBeVisible();
  await expect(page.getByText(/Computer: (?!Detecting)/)).toBeVisible();
  expect(new URL(page.url()).hash).toBe(''); // the token left the address bar
  // An upload and the authenticated image fetch (a blob: URL) both work.
  await page.getByLabel('Scene image').setInputFiles(PORTRAIT);
  const img = page.getByRole('img', { name: /Scene: people_0\.png/ });
  await expect(img).toBeVisible();
  expect(await img.evaluate((el) => el.src.startsWith('blob:') && el.naturalWidth > 0)).toBe(true);
  expect(errors).toEqual([]);
});

test('Auto-Fix suggestions disappear as soon as the line changes', async ({ page }) => {
  await openStudio(page);
  await castPortrait(page);
  await page.getByRole('button', { name: 'Next: Script' }).click();
  await page.getByLabel('Paste a script').fill("Ann: You're late.");
  await page.getByRole('button', { name: 'Import', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Auto-Fix line 1' })).toBeVisible();

  const release = await hold(page, '**/script/lint'); // the re-check of the new text is slow
  await page.getByLabel('What is said in line 1').fill('We have plenty of time.');
  await expect(page.getByRole('button', { name: 'Auto-Fix line 1' })).toHaveCount(0);
  release();
  await page.waitForTimeout(800);
  await expect(page.getByLabel('What is said in line 1')).toHaveValue('We have plenty of time.');
});

test('a render accepted after leaving Roll is still followed when Roll is shown again', async ({ page }) => {
  await openStudio(page);
  await readyToRoll(page);
  await fakeRender(page, 'fa0000000001', () => renderSnapshot('fa0000000001', 'running'));
  const release = await hold(page, '**/render/start',
    (route) => route.fulfill({ status: 202, json: renderSnapshot('fa0000000001', 'queued') }));

  await page.getByRole('button', { name: /Render video/ }).click();
  await expect(page.getByRole('button', { name: /Starting the render/ })).toBeDisabled();
  await page.getByRole('navigation', { name: 'Steps' }).getByRole('button', { name: /Cast/ }).click();
  const accepted = page.waitForResponse('**/render/start');
  release();
  await accepted;

  await page.getByRole('navigation', { name: 'Steps' }).getByRole('button', { name: /Roll/ }).click();
  await expect(page.getByText('Rendering Ann: animating frame 5 of 50')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Render video/ })).toBeDisabled(); // no duplicate GPU job
  await expect.poll(async () => (await savedProject(page)).lastRenderJobId).toBe('fa0000000001'); // autosaved
});

test('coming back to a saved render blocks a second one until its state is known', async ({ page }) => {
  await openStudio(page);
  await readyToRoll(page);
  await fakeRender(page, 'fa0000000003', () => renderSnapshot('fa0000000003', 'running'));
  await page.route('**/render/start', (route) => route.fulfill({ status: 202, json: renderSnapshot('fa0000000003', 'queued') }));
  await page.getByRole('button', { name: /Render video/ }).click();
  await expect(page.getByText('Rendering Ann: animating frame 5 of 50')).toBeVisible();
  await expect.poll(async () => (await savedProject(page)).lastRenderJobId).toBe('fa0000000003');

  // Reload with a slow engine: the first status answer for that render is held back.
  // (The fallback route stays registered first, so the one-shot hold below takes precedence.)
  const release = await hold(page, '**/jobs/fa0000000003', (route) => route.fulfill({ json: renderSnapshot('fa0000000003', 'running') }));
  let starts = 0;
  page.on('request', (r) => { if (r.url().endsWith('/render/start')) starts++; });
  await page.reload();
  await page.getByRole('navigation', { name: 'Steps' }).getByRole('button', { name: /Roll/ }).click();
  await release.reached; // the status request is now being held
  await expect(page.getByText('Checking on the last render…')).toBeVisible();
  await expect(page.getByRole('button', { name: /Render video/ })).toBeDisabled();
  release();
  await expect(page.getByText('Rendering Ann: animating frame 5 of 50')).toBeVisible();
  await expect(page.getByRole('button', { name: /Render video/ })).toBeDisabled();
  expect(starts).toBe(0);
});

test('a late Cancel acknowledgement never brings back a render that has already ended', async ({ page }) => {
  await openStudio(page);
  await readyToRoll(page);
  const id = 'fa0000000004';
  let state = 'running';
  await fakeRender(page, id, () => renderSnapshot(id, state, state === 'cancelled' ? { message: 'Cancelled.' } : {}));
  await page.route('**/render/start', (route) => route.fulfill({ status: 202, json: renderSnapshot(id, 'queued') }));
  await page.getByRole('button', { name: /Render video/ }).click();
  await expect(page.getByText('Rendering Ann: animating frame 5 of 50')).toBeVisible();

  const release = await hold(page, `**/jobs/${id}/cancel`,
    (route) => route.fulfill({ json: renderSnapshot(id, 'running', { cancel_requested: true }) }));
  await page.getByRole('button', { name: 'Cancel' }).click();
  await release.reached;
  await expect(page.getByRole('button', { name: 'Cancelling…' })).toBeDisabled();
  state = 'cancelled'; // the engine stops the render; the watcher sees it and stops polling
  await expect(page.getByRole('button', { name: 'Cancelling…' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Render video/ })).toBeEnabled();

  const acked = page.waitForResponse(`**/jobs/${id}/cancel`);
  release(); // the old "running, cancel requested" answer arrives last
  await acked;
  await page.waitForTimeout(300);
  await expect(page.getByRole('button', { name: /Render video/ })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Cancelling…' })).toHaveCount(0);
  await expect(page.getByText('Cancelled.').first()).toBeVisible();
});

test('a project file never contains a saved job id it shouldn’t follow, or lines removed after a preview', async ({ page }) => {
  await openStudio(page);
  await castPortrait(page);
  // Opening a project with a forged job reference makes no job request.
  const jobRequests = [];
  page.on('request', (r) => { if (/\/jobs\//.test(r.url())) jobRequests.push(r.url()); });
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Open…' }).click();
  await (await chooser).setFiles({ name: 'forged.gladtalker.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({
    version: 1, cast: [{ id: 'cb', name: 'Bea', region: { x: 0, y: 0, w: 64, h: 64 } }],
    script: [{ id: 'lb', castId: 'cb', text: 'Hello.' }],
    lastRenderJobId: '../models/download', lastPreviewJobId: '../hardware?x=',
  })) });
  for (const step of ['Script', 'Roll']) {
    await page.getByRole('navigation', { name: 'Steps' }).getByRole('button', { name: new RegExp(step) }).click();
  }
  await page.waitForTimeout(500);
  expect(jobRequests).toEqual([]);
  const saved = await savedProject(page);
  expect([saved.lastRenderJobId, saved.lastPreviewJobId]).toEqual([null, null]);
});

test('the preview button can’t start a second preview while one is being submitted', async ({ page }) => {
  await openStudio(page);
  await castPortrait(page);
  await page.getByRole('button', { name: 'Next: Script' }).click();
  await page.getByLabel('Paste a script').fill('Ann: Hello there.');
  await page.getByRole('button', { name: 'Import', exact: true }).click();
  let posts = 0;
  page.on('request', (r) => { if (r.method() === 'POST' && r.url().endsWith('/audio/preview')) posts++; });
  const release = await hold(page, '**/audio/preview');
  await page.getByRole('button', { name: /Preview full audio mix/ }).click();
  await expect(page.getByRole('button', { name: /Starting the preview/ })).toBeDisabled();
  release();
  await expect(page.getByLabel('Full audio mix')).toBeVisible({ timeout: 90_000 });
  expect(posts).toBe(1);

  // Edit the line, then save the project: the file has the new line only, nowhere the old one.
  await page.getByLabel('What is said in line 1').fill('A completely new line.');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Save project' }).click();
  const file = await (await download).path();
  const text = (await import('node:fs')).readFileSync(file, 'utf-8');
  expect(text).toContain('A completely new line.');
  expect(text).not.toContain('Hello there.');
  expect(JSON.parse(text).lastPreviewKey).toMatch(/^[0-9a-f]{14}$/);
});

test('token mode: a caption download failure still shows the video, and captions can be retried', async ({ page }) => {
  await page.goto(`${tokenBase()}/#token=${E2E_TOKEN}`);
  await expect(page.getByText(/Computer: (?!Detecting)/)).toBeVisible();
  await readyToRoll(page);
  const result = { video_path: 'C:/out/Ann_scene.mp4', srt_path: 'C:/out/Ann_scene.srt', vtt_path: 'C:/out/Ann_scene.vtt',
    output_dir: 'C:/out', width: 1280, height: 720, duration_s: 2, captions: [{ speaker: 'Ann', text: 'Hello there.', start_ms: 0 }] };
  await fakeRender(page, 'fa0000000002', () => renderSnapshot('fa0000000002', 'succeeded', { result }));
  await page.route('**/render/start', (route) => route.fulfill({ status: 202, json: renderSnapshot('fa0000000002', 'queued') }));
  await page.route('**/jobs/fa0000000002/files/video', (route) => route.fulfill({ body: Buffer.from('not really a video'), contentType: 'video/mp4' }));
  await page.route('**/jobs/fa0000000002/files/srt', (route) => route.fulfill({ body: SRT }));
  let vttCalls = 0;
  await page.route('**/jobs/fa0000000002/files/vtt', (route) => (++vttCalls === 1
    ? route.fulfill({ status: 503, json: { detail: 'The disk was busy.' } })
    : route.fulfill({ body: VTT, contentType: 'text/vtt' })));

  await page.getByRole('button', { name: /Render video/ }).click();
  await expect(page.getByRole('heading', { name: 'Your video' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Download video (MP4)' })).toHaveAttribute('download', 'Ann_scene.mp4');
  await expect(page.getByText(/The captions couldn’t be loaded: The disk was busy\./)).toBeVisible();
  await expect.poll(async () => (await savedProject(page)).lastRenderJobId).toBe('fa0000000002'); // autosaved

  await page.getByRole('button', { name: 'Try loading the captions again' }).click();
  await expect(page.getByRole('link', { name: 'Download captions (WebVTT)' })).toHaveAttribute('download', 'Ann_scene.vtt');
  await expect(page.getByRole('link', { name: 'Download captions (SRT)' })).toHaveAttribute('download', 'Ann_scene.srt');
  await expect(page.locator('video track[kind="captions"]')).toHaveCount(1);
  await expect(page.getByText(/captions couldn’t be loaded/)).toHaveCount(0);
});

test('help dialog: Esc closes it and focus returns to Help', async ({ page }) => {
  await openStudio(page);
  const help = page.getByRole('button', { name: /Help/ });
  await help.click();
  await expect(page.getByRole('dialog', { name: 'Help' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'Help' })).toBeHidden();
  await expect(help).toBeFocused();
});

test('reflows to 320 CSS px without horizontal scrolling (WCAG 1.4.10)', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await openStudio(page);
  await castPortrait(page);
  for (const step of ['Cast', 'Script', 'Roll']) {
    await page.getByRole('navigation', { name: 'Steps' }).getByRole('button', { name: new RegExp(step) }).click();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${step} step horizontal overflow`).toBeLessThanOrEqual(0);
  }
});

test('render a short video with captions @slow', async ({ page }) => {
  test.skip(!process.env.GLADTALKER_E2E_RENDER, 'set GLADTALKER_E2E_RENDER=1 to run a real render');
  test.setTimeout(15 * 60_000);
  await openStudio(page);
  await castPortrait(page);
  await page.getByRole('button', { name: 'Next: Script' }).click();
  await page.getByLabel('Paste a script').fill('Ann: Hello there.');
  await page.getByRole('button', { name: 'Import', exact: true }).click();
  await page.getByRole('button', { name: 'Next: Roll' }).click();
  await page.getByRole('button', { name: /Render video/ }).click();
  await expect(page.getByRole('heading', { name: 'Your video' })).toBeVisible({ timeout: 14 * 60_000 });
  await expect(page.getByRole('link', { name: 'Download video (MP4)' })).toBeVisible();
  await expect(page.locator('video track[kind="captions"]')).toHaveCount(1);
});
