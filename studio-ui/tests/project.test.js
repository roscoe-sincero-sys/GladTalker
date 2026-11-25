import { describe, it, expect } from 'vitest';
import {
  defaultProject, normalizeProject, loadProject, saveProject, renderRequest, audioPreviewRequest,
  nextStyleIndex, STORAGE_KEY, PROJECT_VERSION,
} from '../src/lib/project.svelte.js';
import { renderProblems, problemsForStep } from '../src/lib/validate.js';
import { moveLine, linesFromParse, nameKey } from '../src/lib/script-lines.js';
import { duration, signed, pacingLabel, timestamp } from '../src/lib/format.js';

const IMAGE = { path: 'C:/ws/uploads/abc.png', name: 'scene.png', width: 977, height: 580 };

function sample() {
  const p = defaultProject();
  p.image = { ...IMAGE };
  p.cast = [
    { id: 'c1', name: ' Brain ', region: { x: 757, y: 37, w: 216, h: 116 }, voice: 'authoritative', semitones: -1.5, style: 0 },
    { id: 'c2', name: 'Officer  Mia', region: { x: 755, y: 160, w: 220, h: 119 }, voice: 'nervous', semitones: 2.5, style: 1 },
  ];
  p.script = [
    { id: 'l1', castId: 'c1', text: ' Click it. ' },
    { id: 'l2', castId: 'c2', text: "I'm clicking." },
  ];
  return p;
}

describe('project persistence (WCAG 3.3.7)', () => {
  it('round-trips through storage', () => {
    const p = sample();
    expect(saveProject(p, localStorage)).toBe(true);
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)).cast[1].name).toBe('Officer  Mia');
    expect(loadProject(localStorage).script).toHaveLength(2);
  });
  it('falls back to a fresh project on corrupt storage', () => {
    localStorage.setItem(STORAGE_KEY, '{not json');
    expect(loadProject(localStorage)).toEqual(defaultProject());
  });
  it('fills in missing fields and unlinks lines whose speaker is gone', () => {
    const p = normalizeProject({ cast: [{ id: 'c1', name: 'A', region: { x: 0, y: 0, w: 40, h: 40 } }],
      script: [{ id: 'l1', castId: 'gone', text: 'Hi' }], pacing: { mode: 'target' } });
    expect(p.pacing).toEqual({ mode: 'target', targetSeconds: 42, gapMs: 300 });
    expect(p.script[0].castId).toBeNull();
    expect(p.version).toBe(PROJECT_VERSION);
  });
  it('rejects files that are not projects or are too new', () => {
    expect(() => normalizeProject([])).toThrow(/not a GladTalker project/);
    expect(() => normalizeProject({ version: PROJECT_VERSION + 1 })).toThrow(/newer version/);
  });
});

describe('request bodies', () => {
  it('maps cast ids to the engine’s speaker names', () => {
    const body = renderRequest(sample());
    expect(body.cast.map((c) => c.name)).toEqual(['Brain', 'Officer  Mia']);
    expect(body.script).toEqual([{ speaker: 'Brain', text: 'Click it.' }, { speaker: 'Officer  Mia', text: "I'm clicking." }]);
    expect(body.pacing).toEqual({ target_duration_s: null, gap_ms: 300 });
    expect(body.output).not.toHaveProperty('filename');
    expect(body.image_path).toBe(IMAGE.path);
  });
  it('includes the target duration and filename when set', () => {
    const p = sample();
    p.pacing.mode = 'target';
    p.pacing.targetSeconds = 42;
    p.output.filename = ' phm ';
    const body = renderRequest(p);
    expect(body.pacing.target_duration_s).toBe(42);
    expect(body.output.filename).toBe('phm');
    expect(audioPreviewRequest(p)).toEqual({
      cast: [{ name: 'Brain', semitones: -1.5 }, { name: 'Officer  Mia', semitones: 2.5 }],
      script: body.script, pacing: body.pacing,
    });
  });
  it('gives each new cast member an unused badge style', () => {
    expect(nextStyleIndex([{ style: 0 }, { style: 2 }])).toBe(1);
  });
});

describe('render checklist (WCAG 3.3.1 / 3.3.3)', () => {
  it('is empty for a complete project', () => {
    expect(renderProblems(sample(), { models: { complete: true }, ffmpeg: true })).toEqual([]);
  });
  it('lists every blocker with the step and field to fix', () => {
    const p = sample();
    p.cast[1].name = 'brain';
    p.cast[0].region.x = 900;
    p.script.push({ id: 'l3', castId: null, text: '  ' });
    const problems = renderProblems(p, { models: { complete: false }, ffmpeg: false });
    expect(problems.map((x) => x.fieldId)).toEqual([
      'setup-heading', 'setup-heading', 'region-x-c1', 'name-c2', 'speaker-l3', 'text-l3',
    ]);
    expect(problemsForStep(problems, 'script')).toHaveLength(2);
    expect(problems.find((x) => x.fieldId === 'name-c2').message).toMatch(/used by two cast members/);
  });
  it('asks for an image, cast, and script when starting from scratch', () => {
    expect(renderProblems(defaultProject()).map((x) => x.fieldId)).toEqual(['scene-file', 'auto-cast', 'script-import']);
  });
});

describe('dialogue helpers', () => {
  it('moves lines and refuses to move past the ends', () => {
    expect(moveLine(['a', 'b', 'c'], 0, 1)).toEqual(['b', 'a', 'c']);
    expect(moveLine(['a', 'b'], 0, -1)).toBeNull();
    expect(moveLine(['a', 'b'], 1, 1)).toBeNull();
  });
  it('links parsed speakers to cast ids ignoring case and spacing, keeping unknown names', () => {
    const cast = sample().cast;
    const parsed = { lines: [{ speaker: 'OFFICER MIA', text: 'Hi' }, { speaker: 'Narrator', text: 'Later.' }, { speaker: null, text: '?' }], warning_count: 1 };
    const { lines, unknownSpeakers, warningCount } = linesFromParse(parsed, cast);
    expect(lines.map((l) => l.castId)).toEqual(['c2', null, null]);
    expect(lines[1].pendingSpeaker).toBe('Narrator');
    expect(unknownSpeakers).toEqual(['Narrator']);
    expect(warningCount).toBe(1);
    expect(nameKey('  Officer   MIA ')).toBe('officer mia');
  });
});

describe('formatting', () => {
  it('formats durations, pitch, pace, and timestamps', () => {
    expect(duration(42)).toBe('42 s');
    expect(duration(1329)).toBe('22 min 09 s');
    expect(signed(-1.5)).toBe('−1.5 st');
    expect(signed(2.5)).toBe('+2.5 st');
    expect(pacingLabel(1)).toBe('1.00× (Natural)');
    expect(pacingLabel(1.082)).toBe('1.08× (Slightly faster)');
    expect(timestamp(61_500)).toBe('1:01.5');
  });
});
