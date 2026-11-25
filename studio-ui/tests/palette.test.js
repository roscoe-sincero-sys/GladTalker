import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { SPEAKER_STYLES, SHAPE_PATHS, THEMES, contrast } from '../src/lib/palette.js';

const css = readFileSync(path.resolve(process.cwd(), 'src/app.css'), 'utf8');

describe('theme contrast (WCAG 1.4.3 text, 1.4.11 non-text)', () => {
  for (const [name, t] of Object.entries(THEMES)) {
    describe(name, () => {
      it('body, muted, link, and status text reach 4.5:1 on page and surface', () => {
        for (const fg of [t.text, t.muted, t.accent, t.error, t.success]) {
          expect(contrast(fg, t.bg), `${fg} on ${t.bg}`).toBeGreaterThanOrEqual(4.5);
          expect(contrast(fg, t.surface), `${fg} on ${t.surface}`).toBeGreaterThanOrEqual(4.5);
        }
      });
      it('field borders and the focus ring reach 3:1', () => {
        expect(contrast(t.border, t.bg)).toBeGreaterThanOrEqual(3);
        expect(contrast(t.border, t.surface)).toBeGreaterThanOrEqual(3);
        expect(contrast(t.focus, t.bg)).toBeGreaterThanOrEqual(3);
        expect(contrast(t.focus, t.surface)).toBeGreaterThanOrEqual(3);
      });
    });
  }

  it('primary button text reaches 4.5:1 on the accent color in both themes', () => {
    expect(contrast('#ffffff', THEMES.light.accent)).toBeGreaterThanOrEqual(4.5);
    expect(contrast('#10131a', THEMES.dark.accent)).toBeGreaterThanOrEqual(4.5);
  });

  it('app.css uses exactly the tokens checked here', () => {
    const light = css.slice(css.indexOf(':root {'), css.indexOf('@media (prefers-color-scheme: dark)'));
    const dark = css.slice(css.indexOf('@media (prefers-color-scheme: dark)'));
    for (const [token, value] of Object.entries(THEMES.light)) {
      expect(light, `--${token} (light)`).toContain(`--${token}: ${value};`);
    }
    for (const [token, value] of Object.entries(THEMES.dark)) {
      expect(dark, `--${token} (dark)`).toContain(`--${token}: ${value};`);
    }
  });
});

describe('speaker badges (WCAG 1.4.1: color + shape + number)', () => {
  it('every badge color carries white numbers at 4.5:1', () => {
    for (const s of SPEAKER_STYLES) expect(contrast('#ffffff', s.color), s.color).toBeGreaterThanOrEqual(4.5);
  });
  it('badges stand out from both page backgrounds by color or by their white outline (3:1)', () => {
    for (const t of Object.values(THEMES)) {
      for (const s of SPEAKER_STYLES) {
        const best = Math.max(contrast(s.color, t.bg), contrast('#ffffff', t.bg));
        expect(best, `${s.color} on ${t.bg}`).toBeGreaterThanOrEqual(3);
      }
    }
  });
  it('every style has a distinct color and a distinct, drawable shape', () => {
    expect(new Set(SPEAKER_STYLES.map((s) => s.color)).size).toBe(SPEAKER_STYLES.length);
    expect(new Set(SPEAKER_STYLES.map((s) => s.shape)).size).toBe(SPEAKER_STYLES.length);
    for (const s of SPEAKER_STYLES) expect(SHAPE_PATHS[s.shape]).toBeTruthy();
  });
});
