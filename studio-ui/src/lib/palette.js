// Speaker identity: every cast member gets a color *and* a shape *and* a number (WCAG 1.4.1:
// never color alone). Badge colors are dark enough for white text (>= 4.5:1, WCAG 1.4.3) and
// distinct from both page backgrounds (>= 3:1 non-text contrast, WCAG 1.4.11); see tests.

export const SPEAKER_STYLES = [
  { color: '#1d4ed8', shape: 'circle', shapeName: 'circle' },
  { color: '#b91c1c', shape: 'square', shapeName: 'square' },
  { color: '#047857', shape: 'triangle', shapeName: 'triangle' },
  { color: '#7e22ce', shape: 'diamond', shapeName: 'diamond' },
  { color: '#9a3412', shape: 'hexagon', shapeName: 'hexagon' },
  { color: '#0e7490', shape: 'star', shapeName: 'star' },
  { color: '#be185d', shape: 'pentagon', shapeName: 'pentagon' },
  { color: '#3f6212', shape: 'cross', shapeName: 'cross' },
];

export const speakerStyle = (index) => SPEAKER_STYLES[index % SPEAKER_STYLES.length];

// SVG paths in a 0..20 box, one per shape.
export const SHAPE_PATHS = {
  circle: 'M10 1a9 9 0 1 0 0 18a9 9 0 1 0 0-18z',
  square: 'M2 2h16v16H2z',
  triangle: 'M10 1l9.5 17H0.5z',
  diamond: 'M10 0.5l9.5 9.5l-9.5 9.5l-9.5-9.5z',
  hexagon: 'M5 1.5h10l5 8.5l-5 8.5H5l-5-8.5z',
  star: 'M10 0.5l2.9 6.2l6.6 0.7l-5 4.5l1.4 6.6l-5.9-3.4l-5.9 3.4l1.4-6.6l-5-4.5l6.6-0.7z',
  pentagon: 'M10 0.5l9.5 6.9l-3.6 11.1H4.1L0.5 7.4z',
  cross: 'M7 1h6v6h6v6h-6v6H7v-6H1V7h6z',
};

// --- WCAG contrast math ------------------------------------------------------------------------

function channel(c) {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

export function luminance(hex) {
  const n = parseInt(hex.replace('#', ''), 16);
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
}

export function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// Theme tokens mirrored from app.css so the tests can check them.
export const THEMES = {
  light: { bg: '#ffffff', surface: '#f4f5f8', text: '#1b1e24', muted: '#4a5160', accent: '#1f5fbf',
    border: '#767d8c', error: '#b42318', success: '#12733b', focus: '#1f5fbf' },
  dark: { bg: '#17161a', surface: '#221f27', text: '#f2eff5', muted: '#bdb7c7', accent: '#8fb4ff',
    border: '#8d8799', error: '#ff9b8f', success: '#7ad69b', focus: '#8fb4ff' },
};
