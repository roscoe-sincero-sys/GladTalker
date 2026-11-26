// The Studio project: everything the user has set up across Cast, Script, and Roll. It is
// auto-saved to localStorage so nothing is re-entered after a reload (WCAG 3.3.7), and can be
// saved to / opened from a `.gladtalker.json` file.

import { isJobId } from './api.js';

export const STORAGE_KEY = 'gladtalker.project.v1';
export const PROJECT_VERSION = 1;
// The engine's request limits (src/studio/schemas.py), checked here so the UI never offers a
// project the engine would reject.
export const MAX_CAST = 8;
export const MAX_LINES = 500;
export const MAX_LINE_CHARS = 1000;
export const MAX_NAME_CHARS = 64;

let nextId = 1;
export const newId = (prefix) => `${prefix}${Date.now().toString(36)}${(nextId++).toString(36)}`;

export function defaultProject() {
  return {
    version: PROJECT_VERSION,
    image: null, // { path, name, width, height }
    detections: [], // engine /cast/detect suggestions
    cast: [], // { id, name, region: {x,y,w,h}, voice, semitones, style }
    script: [], // { id, castId, text }
    pacing: { mode: 'natural', targetSeconds: 42, gapMs: 300 },
    output: { preset: 'hd720', fit: 'fit', background: '#ffffff', filename: '' },
    quality: { mode: 'standard' },
    captions: true,
    lastRenderJobId: null,
    lastPreviewJobId: null,
    lastPreviewKey: null, // previewDigest() of the request that preview was made from
  };
}

/** Accept a saved project (from storage or a file), filling in anything missing. Throws a
 * readable error for things that aren't GladTalker projects. */
export function normalizeProject(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('This file is not a GladTalker project.');
  if (raw.version !== undefined && raw.version > PROJECT_VERSION)
    throw new Error('This project was saved by a newer version of GladTalker Studio.');
  const base = defaultProject();
  const out = { ...base, ...raw, version: PROJECT_VERSION };
  out.pacing = { ...base.pacing, ...(raw.pacing || {}) };
  out.output = { ...base.output, ...(raw.output || {}) };
  out.quality = { ...base.quality, ...(raw.quality || {}) };
  out.cast = Array.isArray(raw.cast) ? raw.cast.map((m, i) => ({
    id: m.id || newId('c'), name: String(m.name ?? `Character ${i + 1}`), region: { ...m.region },
    voice: m.voice || 'neutral', semitones: Number(m.semitones) || 0, style: Number.isInteger(m.style) ? m.style : i,
  })) : [];
  const ids = new Set(out.cast.map((m) => m.id));
  out.script = Array.isArray(raw.script) ? raw.script.map((l) => ({
    id: l.id || newId('l'), castId: ids.has(l.castId) ? l.castId : null, text: String(l.text ?? ''),
    pendingSpeaker: l.pendingSpeaker ?? null,
  })) : [];
  out.detections = Array.isArray(raw.detections) ? raw.detections : [];
  // References to engine jobs come back only if they look like ids the engine made; anything
  // else is dropped (the cast and dialogue are kept), so a project file can't steer requests.
  out.lastRenderJobId = isJobId(raw.lastRenderJobId) ? raw.lastRenderJobId : null;
  out.lastPreviewJobId = isJobId(raw.lastPreviewJobId) ? raw.lastPreviewJobId : null;
  out.lastPreviewKey = DIGEST_RE.test(raw.lastPreviewKey ?? '') ? raw.lastPreviewKey : null;
  return out;
}

const DIGEST_RE = /^[0-9a-f]{14}$/;

/** A short fingerprint of a preview request, enough to tell whether the script changed since
 * that preview. Only this is saved, never the request itself: an old snapshot of the dialogue
 * would otherwise live on in saved and shared project files after the lines were edited. */
export function previewDigest(body) {
  const text = JSON.stringify(body);
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 2654435761);
    h2 = Math.imul(h2 ^ c, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return ((h2 >>> 0).toString(16).padStart(8, '0') + (h1 >>> 0).toString(16).padStart(8, '0')).slice(0, 14);
}

