// Pure helpers for the dialogue editor.

import { newId } from './project.svelte.js';

export const nameKey = (s) => String(s || '').trim().split(/\s+/).join(' ').toLowerCase();

/** Move the line at `index` by `delta` (-1 up, +1 down). Returns a new array, or null if it
 * can't move further. */
export function moveLine(lines, index, delta) {
  const to = index + delta;
  if (index < 0 || index >= lines.length || to < 0 || to >= lines.length) return null;
  const next = lines.slice();
  const [item] = next.splice(index, 1);
  next.splice(to, 0, item);
  return next;
}

/** Turn /script/parse output into editor lines linked to cast ids. Speakers not in the cast get
 * castId null (the row then asks for a speaker), and are reported so the UI can say so. */
export function linesFromParse(parsed, cast) {
  const byName = new Map(cast.map((m) => [nameKey(m.name), m.id]));
  const unknown = new Set();
  const lines = parsed.lines.map((l) => {
    const castId = l.speaker ? byName.get(nameKey(l.speaker)) ?? null : null;
    if (l.speaker && !castId) unknown.add(l.speaker);
    // Remember an unknown speaker's name so "Add to the cast" can assign these lines later.
    return { id: newId('l'), castId, text: l.text, pendingSpeaker: castId ? null : l.speaker ?? null };
  });
  return { lines, unknownSpeakers: [...unknown], warningCount: parsed.warning_count ?? 0 };
}

/** "Name: text" for every line, e.g. to copy the script back out. */
export function scriptToText(lines, cast) {
  const byId = new Map(cast.map((m) => [m.id, m.name]));
  return lines.map((l) => `${byId.get(l.castId) ?? 'Unassigned'}: ${l.text}`).join('\n');
}
