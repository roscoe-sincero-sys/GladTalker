// End-to-end test of the *desktop app* (Windows): launches the built GladTalker Studio executable,
// attaches Playwright to its WebView2 over the Chrome DevTools Protocol, and drives the real
// engine the app starts. Run with `npm run e2e:desktop` after `npm run tauri:build`
// (see playwright.desktop.config.js).
//
// Every launch is fully isolated from the user's own GladTalker Studio: its own WebView2 profile
// (so the autosaved project is a test one), its own settings and log folder, its own engine
// workspace, and a free DevTools port. Running these tests never touches a real draft.
import { test, expect, chromium } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const AXE_SOURCE = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf-8');
const repo = path.resolve(import.meta.dirname, '../..');
const PORTRAIT = path.join(repo, 'examples', 'source_image', 'people_0.png');
const EXE = process.env.GLADTALKER_DESKTOP_EXE
  || path.join(repo, 'studio-ui', 'src-tauri', 'target', 'release', 'gladtalker-studio.exe');

test.skip(process.platform !== 'win32', 'the desktop end-to-end test drives WebView2 (Windows)');
test.skip(!fs.existsSync(EXE), `build the app first (npm run tauri:build); no ${EXE}`);

/** Engine processes started by the app with this PID (their --exit-with-parent argument). */
function enginesOf(appPid) {
  return engineCommandLines(appPid).map((line) => Number(line.split(' ', 1)[0]));
}

/** "PID commandline" for each of the app's engine processes. */
function engineCommandLines(appPid) {
  // Only Python processes: this PowerShell command's own command line contains the same words.
  const out = execFileSync('powershell', ['-NoProfile', '-Command',
    `Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" | Where-Object { $_.CommandLine -match 'studio_server.py' -and $_.CommandLine -match '--exit-with-parent ${appPid}\\b' } | ForEach-Object { "$($_.ProcessId) $($_.CommandLine)" }`],
  { encoding: 'utf-8' });
  return out.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
}

/** The value after `flag` in a Windows command line: quoted if it has spaces (as Rust quotes it;
 * the paths here contain no quote characters), bare otherwise. */
function argAfter(commandLine, flag) {
  const m = commandLine.match(new RegExp(String.raw`(?:^|\s)${flag}\s+(?:"([^"]*)"|(\S+))`));
  return m ? (m[1] ?? m[2]) : null;
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

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

/** A throwaway profile: WebView2 data (localStorage, so the autosaved project), the app's
 * settings and logs, and the engine workspace, all under one temporary folder. */
// The names have a space on purpose: paths with spaces (say, a user name with one) must work.
function newProfile(name = 'gladtalker desktop-') {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), name));
  return { root, webview: path.join(root, 'webview2'), data: path.join(root, 'app-data'), workspace: path.join(root, 'workspace') };
}

