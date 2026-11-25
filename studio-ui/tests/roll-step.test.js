// The Roll step following a render: the finished video never waits on the job-state lookup.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/svelte';

const engine = vi.hoisted(() => ({ wasRunning: null, fileUrls: [] }));

vi.mock('../src/lib/api.js', async (importOriginal) => {
  const real = await importOriginal();
  const finished = (id) => ({ id, state: 'succeeded', progress: 1, message: 'Done', stages: ['render'],
    result: { duration_s: 42, width: 1280, height: 720, video_path: `results/studio/jobs/${id}/out.mp4` } });
  return {
    ...real,
    revokeMedia: () => {},
    api: {
      jobWasRunning: () => engine.wasRunning,
      watchJob: (id, onEvent) => {
        // The replay of a job (every snapshot, its end included), then its end.
        onEvent({ id, state: 'running', progress: 0.5, message: 'Rendering', stages: ['render'] });
        onEvent(finished(id));
        return { done: Promise.resolve(finished(id)), close() {} };
      },
      fileUrl: async (id, kind) => {
        engine.fileUrls.push(kind);
        return `blob:${kind}-${id}`;
      },
    },
  };
});

const { project, replaceProject, defaultProject } = await import('../src/lib/project.svelte.js');
const RollStep = (await import('../src/steps/RollStep.svelte')).default;
const { live } = await import('../src/lib/announce.svelte.js');

describe('a finished render', () => {
  beforeEach(() => {
    engine.fileUrls = [];
    live.polite = '';
    replaceProject(defaultProject());
  });

  it('shows its video at once while the job-state lookup is pending, and announces it once that says it is news', async () => {
    let answer;
    engine.wasRunning = new Promise((r) => (answer = r)); // the engine hasn't answered yet
    project.lastRenderJobId = '0123456789ab';
    render(RollStep);
    await vi.waitFor(() => expect(document.querySelector('video')?.getAttribute('src')).toBe('blob:video-0123456789ab'));
    expect(screen.getByRole('link', { name: 'Download video (MP4)' })).toBeTruthy();
    await new Promise((r) => setTimeout(r, 80));
    expect(live.polite).not.toMatch(/Your video is ready/);

    answer(true);
    await vi.waitFor(() => expect(live.polite).toBe('Your video is ready: 42 s, 1280×720. The player and download links are below.'));
  });

  it('shows the video of a render that had already finished, without announcing it', async () => {
    engine.wasRunning = Promise.resolve(false);
    project.lastRenderJobId = 'ba9876543210';
    render(RollStep);
    await vi.waitFor(() => expect(document.querySelector('video')?.getAttribute('src')).toBe('blob:video-ba9876543210'));
    await new Promise((r) => setTimeout(r, 80));
    expect(live.polite).not.toMatch(/Your video is ready/);
  });
});
