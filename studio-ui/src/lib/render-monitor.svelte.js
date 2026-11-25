// Desktop app only: follows the current render at app level (whichever step is open) to show its
// progress on the taskbar icon and send a notification when it ends while the user is in
// another window. (The shell only shows the notification if the Studio window isn't focused.)

import { untrack } from 'svelte';
import { api } from './api.js';
import { project } from './project.svelte.js';
import { desktopCall } from './desktop.js';
import { duration } from './format.js';

const RUNNING = new Set(['queued', 'running']);

/** Starts following `project.lastRenderJobId`. Returns a function that stops it. */
export function startRenderMonitor({ call = desktopCall } = {}) {
  let watchedId = null;
  let watch = null;
  const progress = (fraction, failed = false) => call('set_progress', { fraction, failed }).catch(() => {});
  const notify = (title, body) => call('notify', { title, body }).catch(() => {});

  function follow(id) {
    if (id === watchedId) return;
    watchedId = id;
    watch?.close();
    watch = null;
    progress(null);
    if (!id) return;
    // Its end is news only if it was still running when we started watching (the watcher
    // replays the whole history of a job that finished before, queued and running included).
    const wasRunning = api.jobWasRunning(id);
    watch = api.watchJob(id, (job) => {
      if (watchedId !== id || !RUNNING.has(job.state)) return;
      progress(job.progress ?? 0);
    });
    watch.done.then(
      async (final) => {
        if (watchedId !== id || !final) return;
        progress(null);
        if (!(await wasRunning) || watchedId !== id) return;
        if (final.state === 'succeeded') {
          const r = final.result ?? {};
          notify('Your video is ready', `${duration(r.duration_s)}, ${r.width}×${r.height}. Open GladTalker Studio to watch it.`);
        } else if (final.state === 'failed') {
          notify('The render failed', final.error || final.message || 'Open GladTalker Studio for details.');
        }
      },
      () => {
        if (watchedId === id) progress(null);
      },
    );
  }

  const stopEffects = $effect.root(() => {
    $effect(() => {
      const id = project.lastRenderJobId;
      untrack(() => follow(id));
    });
  });
  return () => {
    stopEffects();
    watch?.close();
    progress(null);
  };
}
