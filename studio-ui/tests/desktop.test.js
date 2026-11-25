// The desktop app's side of the UI, with a scripted shell (host.invoke) instead of Tauri.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import axe from 'axe-core';
import { host, isDesktop } from '../src/lib/desktop.js';
import { api } from '../src/lib/api.js';
import { engine, connectEngine, chooseEngineFolder } from '../src/lib/app-state.svelte.js';
import { project, replaceProject, defaultProject } from '../src/lib/project.svelte.js';
import { startRenderMonitor } from '../src/lib/render-monitor.svelte.js';
import EngineSetup from '../src/components/EngineSetup.svelte';

const READY = { state: 'ready', base: 'http://127.0.0.1:54321', token: 'tok123', folder: 'C:/gt', python: 'C:/gt/venv/python.exe',
  workspace: 'C:/gt/results/studio' };

function shell(answers) {
  const calls = [];
  host.invoke = vi.fn(async (cmd, args) => {
    calls.push([cmd, args]);
    const answer = answers[cmd];
    return typeof answer === 'function' ? answer(args) : answer;
  });
  return calls;
}

function engineFetch() {
  const fetchMock = vi.fn(async (url) => {
    const body = url.endsWith('/health') ? { status: 'ok', version: '0.1.0' }
      : url.endsWith('/hardware') ? { device: { label: 'CPU' }, ffmpeg: { available: true }, models: { complete: true, files: [] } }
        : url.endsWith('/voices') ? { presets: [], semitone_range: [-6, 6] } : {};
    return { ok: true, status: 200, json: async () => body };
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

async function violations(container) {
  const res = await axe.run(container, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] },
    rules: { 'color-contrast': { enabled: false } } });
  return res.violations.map((v) => `${v.id}: ${v.help}`);
}

afterEach(() => {
  host.invoke = null;
  vi.unstubAllGlobals();
});

describe('starting the engine from the desktop app', () => {
  it('connects to the private port with the token the shell hands over', async () => {
    const calls = shell({ start_engine: READY });
    const fetchMock = engineFetch();
    expect(isDesktop()).toBe(true);
    await connectEngine();
    expect(calls.map((c) => c[0])).toEqual(['start_engine']);
    expect(engine.status).toBe('ready');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/^http:\/\/127\.0\.0\.1:54321\//);
    expect(init.headers.Authorization).toBe('Bearer tok123');
  });

  it('shows the setup screen when no GladTalker folder is found, and starts from the chosen one', async () => {
    const message = 'GladTalker Studio needs the GladTalker folder. Choose where it is on this computer.';
    let chosen = false;
    const calls = shell({ start_engine: () => (chosen ? READY : { state: 'not_found', message, folder: null }),
      choose_engine_folder: () => { chosen = true; return READY; } });
    engineFetch();
    await connectEngine();
    expect(engine.status).toBe('setup');

    const { container, unmount } = render(EngineSetup);
    expect(screen.getByRole('heading', { name: 'Find GladTalker' })).toBe(document.activeElement);
    expect(screen.getByText(message)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull(); // nothing to retry yet
    expect(await violations(container)).toEqual([]);

    await fireEvent.click(screen.getByRole('button', { name: 'Choose GladTalker folder…' }));
    await vi.waitFor(() => expect(engine.status).toBe('ready'));
    expect(calls.map((c) => c[0])).toEqual(['start_engine', 'choose_engine_folder', 'start_engine']);
    unmount();
  });

  it('explains a failed start with the engine’s last messages, and can restart it', async () => {
    shell({ start_engine: { state: 'failed', message: 'The engine stopped while starting (exit code: 1).', folder: 'C:/gt',
      python: 'C:/gt/venv/python.exe', log: 'ModuleNotFoundError: No module named "torch"', log_path: 'C:/logs/engine.log' },
    restart_engine: READY });
    engineFetch();
    await connectEngine();
    const { container } = render(EngineSetup);
    expect(screen.getByRole('heading', { name: 'The GladTalker engine didn’t start' })).toBeTruthy();
    expect(screen.getByLabelText('The engine’s last messages').textContent).toMatch(/No module named "torch"/);
    expect(screen.getByText('C:/logs/engine.log')).toBeTruthy();
    expect(await violations(container)).toEqual([]);
    await fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await vi.waitFor(() => expect(engine.status).toBe('ready'));
    expect(host.invoke).toHaveBeenLastCalledWith('restart_engine', {});
  });

  it('leaves things as they were when the folder dialog is cancelled', async () => {
    shell({ start_engine: { state: 'not_found', message: 'Choose it.', folder: null }, choose_engine_folder: null });
    await connectEngine();
    await chooseEngineFolder();
    expect(engine.status).toBe('setup');
    expect(engine.desktop.message).toBe('Choose it.');
  });
});

describe('render monitor (taskbar progress and notifications)', () => {
  let calls;
  let realWatch;
  const call = vi.fn(async (cmd, args) => calls.push([cmd, args]));

  beforeEach(() => {
    calls = [];
    realWatch = api.watchJob;
    replaceProject(defaultProject());
  });
  afterEach(() => {
    api.watchJob = realWatch;
  });

  function scriptedJob(events) {
    api.watchJob = (id, onEvent) => {
      for (const e of events.slice(0, -1)) onEvent(e);
      return { done: Promise.resolve(events.at(-1)), close() {} };
    };
  }

  it('shows progress while a render runs and notifies when it is ready', async () => {
    scriptedJob([{ state: 'running', progress: 0.25 }, { state: 'running', progress: 0.5 },
      { state: 'succeeded', result: { duration_s: 42, width: 1280, height: 720 } }]);
    const stop = startRenderMonitor({ call });
    project.lastRenderJobId = '0123456789ab';
    await vi.waitFor(() => expect(calls.some(([c]) => c === 'notify')).toBe(true));
    const progress = calls.filter(([c]) => c === 'set_progress').map(([, a]) => a.fraction);
    expect(progress).toEqual([null, 0.25, 0.5, null]); // cleared on attach, running, cleared at the end
    expect(calls.find(([c]) => c === 'notify')[1]).toEqual({ title: 'Your video is ready',
      body: '42 s, 1280×720. Open GladTalker Studio to watch it.' });
    stop();
  });

  it('doesn’t notify about a render that had already ended (say, after a restart), or a cancelled one', async () => {
    scriptedJob([{ state: 'succeeded', result: {} }]);
    const stop = startRenderMonitor({ call });
    project.lastRenderJobId = '0123456789ab';
    await new Promise((r) => setTimeout(r, 20));
    scriptedJob([{ state: 'running', progress: 0.1 }, { state: 'cancelled' }]);
    project.lastRenderJobId = 'ba9876543210';
    await new Promise((r) => setTimeout(r, 20));
    expect(calls.filter(([c]) => c === 'notify')).toEqual([]);
    stop();
  });
});
