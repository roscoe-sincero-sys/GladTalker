// The Script step's full-mix preview against a scripted engine: answers arrive out of order.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import { tick } from 'svelte';

const engine = vi.hoisted(() => ({ urls: {}, created: [], revoked: [], linted: [], wasRunning: null }));

vi.mock('../src/lib/api.js', async (importOriginal) => {
  const real = await importOriginal();
  const deferred = () => {
    let resolve;
    const promise = new Promise((r) => (resolve = r));
    return { promise, resolve };
  };
  const finished = (id, frames) => ({ id, state: 'succeeded', progress: 1, message: 'Done', stages: ['audio'],
    result: { duration_s: frames / 25, tempo: 1, frames, captions: [{ speaker: 'Brain', text: `take ${id}`, start_ms: 0 }] } });
  return {
    ...real,
    revokeMedia: (url) => engine.revoked.push(url),
    api: {
      // Offers a whole-line fix for every line it is asked about, like the contraction check.
      lintLines: async (lines) => {
        engine.linted.push(...lines);
        return { lines: lines.map((t) => (t ? [{ code: 'tts_contraction', message: 'Say “your”.', fix: `fixed: ${t}` }] : [])) };
      },
      jobWasRunning: () => engine.wasRunning ?? Promise.resolve(false),
      watchJob: (id, onEvent) => {
        const job = finished(id, id === '00000000000a' ? 100 : 50);
        onEvent(job);
        return { done: Promise.resolve(job), close() {} };
      },
      fileUrl: (id) => {
        engine.urls[id] = deferred();
        return engine.urls[id].promise;
      },
    },
  };
});

const { project, replaceProject, defaultProject } = await import('../src/lib/project.svelte.js');
const ScriptStep = (await import('../src/steps/ScriptStep.svelte')).default;
const { live } = await import('../src/lib/announce.svelte.js');

describe('pronunciation suggestions', () => {
  it('are not offered for a line over the 1000-character limit, so Auto-Fix can’t cut it short', async () => {
    const long = `${'word '.repeat(220)}the end.`;
    replaceProject({
      ...defaultProject(),
      cast: [{ id: 'c1', name: 'Brain', region: { x: 0, y: 0, w: 40, h: 40 } }],
      script: [{ id: 'l1', castId: 'c1', text: "You're late." }, { id: 'l2', castId: 'c1', text: long }],
    });
    engine.linted = [];
    render(ScriptStep);
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Auto-Fix line 1' })).toBeTruthy(), { timeout: 3000 });
    expect(screen.queryByRole('button', { name: 'Auto-Fix line 2' })).toBeNull();
    expect(engine.linted).not.toContain(long);
    expect(project.script[1].text).toBe(long);
  });
});

describe('full-mix preview', () => {
  beforeEach(() => {
    engine.urls = {};
    engine.revoked = [];
    engine.wasRunning = null;
    replaceProject({
      ...defaultProject(),
      cast: [{ id: 'c1', name: 'Brain', region: { x: 0, y: 0, w: 40, h: 40 } }],
      script: [{ id: 'l1', castId: 'c1', text: 'Click it.' }],
    });
  });

  it('keeps the newest preview when an older one’s audio arrives last, and releases the old audio', async () => {
    project.lastPreviewJobId = '00000000000a';
    render(ScriptStep);
    await vi.waitFor(() => expect(engine.urls['00000000000a']).toBeTruthy());
    // While p1's audio is loading, the preview can't be started again.
    expect(screen.getByRole('button', { name: /Preview full audio mix/ }).disabled).toBe(true);

    project.lastPreviewJobId = '00000000000b'; // a newer preview took over
    await vi.waitFor(() => expect(engine.urls['00000000000b']).toBeTruthy());
    engine.urls['00000000000b'].resolve('blob:p2');
    await vi.waitFor(() => expect(screen.getByLabelText('Full audio mix').getAttribute('src')).toBe('blob:p2'));

    engine.urls['00000000000a'].resolve('blob:p1'); // the older audio finally arrives
    await vi.waitFor(() => expect(engine.revoked).toContain('blob:p1'));
    await tick();
    expect(screen.getByLabelText('Full audio mix').getAttribute('src')).toBe('blob:p2');
    expect(screen.getByText(/\(50 frames\)/)).toBeTruthy(); // the timing shown is p2's, matching the audio
    expect(screen.queryByText(/\(100 frames\)/)).toBeNull();
  });

  it('plays a finished preview at once, even while the job-state lookup is still pending', async () => {
    let answer;
    live.polite = '';
    engine.wasRunning = new Promise((r) => (answer = r)); // the engine hasn't answered yet
    project.lastPreviewJobId = '00000000000b';
    render(ScriptStep);
    await vi.waitFor(() => expect(engine.urls['00000000000b']).toBeTruthy()); // audio requested
    engine.urls['00000000000b'].resolve('blob:p2');
    await vi.waitFor(() => expect(screen.getByLabelText('Full audio mix').getAttribute('src')).toBe('blob:p2'));
    expect(screen.getByRole('button', { name: /Preview full audio mix/ }).disabled).toBe(false);
    await new Promise((r) => setTimeout(r, 80));
    expect(live.polite).not.toMatch(/Audio mix ready/); // not announced before we know it's news

    answer(true); // it was still running when the step attached: its ending is news
    await vi.waitFor(() => expect(live.polite).toMatch(/^Audio mix ready: 2 s/));
  });
});
