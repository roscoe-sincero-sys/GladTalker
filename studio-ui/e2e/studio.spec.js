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
  const base = `http://127.0.0.1:${Number(process.env.GLADTALKER_E2E_PORT || 7863) + 1}`;
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