async function launchApp(profile) {
  const port = await freePort();
  const app = spawn(EXE, [], {
    env: { ...process.env, GLADTALKER_HOME: repo,
      WEBVIEW2_USER_DATA_FOLDER: profile.webview,
      GLADTALKER_STUDIO_DATA_DIR: profile.data,
      // Relative on purpose: the app runs in the profile folder and the engine in GladTalker's, so
      // the app must resolve it once (to profile.workspace) for both.
      GLADTALKER_STUDIO_WORKSPACE: path.relative(profile.root, profile.workspace),
      // A web-mode bind address the desktop engine must not inherit (it always uses 127.0.0.1).
      GLADTALKER_STUDIO_HOST: '::1',
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port}` },
    cwd: profile.root,
    stdio: 'ignore',
  });
  const browser = await waitFor(async () => {
    try {
      return await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
    } catch {
      return null;
    }
  }, 60_000, 'the app’s WebView2 to accept a DevTools connection');
  const page = await waitFor(async () => browser.contexts()[0]?.pages().find((p) => !p.url().startsWith('devtools')), 30_000, 'the Studio page');
  page.setDefaultTimeout(60_000);
  return { app, browser, page };
}

/** Closes the window the normal way (WM_CLOSE) and waits for the app to exit. */
async function closeApp({ app, browser }) {
  // The app goes first: closing a DevTools connection to WebView2 can wait on its browser process.
  try { execFileSync('taskkill', ['/PID', String(app.pid)], { stdio: 'ignore' }); } catch { /* already gone */ }
  await waitFor(() => app.exitCode !== null, 30_000, 'the app to exit');
  await disconnect(browser);
}

/** A new profile holds nothing, or the empty project the app autosaves on its first start. */
const isEmptyProject = (p) => !p || (!p.image && p.cast.length === 0 && p.script.length === 0);

const savedDraft = (page) => page.evaluate(() => JSON.parse(localStorage.getItem('gladtalker.project.v1') || 'null'));

test.describe.configure({ mode: 'serial' });

test('the desktop app starts its engine, works end to end, and stops the engine when closed', async () => {
  test.setTimeout(6 * 60_000);
  const profile = newProfile();
  const launched = await launchApp(profile);
  const { app, page } = launched;
  try {
    // The shell found GladTalker (GLADTALKER_HOME), started the engine on 127.0.0.1 even with
    // GLADTALKER_STUDIO_HOST=::1 inherited, and handed over its port and token.
    await expect(page.getByRole('heading', { name: 'Step 1 of 3: Cast' })).toBeVisible({ timeout: 180_000 });
    await expect(page.getByText(/Computer: (?!Detecting)/)).toBeVisible();
    expect(isEmptyProject(await savedDraft(page))).toBe(true); // a brand-new profile: nobody's draft
    const engines = await waitFor(() => enginesOf(app.pid), 10_000, 'the engine process');
    expect(engines.length).toBeGreaterThan(0);
    // The token travels in the engine's environment, never on its command line (which any
    // program on the computer can list).
    for (const line of engineCommandLines(app.pid)) {
      expect(line).not.toMatch(/--token/);
      expect(line).toContain('--host 127.0.0.1');
      expect(argAfter(line, '--workspace')).toBe(profile.workspace); // the test's own, made absolute
    }
    // The shell checks "Show in folder" against that same absolute workspace.
    const status = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('start_engine'));
    expect(status.state).toBe('ready');
    expect(status.workspace).toBe(profile.workspace);
    await expect(page.evaluate(() => window.__TAURI_INTERNALS__.invoke('reveal_file', { path: 'C:\\Windows\\win.ini' })))
      .rejects.toThrow(/Only files that GladTalker Studio made/);

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
    await closeApp(launched); // the app stops its engine on exit
  }
  await waitFor(() => enginesOf(app.pid).length === 0, 30_000, 'the engine to stop after the app closed');
  expect(fs.existsSync(path.join(profile.data, 'logs', 'engine.log'))).toBe(true); // logs went to the test's folder
  fs.rmSync(profile.root, { recursive: true, force: true });
});

test('a test run never touches another profile’s autosaved draft', async () => {
  test.setTimeout(6 * 60_000);
  // A "user" profile with a draft in it.
  const user = newProfile('gladtalker user-');
  let launched = await launchApp(user);
  await expect(launched.page.getByText(/Computer: (?!Detecting)/)).toBeVisible({ timeout: 180_000 });
  await launched.page.getByRole('button', { name: 'Next: Script' }).click();
  await launched.page.getByRole('button', { name: '+ Add line' }).click();
  await launched.page.getByLabel('What is said in line 1').fill('My unfinished draft.');
  await expect.poll(async () => (await savedDraft(launched.page))?.script?.[0]?.text).toBe('My unfinished draft.');
  await closeApp(launched);

  // A test run in its own profile, starting a new project there.
  const testRun = newProfile();
  launched = await launchApp(testRun);
  await expect(launched.page.getByText(/Computer: (?!Detecting)/)).toBeVisible({ timeout: 180_000 });
  expect(isEmptyProject(await savedDraft(launched.page))).toBe(true);
  await launched.page.getByRole('button', { name: 'New', exact: true }).click();
  await closeApp(launched);

  // The user's draft is exactly as it was.
  launched = await launchApp(user);
  await expect(launched.page.getByText(/Computer: (?!Detecting)/)).toBeVisible({ timeout: 180_000 });
  expect((await savedDraft(launched.page)).script.map((l) => l.text)).toEqual(['My unfinished draft.']);
  await closeApp(launched);
  for (const p of [user, testRun]) fs.rmSync(p.root, { recursive: true, force: true });
});

test('if the app crashes, its engine still shuts itself down', async () => {
  test.setTimeout(4 * 60_000);
  const profile = newProfile();
  const { app, browser, page } = await launchApp(profile);
  await expect(page.getByRole('heading', { name: 'Step 1 of 3: Cast' })).toBeVisible({ timeout: 180_000 });
  const pid = app.pid;
  expect(enginesOf(pid).length).toBeGreaterThan(0);
  execFileSync('taskkill', ['/PID', String(pid), '/F'], { stdio: 'ignore' }); // no chance to clean up
  await disconnect(browser);
  await waitFor(() => enginesOf(pid).length === 0, 60_000, 'the orphaned engine to notice and exit');
  try { fs.rmSync(profile.root, { recursive: true, force: true }); } catch { /* WebView2 may still be letting go */ }
});

/** What Windows UI Automation (Narrator, NVDA, JAWS) exposes for the app's window. */
function uiaTree(appPid) {
  const out = execFileSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
    path.join(import.meta.dirname, 'uia-tree.ps1'), '-AppPid', String(appPid)], { encoding: 'utf-8' });
  const parsed = JSON.parse(out.trim() || '[]');
  return Array.isArray(parsed) ? parsed : [parsed];
}

test('screen readers get named controls, headings, and landmarks through UI Automation (WCAG2ICT 4.1.2, 1.3.1, 2.4.2)', async () => {
  test.setTimeout(6 * 60_000);
  const profile = newProfile();
  const launched = await launchApp(profile);
  const { app, page } = launched;
  try {
    await expect(page.getByText(/Computer: (?!Detecting)/)).toBeVisible({ timeout: 180_000 });
    await page.getByLabel('Scene image').setInputFiles(PORTRAIT);
    await page.getByRole('button', { name: /Auto-Detect Cast/ }).click();
    await expect(page.getByRole('group', { name: /Cast member 1/ })).toBeVisible({ timeout: 60_000 });

    // Chromium builds its UI Automation tree when a client first asks: wait until it is complete.
    let tree = [];
    const has = (localized, name) => tree.some((e) => e.localized === localized && (name instanceof RegExp ? name.test(e.name) : e.name === name));
    await waitFor(() => {
      tree = uiaTree(app.pid);
      return has('heading', 'Step 1 of 3: Cast') && has('button', /Auto-Detect Cast/);
    }, 60_000, 'the UI Automation tree');

    // The window's document is named after the app (2.4.2); the skip link, the step heading,
    // and the landmarks are there (2.4.1, 1.3.1).
    expect(has('document', 'GladTalker Studio')).toBe(true);
    expect(has('link', 'Skip to the current step')).toBe(true);
    expect(has('navigation', 'Steps')).toBe(true);
    expect(tree.some((e) => e.localized === 'main')).toBe(true);
    // The step buttons' names include their state once a step is done ("Cast, done").
    for (const name of [/^Cast/, /^Script/, /^Roll/, /^Help$/, /^Next: Script/]) expect(has('button', name), String(name)).toBe(true);
    // Cast member fields keep their visible labels as names (2.5.3, 3.3.2).
    expect(tree.some((e) => e.type === 'Edit' && e.name === 'Name')).toBe(true);
    // Every control a keyboard user can reach has a name (4.1.2).
    const CONTROLS = new Set(['Button', 'Edit', 'ComboBox', 'CheckBox', 'RadioButton', 'Hyperlink', 'Spinner', 'Slider', 'List', 'ListItem']);
    const unnamed = tree.filter((e) => e.focusable && e.enabled && CONTROLS.has(e.type) && !String(e.name || '').trim());
    expect(unnamed, 'focusable controls without a name').toEqual([]);
  } finally {
    await closeApp(launched);
  }
  fs.rmSync(profile.root, { recursive: true, force: true });
});
