// Everything that would stop a render, as plain-language problems that say where to fix them
// (WCAG 3.3.1 / 3.3.3). Each problem names the step and the field id to focus.

import { regionProblems } from './regions.js';
import { MAX_CAST, MAX_LINES, MAX_LINE_CHARS, MAX_NAME_CHARS } from './project.svelte.js';

const nameKey = (s) => s.trim().split(/\s+/).join(' ').toLowerCase();

export function renderProblems(p, { models = null, ffmpeg = null } = {}) {
  const problems = [];
  const add = (step, fieldId, message) => problems.push({ step, fieldId, message });

  if (ffmpeg === false) add('setup', 'setup-heading', 'FFmpeg isn’t installed. See Setup for how to install it.');
  if (models && !models.complete) {
    const outstanding = (models.files ?? []).filter((f) => !f.ready);
    if (outstanding.length && outstanding.every((f) => f.busy)) {
      add('setup', 'setup-heading', 'Some models are being updated or are in use by another program. '
        + 'GladTalker checks them again every few seconds; you can render once they’re free.');
    } else {
      add('setup', 'setup-heading', 'Some models aren’t downloaded yet. Choose Download Models in Setup.');
    }
  }

  if (!p.image) add('cast', 'scene-file', 'Choose a scene image in the Cast step.');
  soundtrackProblems(p, add, { regions: true });
  return problems;
}

/** What stops POST /audio/preview: the render's cast and script rules, without the scene
 * image, regions, or setup. */
export function audioProblems(p) {
  const problems = [];
  soundtrackProblems(p, (step, fieldId, message) => problems.push({ step, fieldId, message }), { regions: false });
  return problems;
}

function soundtrackProblems(p, add, { regions }) {
  if (!p.cast.length) add('cast', 'auto-cast', 'Add at least one cast member (Auto-Detect Cast or Add Region).');
  if (p.cast.length > MAX_CAST) {
    add('cast', `name-${p.cast[MAX_CAST].id}`, `A video can have up to ${MAX_CAST} cast members. `
      + `Remove ${p.cast.length - MAX_CAST} (there are ${p.cast.length}).`);
  }

  const seen = new Map();
  p.cast.forEach((m, i) => {
    const label = m.name.trim() || `Cast member ${i + 1}`;
    if (!m.name.trim()) add('cast', `name-${m.id}`, `Cast member ${i + 1} needs a name.`);
    else if (m.name.trim().length > MAX_NAME_CHARS) add('cast', `name-${m.id}`, `Cast member ${i + 1}’s name is longer than ${MAX_NAME_CHARS} characters. Shorten it.`);
    else if (seen.has(nameKey(m.name))) add('cast', `name-${m.id}`, `“${m.name.trim()}” is used by two cast members. Give each a different name.`);
    else seen.set(nameKey(m.name), m.id);
    if (regions && p.image) {
      for (const msg of regionProblems(m.region, p.image.width, p.image.height)) add('cast', `region-x-${m.id}`, `${label}: ${msg}`);
    }
  });

  if (!p.script.length) add('script', 'script-import', 'Add at least one line of dialogue in the Script step.');
  if (p.script.length > MAX_LINES) {
    add('script', `text-${p.script[MAX_LINES].id}`, `A script can have up to ${MAX_LINES} lines. `
      + `Delete ${p.script.length - MAX_LINES} (there are ${p.script.length}).`);
  }
  const castIds = new Set(p.cast.map((m) => m.id));
  p.script.forEach((l, i) => {
    if (!l.castId || !castIds.has(l.castId)) add('script', `speaker-${l.id}`, `Line ${i + 1} needs a speaker.`);
    if (!l.text.trim()) add('script', `text-${l.id}`, `Line ${i + 1} is empty. Type what should be said, or delete the line.`);
    else if (l.text.trim().length > MAX_LINE_CHARS) add('script', `text-${l.id}`, `Line ${i + 1} is longer than ${MAX_LINE_CHARS} characters. Split it into shorter lines.`);
  });

  if (p.pacing.mode === 'target') {
    const t = Number(p.pacing.targetSeconds);
    if (!(t > 0 && t <= 600)) add('script', 'target-seconds', 'Set a target duration between 1 and 600 seconds.');
  }
}

export const problemsForStep = (problems, step) => problems.filter((x) => x.step === step);
