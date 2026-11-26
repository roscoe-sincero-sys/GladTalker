// Setup while a model file is busy (being replaced or locked): keep checking, never offer a repair.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/svelte';

const calls = vi.hoisted(() => ({ models: [] }));

vi.mock('../src/lib/api.js', async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    api: {
      models: () => {
        const next = calls.models.shift();
        return next instanceof Error ? Promise.reject(next) : Promise.resolve(next);
      },
    },
  };
});

const { engine, pollBusyModels } = await import('../src/lib/app-state.svelte.js');
const SetupPanel = (await import('../src/components/SetupPanel.svelte')).default;

const file = (name, extra = {}) => ({ path: `w/${name}`, name, ready: false, busy: false, damaged: false, incomplete: false, ...extra });
const busyStatus = () => ({ complete: false, ready: 1, busy: 1, total: 2,
  files: [file('a.pth', { ready: true }), file('b.pth', { busy: true })] });
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

  it('Setup lists busy files as being checked, without a download button', async () => {
    render(SetupPanel);
    expect(screen.getByText(/Checking again shortly/)).toBeTruthy();
    expect(screen.getByText('b.pth')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Download models/ })).toBeNull();
    expect(screen.queryByText(/need downloading/)).toBeNull();
  });
});
