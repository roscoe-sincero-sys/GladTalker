// The Script step's full-mix preview against a scripted engine: answers arrive out of order.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import { tick } from 'svelte';

const engine = vi.hoisted(() => ({ urls: {}, created: [], revoked: [] }));

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
      lintLines: async (lines) => ({ lines: lines.map(() => []) }),
      watchJob: (id, onEvent) => {
        const job = finished(id, id === 'p1' ? 100 : 50);
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

describe('full-mix preview', () => {
  beforeEach(() => {
    engine.urls = {};
    engine.revoked = [];
    replaceProject({
      ...defaultProject(),
      cast: [{ id: 'c1', name: 'Brain', region: { x: 0, y: 0, w: 40, h: 40 } }],
      script: [{ id: 'l1', castId: 'c1', text: 'Click it.' }],
    });
  });

  it('keeps the newest preview when an older one’s audio arrives last, and releases the old audio', async () => {
    project.lastPreviewJobId = 'p1';
    render(ScriptStep);
    await vi.waitFor(() => expect(engine.urls.p1).toBeTruthy());
    // While p1's audio is loading, the preview can't be started again.
    expect(screen.getByRole('button', { name: /Preview full audio mix/ }).disabled).toBe(true);

    project.lastPreviewJobId = 'p2'; // a newer preview took over
    await vi.waitFor(() => expect(engine.urls.p2).toBeTruthy());
    engine.urls.p2.resolve('blob:p2');
    await vi.waitFor(() => expect(screen.getByLabelText('Full audio mix').getAttribute('src')).toBe('blob:p2'));

    engine.urls.p1.resolve('blob:p1'); // the older audio finally arrives
    await vi.waitFor(() => expect(engine.revoked).toContain('blob:p1'));
    await tick();
    expect(screen.getByLabelText('Full audio mix').getAttribute('src')).toBe('blob:p2');
    expect(screen.getByText(/\(50 frames\)/)).toBeTruthy(); // the timing shown is p2's, matching the audio
    expect(screen.queryByText(/\(100 frames\)/)).toBeNull();
  });
});
