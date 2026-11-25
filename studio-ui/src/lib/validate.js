// Everything that would stop a render, as plain-language problems that say where to fix them
// (WCAG 3.3.1 / 3.3.3). Each problem names the step and the field id to focus.

import { regionProblems } from './regions.js';

const nameKey = (s) => s.trim().split(/\s+/).join(' ').toLowerCase();

export function renderProblems(p, { models = null, ffmpeg = null } = {}) {
  const problems = [];
  const add = (step, fieldId, message) => problems.push({ step, fieldId, message });

  if (ffmpeg === false) add('setup', 'setup-heading', 'FFmpeg isn’t installed. See Setup for how to install it.');
  if (models && !models.complete) add('setup', 'setup-heading', 'Some models aren’t downloaded yet. Choose Download Models in Setup.');

  if (!p.image) add('cast', 'scene-file', 'Choose a scene image in the Cast step.');
  if (!p.cast.length) add('cast', 'auto-cast', 'Add at least one cast member (Auto-Detect Cast or Add Region).');

  const seen = new Map();
  p.cast.forEach((m, i) => {
    const label = m.name.trim() || `Cast member ${i + 1}`;
    if (!m.name.trim()) add('cast', `name-${m.id}`, `Cast member ${i + 1} needs a name.`);
    else if (seen.has(nameKey(m.name))) add('cast', `name-${m.id}`, `“${m.name.trim()}” is used by two cast members. Give each a different name.`);
    else seen.set(nameKey(m.name), m.id);
    if (p.image) {
      for (const msg of regionProblems(m.region, p.image.width, p.image.height)) add('cast', `region-x-${m.id}`, `${label}: ${msg}`);
    }
  });

  if (!p.script.length) add('script', 'script-import', 'Add at least one line of dialogue in the Script step.');
  const castIds = new Set(p.cast.map((m) => m.id));
  p.script.forEach((l, i) => {
    if (!l.castId || !castIds.has(l.castId)) add('script', `speaker-${l.id}`, `Line ${i + 1} needs a speaker.`);
    if (!l.text.trim()) add('script', `text-${l.id}`, `Line ${i + 1} is empty. Type what should be said, or delete the line.`);
  });

  if (p.pacing.mode === 'target') {
    const t = Number(p.pacing.targetSeconds);
    if (!(t > 0 && t <= 600)) add('script', 'target-seconds', 'Set a target duration between 1 and 600 seconds.');
  }
  return problems;
}

export const problemsForStep = (problems, step) => problems.filter((x) => x.step === step);
