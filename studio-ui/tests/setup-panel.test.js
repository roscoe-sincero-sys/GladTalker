// Setup while a model file is busy (being replaced or locked): keep checking, never offer a repair.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/svelte';

const calls = vi.hoisted(() => ({ models: [], hardwareModels: null }));

vi.mock('../src/lib/api.js', async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    api: {
      models: () => {
        const next = calls.models.shift();
        if (next instanceof Error) return Promise.reject(next);
        return next?.promise ?? Promise.resolve(next);
      },
      health: async () => ({ version: 'test' }),
      hardware: async () => ({ ffmpeg: { available: true }, models: calls.hardwareModels }),
      voices: async () => ({ presets: [], semitone_range: [-6, 6] }),
    },
  };
});

const { engine, connectEngine, pollBusyModels, refreshModels } = await import('../src/lib/app-state.svelte.js');
const SetupPanel = (await import('../src/components/SetupPanel.svelte')).default;

const file = (name, extra = {}) => ({ path: `w/${name}`, name, ready: false, busy: false, damaged: false, incomplete: false, ...extra });
const busyStatus = () => ({ complete: false, ready: 1, busy: 1, total: 2,
  files: [file('a.pth', { ready: true }), file('b.pth', { busy: true })] });
const missingStatus = () => ({ complete: false, ready: 1, busy: 0, total: 2,
  files: [file('a.pth', { ready: true }), file('b.pth')] });
/** A models() answer the test releases later, like a slow request. */
function held() {
  let release;
  const promise = new Promise((resolve) => { release = resolve; });
  return { promise, release };
}
const readyStatus = () => ({ complete: true, ready: 2, busy: 0, total: 2,
  files: [file('a.pth', { ready: true }), file('b.pth', { ready: true })] });

describe('busy models', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    engine.hardware = { ffmpeg: { available: true, version: '7' } };
    engine.models = busyStatus();
    calls.models = [];
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('keeps polling after a failed check until the file is ready', async () => {
    calls.models = [new Error('engine busy'), busyStatus(), readyStatus()];
    const poller = pollBusyModels({ delayMs: 1000 });
    await vi.advanceTimersByTimeAsync(1000); // fails: last known state kept
    expect(engine.models.busy).toBe(1);
    await vi.advanceTimersByTimeAsync(1000); // still busy
    expect(engine.models.busy).toBe(1);
    await vi.advanceTimersByTimeAsync(1000); // ready
    expect(engine.models.complete).toBe(true);
    await vi.advanceTimersByTimeAsync(5000);
    expect(calls.models).toEqual([]); // stopped by itself, no extra requests
    poller.stop();
  });

  it('runs one check at a time and stops on request', async () => {
    let resolve;
    let requests = 0;
    const { api } = await import('../src/lib/api.js');
    const spy = vi.spyOn(api, 'models').mockImplementation(() => {
      requests += 1;
      return new Promise((r) => { resolve = r; });
    });
    const poller = pollBusyModels({ delayMs: 1000 });
    await vi.advanceTimersByTimeAsync(1000);
    poller.poke();
    await vi.advanceTimersByTimeAsync(5000);
    expect(requests).toBe(1); // the first check is still pending
    poller.stop();
    resolve(busyStatus());
    await vi.advanceTimersByTimeAsync(5000);
    expect(requests).toBe(1);
    spy.mockRestore();
  });

  it('an earlier poll answering after the download finished does not bring Setup back', async () => {
    const early = held();
    calls.models = [early, readyStatus()];
    const poller = pollBusyModels({ delayMs: 1000 });
    await vi.advanceTimersByTimeAsync(1000); // the poll is now waiting on a slow answer
    await refreshModels(); // the download job finished: everything is ready
    expect(engine.models.complete).toBe(true);
    early.release(missingStatus()); // the stale answer arrives last
    await vi.advanceTimersByTimeAsync(0);
    expect(engine.models.complete).toBe(true);
    poller.stop();
  });

  it('drops the answer to a poll that was stopped (Setup closed) while it was pending', async () => {
    const early = held();
    calls.models = [early];
    const poller = pollBusyModels({ delayMs: 1000 });
    await vi.advanceTimersByTimeAsync(1000);
    poller.stop();
    early.release(missingStatus());
    await vi.advanceTimersByTimeAsync(0);
    expect(engine.models).toEqual(busyStatus());
  });

  it('drops answers from before a reconnect', async () => {
    const stale = held();
    calls.models = [stale];
    const pending = refreshModels();
    calls.hardwareModels = readyStatus();
    await connectEngine();
    expect(engine.models.complete).toBe(true);
    stale.release(missingStatus());
    await pending;
    expect(engine.models.complete).toBe(true);
  });

  it('Setup lists busy files as being checked, without a download button', async () => {
    render(SetupPanel);
    expect(screen.getByText(/Checking again shortly/)).toBeTruthy();
    expect(screen.getByText('b.pth')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Download models/ })).toBeNull();
    expect(screen.queryByText(/need downloading/)).toBeNull();
  });
});
