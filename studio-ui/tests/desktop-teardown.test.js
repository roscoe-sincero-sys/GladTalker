// @vitest-environment node
// The desktop end-to-end tests' teardown (e2e-desktop/teardown.js), with a real process standing
// in for the app: its working folder is the profile, which Windows won't delete while it runs.
import { describe, it, expect, afterEach } from 'vitest';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createTeardown } from '../e2e-desktop/teardown.js';

const exited = (child) => child.exitCode !== null || child.signalCode !== null;

async function waitUntil(check, ms = 10_000) {
  const deadline = Date.now() + ms;
  while (!check()) {
    if (Date.now() > deadline) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 50));
  }
}

const spawned = [];
afterEach(() => {
  for (const child of spawned.splice(0)) if (!exited(child)) child.kill();
});

/** A stand-in app: a process holding a file in a new profile open, as WebView2 does. On Windows
 * the file is opened without delete sharing (Node itself always allows it), so the profile can't
 * be deleted while the process runs. Resolves once the file is held. */
async function launchStandIn() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'gladtalker teardown-'));
  fs.mkdirSync(path.join(root, 'webview2'));
  const held = path.join(root, 'webview2', 'Local State');
  fs.writeFileSync(held, '{}');
  const child = process.platform === 'win32'
    ? spawn('powershell', ['-NoProfile', '-Command',
      `$f = [System.IO.File]::Open('${held.replace(/'/g, "''")}', 'Open', 'Read', 'Read'); 'held'; Start-Sleep -Seconds 600`],
    { stdio: ['ignore', 'pipe', 'ignore'] })
    : spawn(process.execPath, ['-e', `require('fs').openSync(${JSON.stringify(held)}, 'r'); console.log('held'); setInterval(() => {}, 1000)`],
      { stdio: ['ignore', 'pipe', 'ignore'] });
  spawned.push(child);
  await new Promise((resolve, reject) => {
    child.stdout.on('data', (d) => { if (String(d).includes('held')) resolve(); });
    child.on('exit', () => reject(new Error('the stand-in app exited early')));
  });
  return { root, child };
}

function teardownFor(log = []) {
  const stopped = [];
  const teardown = createTeardown({
    stopApp: async ({ child }) => {
      child.kill();
      await waitUntil(() => exited(child));
      stopped.push(child.pid);
    },
    log: (message) => log.push(message),
  });
  return { teardown, stopped };
}

describe('desktop e2e teardown', () => {
  it('stops an app whose launch failed before its profile is deleted', async () => {
    const { teardown, stopped } = teardownFor();
    const { root, child } = await launchStandIn();
    teardown.profile(root);
    // launchApp registers the app right after spawning it; then the DevTools connection fails.
    const launch = async () => {
      teardown.app({ child }, root);
      throw new Error('Timed out waiting for the app’s WebView2 to accept a DevTools connection');
    };
    await expect(launch()).rejects.toThrow(/DevTools/);
    expect(exited(child)).toBe(false);

    await teardown.run();
    expect(stopped).toEqual([child.pid]);
    expect(fs.existsSync(root)).toBe(false);
  });

  it('stops an app still running after a failed assertion before its profile is deleted', async () => {
    const log = [];
    const { teardown, stopped } = teardownFor(log);
    const { root, child } = await launchStandIn();
    teardown.profile(root);
    teardown.app({ child }, root);
    expect(() => expect('a draft').toBe('another draft')).toThrow(); // the test fails before closing its app

    await teardown.run();
    expect(stopped).toEqual([child.pid]);
    expect(fs.existsSync(root)).toBe(false);
    expect(log).toEqual([]);
  });

  it.runIf(process.platform === 'win32')('needs that order on Windows: a running app’s profile can’t be deleted', async () => {
    const { root, child } = await launchStandIn();
    try {
      expect(() => fs.rmSync(root, { recursive: true, force: true })).toThrow();
    } finally {
      child.kill();
      await waitUntil(() => exited(child));
      fs.rmSync(root, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
    }
  });

  it('keeps the profile of an app it couldn’t stop, says which, and still cleans up the others', async () => {
    const log = [];
    const { root: stuck, child: stuckApp } = await launchStandIn();
    const { root: done, child: doneApp } = await launchStandIn();
    const teardown = createTeardown({
      stopApp: async ({ child }) => {
        if (child === stuckApp) throw new Error('access denied');
        child.kill();
        await waitUntil(() => exited(child));
      },
      log: (m) => log.push(m),
    });
    teardown.profile(stuck);
    teardown.profile(done);
    teardown.app({ child: stuckApp }, stuck);
    teardown.app({ child: doneApp }, done);

    await teardown.run();
    expect(fs.existsSync(path.join(stuck, 'webview2', 'Local State'))).toBe(true); // kept whole, not half deleted
    expect(fs.existsSync(done)).toBe(false);
    expect(log).toEqual([
      'Couldn’t confirm a test app stopped: access denied',
      `Kept the test profile ${path.resolve(stuck)}: an app using it may still be running (access denied). Delete it once that app has exited.`,
    ]);
    stuckApp.kill(); // this test's own cleanup of what teardown rightly kept
    await waitUntil(() => exited(stuckApp));
    fs.rmSync(stuck, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
  });

  it('keeps the profile when the app stopped but its engine can’t be confirmed gone', async () => {
    const log = [];
    const { root, child } = await launchStandIn();
    const teardown = createTeardown({
      stopApp: async (launched) => {
        launched.child.kill();
        await waitUntil(() => exited(launched.child));
        throw new Error('Timed out waiting for the app’s engine to exit'); // what stopApp reports
      },
      log: (m) => log.push(m),
    });
    teardown.profile(root);
    teardown.app({ child }, root);
    await teardown.run();
    expect(exited(child)).toBe(true);
    expect(fs.existsSync(root)).toBe(true);
    expect(log[1]).toMatch(/^Kept the test profile .*engine to exit/);
    fs.rmSync(root, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
  });

  it('keeps a profile while any of the apps that used it is unconfirmed', async () => {
    const log = [];
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'gladtalker teardown-'));
    const teardown = createTeardown({
      stopApp: async ({ fails }) => { if (fails) throw new Error('still running'); },
      log: (m) => log.push(m),
    });
    teardown.profile(root);
    teardown.app({ fails: false }, root); // the first launch closed fine
    teardown.app({ fails: true }, root); // a second launch with the same profile didn't
    await teardown.run();
    expect(fs.existsSync(root)).toBe(true);
    fs.rmSync(root, { recursive: true, force: true });
  });
});