export function loadProject(storage = globalThis.localStorage) {
  try {
    const raw = storage?.getItem(STORAGE_KEY);
    return raw ? normalizeProject(JSON.parse(raw)) : defaultProject();
  } catch {
    return defaultProject();
  }
}

export function saveProject(project, storage = globalThis.localStorage) {
  return saveProjectText(JSON.stringify(project), storage);
}

/** Store an already-serialized project (autosave serializes once to see what changed). */
export function saveProjectText(text, storage = globalThis.localStorage) {
  try {
    storage?.setItem(STORAGE_KEY, text);
    return true;
  } catch {
    return false; // private mode / quota: the session still works, it just won't persist
  }
}

export const project = $state(loadProject());

// Bumped whenever New/Open replaces the project. Anything that awaits the engine and then writes
// to the project captures a guard first and drops its result if the project was replaced in the
// meantime, so a slow import or detection never lands in a different project.
let generation = 0;

/** Returns a check that stays true until the project is replaced. */
export function projectGuard() {
  const mine = generation;
  return () => mine === generation;
}

// The scene upload in flight. It belongs to the project, not to a Cast view: leaving Cast and
// coming back mounts a new view, which must still see the earlier upload as busy, so a second
// upload can't be accepted and then overwritten when the earlier answer arrives. Replacing the
// project (New, Open) frees it: the old upload's answer is dropped by its projectGuard, and it
// only clears the flag while its project is still the current one.
export const sceneUpload = $state({ busy: false });

/** Replace the whole project in place (keeps the reactive object identity). */
/** Does this project hold anything a user made or chose: a scene, cast, script, or any setting
 * changed from the defaults? New and Open ask before replacing such a project (WCAG 3.3.4).
 * Bookkeeping (detections, the last job ids) doesn't count on its own. */
export function hasWork(p) {
  const same = (a, b) => {
    if (a === b) return true;
    if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) return false;
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    return [...keys].every((k) => same(a[k], b[k]));
  };
  const blank = defaultProject();
  return ['image', 'cast', 'script', 'pacing', 'output', 'quality', 'captions'].some((k) => !same(p[k], blank[k]));
}

export function replaceProject(next) {
  const normalized = normalizeProject(next);
  generation++;
  sceneUpload.busy = false; // an upload still pending belongs to the project being replaced
  for (const key of Object.keys(project)) delete project[key];
  Object.assign(project, normalized);
}

export const castName = (castId) => project.cast.find((m) => m.id === castId)?.name ?? null;

/** Lowest style index not used by another cast member, so badges stay distinct. */
export function nextStyleIndex(cast) {
  const used = new Set(cast.map((m) => m.style));
  let i = 0;
  while (used.has(i)) i++;
  return i;
}

/** Body for POST /render/start (names, not ids, as the engine expects). */
export function renderRequest(p) {
  const byId = new Map(p.cast.map((m) => [m.id, m]));
  const body = {
    image_path: p.image?.path,
    cast: p.cast.map((m) => ({ name: m.name.trim(), region: { ...m.region }, semitones: m.semitones })),
    script: p.script.map((l) => ({ speaker: byId.get(l.castId)?.name.trim() ?? '', text: l.text.trim() })),
    pacing: pacingBody(p.pacing),
    output: { preset: p.output.preset, fit: p.output.fit, background: p.output.background },
    quality: { mode: p.quality.mode },
    captions: Boolean(p.captions),
  };
  if (p.output.filename?.trim()) body.output.filename = p.output.filename.trim();
  return body;
}

/** Body for POST /audio/preview. */
export function audioPreviewRequest(p) {
  const byId = new Map(p.cast.map((m) => [m.id, m]));
  return {
    cast: p.cast.map((m) => ({ name: m.name.trim(), semitones: m.semitones })),
    script: p.script.map((l) => ({ speaker: byId.get(l.castId)?.name.trim() ?? '', text: l.text.trim() })),
    pacing: pacingBody(p.pacing),
  };
}

function pacingBody(pacing) {
  return pacing.mode === 'target'
    ? { target_duration_s: Number(pacing.targetSeconds), gap_ms: Number(pacing.gapMs) }
    : { target_duration_s: null, gap_ms: Number(pacing.gapMs) };
}
