// Starting renders and audio previews. This lives at project scope, not in the step components:
// the engine can accept a job after the user has left the step that asked for it, and the
// accepted job id must still reach the project (and its autosave), so the step re-attaches to it
// whenever it is shown. A submission is dropped only if New/Open replaced the project meanwhile.

import { api } from './api.js';
import { project, projectGuard, previewDigest } from './project.svelte.js';

/** True while a request is on its way, so a second click can't start a duplicate job. */
export const submitting = $state({ render: false, preview: false });

export async function submitRender(body) {
  if (submitting.render) return null;
  submitting.render = true;
  const current = projectGuard();
  try {
    const job = await api.startRender(body);
    if (!current()) return null;
    project.lastRenderJobId = job.id;
    return job;
  } finally {
    submitting.render = false;
  }
}

/** Starts a full-mix preview of `body` (an audioPreviewRequest). The request is remembered as
 * a digest in `lastPreviewKey`, so the Script step can tell when the script has changed since. */
export async function submitPreview(body) {
  if (submitting.preview) return null;
  submitting.preview = true;
  const current = projectGuard();
  try {
    const job = await api.previewAudio(body);
    if (!current()) return null;
    project.lastPreviewJobId = job.id;
    project.lastPreviewKey = previewDigest(body);
    return job;
  } finally {
    submitting.preview = false;
  }
}
