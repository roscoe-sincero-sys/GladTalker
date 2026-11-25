// The Studio project: everything the user has set up across Cast, Script, and Roll. It is
// auto-saved to localStorage so nothing is re-entered after a reload (WCAG 3.3.7), and can be
// saved to / opened from a `.gladtalker.json` file.

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
  return out;
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

/** Replace the whole project in place (keeps the reactive object identity). */
export function replaceProject(next) {
  const normalized = normalizeProject(next);
  generation++;
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
