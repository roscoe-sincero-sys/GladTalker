// End-to-end test of the *desktop app* (Windows): launches the built GladTalker Studio executable,
// attaches Playwright to its WebView2 over the Chrome DevTools Protocol, and drives the real
// engine the app starts. Run with `npm run e2e:desktop` after `npm run tauri:build`
// (see playwright.desktop.config.js).
import { test, expect, chromium } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const require = createRequire(import.meta.url);
const AXE_SOURCE = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf-8');
const repo = path.resolve(import.meta.dirname, '../..');
const PORTRAIT = path.join(repo, 'examples', 'source_image', 'people_0.png');
const EXE = process.env.GLADTALKER_DESKTOP_EXE
  || path.join(repo, 'studio-ui', 'src-tauri', 'target', 'release', 'gladtalker-studio.exe');
const CDP_PORT = Number(process.env.GLADTALKER_DESKTOP_CDP_PORT || 9333);

test.skip(process.platform !== 'win32', 'the desktop end-to-end test drives WebView2 (Windows)');
test.skip(!fs.existsSync(EXE), `build the app first (npm run tauri:build); no ${EXE}`);

/** Engine processes started by the app with this PID (their --exit-with-parent argument). */
function enginesOf(appPid) {
  // Only Python processes: this PowerShell command's own command line contains the same words.
  const out = execFileSync('powershell', ['-NoProfile', '-Command',
    `Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" | Where-Object { $_.CommandLine -match 'studio_server.py' -and $_.CommandLine -match '--exit-with-parent ${appPid}\\b' } | ForEach-Object { $_.ProcessId }`],
  { encoding: 'utf-8' });
  return out.split(/\s+/).filter(Boolean).map(Number);
}

async function waitFor(check, timeoutMs, what) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = await check();
    if (value) return value;
    if (Date.now() > deadline) throw new Error(`Timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 500));
  }
}

/** Drops the DevTools connection without waiting on a browser that is already gone. */
async function disconnect(browser) {
  await Promise.race([browser.close().catch(() => {}), new Promise((r) => setTimeout(r, 5_000))]);
}

async function launchApp() {
  const app = spawn(EXE, [], {
    env: { ...process.env, GLADTALKER_HOME: repo,
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${CDP_PORT}` },
    stdio: 'ignore',
  });
  const browser = await waitFor(async () => {
    try {
      return await chromium.connectOverCDP(`http://127.0.0.1:${CDP_PORT}`);
    } catch {
      return null;
    }
  }, 60_000, 'the app’s WebView2 to accept a DevTools connection');
  const page = await waitFor(async () => browser.contexts()[0]?.pages().find((p) => !p.url().startsWith('devtools')), 30_000, 'the Studio page');
  page.setDefaultTimeout(60_000);
  page.on('dialog', (dialog) => dialog.accept()); // "Start a new project?"
  return { app, browser, page };
}

test.describe.configure({ mode: 'serial' });

test('the desktop app starts its engine, works end to end, and stops the engine when closed', async () => {
  test.setTimeout(6 * 60_000);
  const { app, browser, page } = await launchApp();
  try {
    // The shell found GladTalker (GLADTALKER_HOME), started the engine, and handed over its port and token.
    await expect(page.getByRole('heading', { name: 'Step 1 of 3: Cast' })).toBeVisible({ timeout: 180_000 });
    await expect(page.getByText(/Computer: (?!Detecting)/)).toBeVisible();
    // The app keeps its autosaved project between runs (like a browser profile): start fresh.
    await page.getByRole('button', { name: 'New', exact: true }).click();
    const engines = await waitFor(() => enginesOf(app.pid), 10_000, 'the engine process');
    expect(engines.length).toBeGreaterThan(0);

    // No WCAG 2.2 A/AA violations in the desktop window. (Evaluated over CDP, which the page's
    // Content-Security-Policy doesn't apply to; the policy itself stays on.)
    const violations = await page.evaluate(async (src) => {
      (0, eval)(src); // eslint-disable-line no-eval
      const res = await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] } });
      return res.violations.map((v) => `${v.id}: ${v.help}`);
    }, AXE_SOURCE);
    expect(violations).toEqual([]);

    // Upload, detect, script, and a real full-mix preview: authenticated requests, the shown
    // scene (a blob: URL), and job progress, all from the app's own origin to the engine's port.
    await page.getByLabel('Scene image').setInputFiles(PORTRAIT);
    const scene = page.getByRole('img', { name: /Scene: people_0\.png/ });
    await expect(scene).toBeVisible();
    expect(await scene.evaluate((img) => img.src.startsWith('blob:') && img.naturalWidth > 0)).toBe(true);
    await page.getByRole('button', { name: /Auto-Detect Cast/ }).click();
    await page.getByLabel('Name', { exact: true }).fill('Ann');
    await page.getByRole('button', { name: 'Next: Script' }).click();
    await page.getByLabel('Paste a script').fill('Ann: Hello from the desktop app.');
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    await page.getByRole('button', { name: /Preview full audio mix/ }).click();
    await expect(page.getByLabel('Full audio mix')).toBeVisible({ timeout: 120_000 });
    await page.getByRole('button', { name: 'Next: Roll' }).click();
    await expect(page.getByText(/Everything is ready: 1 cast member, 1 line/)).toBeVisible();
  } finally {
    // Close the window the normal way (WM_CLOSE): the app stops its engine on exit. The app goes
    // first: closing a DevTools connection to WebView2 can wait on the app's own browser process.
    try { execFileSync('taskkill', ['/PID', String(app.pid)], { stdio: 'ignore' }); } catch { /* already gone */ }
    await waitFor(() => app.exitCode !== null, 30_000, 'the app to exit');
    await disconnect(browser);
  }
  await waitFor(() => enginesOf(app.pid).length === 0, 30_000, 'the engine to stop after the app closed');
});

test('if the app crashes, its engine still shuts itself down', async () => {
  test.setTimeout(4 * 60_000);
  const { app, browser, page } = await launchApp();
  await expect(page.getByRole('heading', { name: 'Step 1 of 3: Cast' })).toBeVisible({ timeout: 180_000 });
  const pid = app.pid;
  expect(enginesOf(pid).length).toBeGreaterThan(0);
  execFileSync('taskkill', ['/PID', String(pid), '/F'], { stdio: 'ignore' }); // no chance to clean up
  await disconnect(browser);
  await waitFor(() => enginesOf(pid).length === 0, 60_000, 'the orphaned engine to notice and exit');
});
