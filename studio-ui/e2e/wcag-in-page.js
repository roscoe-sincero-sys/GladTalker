// In-page measurements for the WCAG 2.2 audit (e2e/wcag-audit.spec.js). Injected as a classic
// script; everything is on window.__wcag and returns plain, serializable data.
(() => {
  const FOCUSABLE = 'a[href], button, input:not([type="hidden"]), select, textarea, summary, iframe, '
    + 'audio[controls], video[controls], [contenteditable="true"], [tabindex]';
  const TARGETS = 'a[href], button, input:not([type="hidden"]), select, textarea, summary, audio[controls], '
    + 'video[controls], [role="button"], [role="link"], [role="checkbox"], [role="radio"], [role="slider"], '
    + '[role="tab"], [role="menuitem"], [role="switch"]';

  function isShown(el) {
    if (!el.getClientRects().length) return false;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') return false;
    if (el.closest('[inert]')) return false;
    if (el.closest('.visually-hidden')) return false;
    return true;
  }

  const isDisabled = (el) => el.disabled === true || el.getAttribute('aria-disabled') === 'true'
    || Boolean(el.closest('fieldset:disabled'));

  /** A short, readable name for an element in a test report. */
  function describe(el) {
    if (!el || el === document.body) return 'body';
    const name = el.getAttribute('aria-label') || (el.labels && el.labels[0] && el.labels[0].textContent)
      || el.getAttribute('title') || el.textContent || el.getAttribute('placeholder') || '';
    const id = el.id ? `#${el.id}` : '';
    const type = el.getAttribute('type') ? `[type=${el.getAttribute('type')}]` : '';
    return `${el.tagName.toLowerCase()}${id}${type} "${name.replace(/\s+/g, ' ').trim().slice(0, 50)}"`;
  }

  /** A stable key for an element: its index path from <html>. */
  function key(el) {
    const parts = [];
    for (let n = el; n && n.parentElement; n = n.parentElement) parts.push([...n.parentElement.children].indexOf(n));
    return parts.reverse().join('.');
  }

  // ---- colour ----------------------------------------------------------------------------------
  function parseColor(c) {
    const m = String(c).match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  }
  const over = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
  });
  function luminance({ r, g, b }) {
    const ch = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
  }
  function contrast(a, b) {
    const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05);
  }
  /** The solid colour behind `el` (its own background, then its ancestors', composited). */
  function backgroundOf(el) {
    const layers = [];
    for (let n = el; n; n = n.parentElement) {
      const c = parseColor(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0) {
        layers.push(c);
        if (c.a >= 1) break;
      }
    }
    let color = { r: 255, g: 255, b: 255, a: 1 };
    const canvas = parseColor(getComputedStyle(document.documentElement).backgroundColor);
    if (canvas && canvas.a > 0) color = over(canvas, color);
    for (const layer of layers.reverse()) color = over(layer, color);
    return color;
  }

  // ---- 2.4.7 / 2.4.11 / 1.4.11: the focused element --------------------------------------------
  /** Fraction of `el`'s box that is on screen and not covered by other content (sticky bars). */
  function visibleFraction(el) {
    const r = el.getBoundingClientRect();
    const steps = 5;
    let seen = 0;
    let total = 0;
    for (let i = 0; i < steps; i++) {
      for (let j = 0; j < steps; j++) {
        const x = r.left + ((i + 0.5) / steps) * r.width;
        const y = r.top + ((j + 0.5) / steps) * r.height;
        total += 1;
        if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) continue;
        const hit = document.elementFromPoint(x, y);
        if (hit && (hit === el || el.contains(hit) || hit.contains(el))) seen += 1;
      }
    }
    return total ? seen / total : 0;
  }

  function focusInfo() {
    const el = document.activeElement;
    if (!el || el === document.body || el === document.documentElement) return null;
    const cs = getComputedStyle(el);
    const outlineWidth = parseFloat(cs.outlineWidth) || 0;
    const hasOutline = cs.outlineStyle !== 'none' && outlineWidth >= 1;
    const outlineColor = parseColor(cs.outlineColor);
    const shadow = cs.boxShadow && cs.boxShadow !== 'none';
    // The outline is drawn outside the element (outline-offset), so it sits on the parent's background.
    const behind = backgroundOf(el.parentElement || el);
    const indicatorContrast = hasOutline && outlineColor ? contrast(over(outlineColor, behind), behind) : null;
    return {
      key: key(el),
      what: describe(el),
      tabIndex: el.tabIndex,
      hasOutline,
      outlineWidth,
      shadow,
      indicatorContrast,
      visible: visibleFraction(el),
      rect: (({ top, left, width, height }) => ({ top, left, width, height }))(el.getBoundingClientRect()),
    };
  }

  /** Elements a keyboard user should reach with Tab (shown, enabled, not tabindex=-1; inside an
   * open modal dialog, only its own). */
  function tabbables() {
    const modal = document.querySelector('dialog:modal');
    return [...(modal || document).querySelectorAll(FOCUSABLE)]
      .filter((el) => isShown(el) && !isDisabled(el) && el.tabIndex >= 0)
      // A radio group is one tab stop: its checked button, or its first (arrow keys move within it).
      .filter((el) => {
        if (el.type !== 'radio' || !el.name) return true;
        const group = [...document.querySelectorAll(`input[type="radio"][name="${CSS.escape(el.name)}"]`)]
          .filter((r) => isShown(r) && !isDisabled(r));
        return el === (group.find((r) => r.checked) || group[0]);
      })
      .map((el) => ({ key: key(el), what: describe(el) }));
  }

  const positiveTabindex = () => [...document.querySelectorAll('[tabindex]')]
    .filter((el) => el.tabIndex > 0).map(describe);

  // ---- 2.5.8 target size -----------------------------------------------------------------------
  function targetRect(el) {
    let r = el.getBoundingClientRect();
    // A label activates its checkbox or radio button, so it is part of the target.
    if ((el.type === 'checkbox' || el.type === 'radio') && el.labels) {
      for (const label of el.labels) {
        const l = label.getBoundingClientRect();
        const left = Math.min(r.left, l.left);
        const top = Math.min(r.top, l.top);
        const right = Math.max(r.right, l.right);
        const bottom = Math.max(r.bottom, l.bottom);
        r = { left, top, right, bottom, width: right - left, height: bottom - top };
      }
    }
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
  }

  /** A link inside a sentence or block of text (2.5.8's "inline" exception). */
  function isInline(el) {
    if (el.tagName !== 'A') return false;
    if (getComputedStyle(el).display !== 'inline') return false;
    const block = el.parentElement;
    return Boolean(block && block.textContent.trim().length > el.textContent.trim().length + 10);
  }

  const distanceToRect = (x, y, r) => Math.hypot(Math.max(r.left - x, 0, x - r.right), Math.max(r.top - y, 0, y - r.bottom));

  /** Targets under 24×24 CSS px whose 24 px circle overlaps another target or its circle
   * (2.5.8 Target Size (Minimum), with its spacing and inline exceptions). `extra` adds
   * pointer-only targets (e.g. resize handles) that aren't controls. */
  function targetSizeFailures(extra = '') {
    const els = [...document.querySelectorAll(extra ? `${TARGETS}, ${extra}` : TARGETS)]
      .filter((el) => isShown(el) && !isDisabled(el));
    const items = els.map((el) => ({ el, r: targetRect(el) }));
    const failures = [];
    for (const a of items) {
      if (a.r.width >= 24 && a.r.height >= 24) continue;
      if (isInline(a.el)) continue;
      const cx = (a.r.left + a.r.right) / 2;
      const cy = (a.r.top + a.r.bottom) / 2;
      const clash = items.find((b) => {
        if (b === a || b.el.contains(a.el) || a.el.contains(b.el)) return false;
        if (distanceToRect(cx, cy, b.r) < 12) return true;
        const small = b.r.width < 24 || b.r.height < 24;
        return small && Math.hypot(cx - (b.r.left + b.r.right) / 2, cy - (b.r.top + b.r.bottom) / 2) < 24;
      });
      if (clash) {
        failures.push({ what: describe(a.el), selector: a.el.className || a.el.tagName, width: Math.round(a.r.width),
          height: Math.round(a.r.height), clashesWith: describe(clash.el) });
      }
    }
    return failures;
  }

  const smallestTarget = () => {
    const sizes = [...document.querySelectorAll(TARGETS)].filter((el) => isShown(el) && !isDisabled(el) && !isInline(el))
      .map((el) => { const r = targetRect(el); return { what: describe(el), size: Math.min(r.width, r.height) }; });
    return sizes.sort((a, b) => a.size - b.size)[0] || null;
  };

  // ---- 1.4.4 / 1.4.10 / 1.4.12: overflow and clipping --------------------------------------------
  function horizontalOverflow() {
    const doc = document.documentElement;
    return { scrollWidth: doc.scrollWidth, innerWidth, overflows: doc.scrollWidth > innerWidth + 1 };
  }

  /** Text cut off by its own box: overflow hidden/clip (or ellipsis) with more content than room. */
  function clippedText() {
    const out = [];
    for (const el of document.querySelectorAll('body *')) {
      if (!isShown(el) || ['TEXTAREA', 'INPUT', 'SELECT', 'VIDEO', 'AUDIO', 'IMG', 'svg', 'CANVAS'].includes(el.tagName)) continue;
      const cs = getComputedStyle(el);
      const hidesX = ['hidden', 'clip'].includes(cs.overflowX) || cs.textOverflow === 'ellipsis';
      const hidesY = ['hidden', 'clip'].includes(cs.overflowY);
      if (!hidesX && !hidesY) continue;
      if (!el.textContent.trim()) continue;
      const cutX = hidesX && el.scrollWidth > el.clientWidth + 1;
      const cutY = hidesY && el.scrollHeight > el.clientHeight + 1;
      if (cutX || cutY) out.push({ what: describe(el), scroll: [el.scrollWidth, el.scrollHeight], client: [el.clientWidth, el.clientHeight] });
    }
    // Text that spills out of a fixed-height control without a scrollbar overlaps what follows.
    for (const el of document.querySelectorAll('button, .badge, .tag, label, summary, a[href]')) {
      if (!isShown(el)) continue;
      if (el.scrollWidth > el.clientWidth + 2 && getComputedStyle(el).overflowX === 'visible' && el.clientWidth > 0) {
        out.push({ what: describe(el), spills: 'x', scroll: [el.scrollWidth, el.scrollHeight], client: [el.clientWidth, el.clientHeight] });
      }
    }
    return out;
  }

  const TEXT_SPACING_CSS = `* { line-height: 1.5 !important; letter-spacing: 0.12em !important;
    word-spacing: 0.16em !important; } p { margin-bottom: 2em !important; }`;
  function applyTextSpacing() {
    const style = document.createElement('style');
    style.id = 'wcag-text-spacing';
    style.textContent = TEXT_SPACING_CSS;
    document.head.append(style);
  }

  // ---- 1.4.11: boundaries of form controls ------------------------------------------------------
  /** Text fields and selects whose border (their visible boundary) is under 3:1 against the
   * background around them. */
  function weakControlBorders() {
    const out = [];
    for (const el of document.querySelectorAll('input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="file"]), select, textarea')) {
      if (!isShown(el)) continue;
      const cs = getComputedStyle(el);
      if (cs.borderStyle === 'none' || (parseFloat(cs.borderTopWidth) || 0) < 1) {
        out.push({ what: describe(el), problem: 'no border' });
        continue;
      }
      const border = parseColor(cs.borderTopColor);
      const around = backgroundOf(el.parentElement);
      const inside = backgroundOf(el);
      const c = Math.max(contrast(over(border, around), around), contrast(inside, around));
      if (c < 3) out.push({ what: describe(el), contrast: Number(c.toFixed(2)) });
    }
    return out;
  }

  // ---- 1.3.1 / 2.4.x structure -------------------------------------------------------------------
  function structure() {
    const headings = [...document.querySelectorAll('h1, h2, h3, h4, h5, h6, [role="heading"]')]
      .filter(isShown)
      .map((h) => ({ level: Number(h.getAttribute('aria-level') || h.tagName.slice(1)), text: h.textContent.trim() }));
    const skips = [];
    for (let i = 1; i < headings.length; i++) {
      if (headings[i].level > headings[i - 1].level + 1) skips.push(`${headings[i - 1].text} (h${headings[i - 1].level}) → ${headings[i].text} (h${headings[i].level})`);
    }
    const landmark = (sel) => [...document.querySelectorAll(sel)].filter(isShown).length;
    return {
      lang: document.documentElement.lang,
      title: document.title,
      h1: headings.filter((h) => h.level === 1).map((h) => h.text),
      firstLevel: headings[0] && headings[0].level,
      skips,
      banner: landmark('header:not(main header):not(section header):not(article header), [role="banner"]'),
      nav: landmark('nav, [role="navigation"]'),
      main: landmark('main, [role="main"]'),
      contentinfo: landmark('footer:not(main footer):not(section footer):not(article footer), [role="contentinfo"]'),
      skipLink: (() => {
        const a = document.querySelector('a[href^="#"]');
        return a ? { text: a.textContent.trim(), target: Boolean(document.querySelector(a.getAttribute('href'))) } : null;
      })(),
    };
  }

  // ---- 2.2.2 / 2.3.3: motion --------------------------------------------------------------------
  function motion() {
    const running = document.getAnimations().filter((a) => a.playState === 'running').map((a) => describe(a.effect && a.effect.target));
    const transitions = [];
    for (const el of document.querySelectorAll('body *')) {
      if (!isShown(el)) continue;
      const d = getComputedStyle(el).transitionDuration.split(',').map((s) => parseFloat(s) || 0);
      const a = getComputedStyle(el).animationDuration.split(',').map((s) => parseFloat(s) || 0);
      if (Math.max(...d, ...a) > 0.01) transitions.push(describe(el));
    }
    return { running, transitions: transitions.slice(0, 10) };
  }

  // ---- forced colours ----------------------------------------------------------------------------
  /** Selected elements that have no visible edge in forced-colours mode (no border or outline). */
  function edgelessIn(selector) {
    return [...document.querySelectorAll(selector)].filter(isShown).filter((el) => {
      const cs = getComputedStyle(el);
      const border = cs.borderTopStyle !== 'none' && (parseFloat(cs.borderTopWidth) || 0) >= 1;
      const outline = cs.outlineStyle !== 'none' && (parseFloat(cs.outlineWidth) || 0) >= 1;
      return !border && !outline;
    }).map(describe);
  }

  /** Focuses the element with this key (see key()). */
  function focusKey(k) {
    const el = [...document.querySelectorAll(FOCUSABLE)].find((e) => key(e) === k);
    if (el) el.focus();
    return Boolean(el);
  }

  window.__wcag = {
    describe, key, focusKey, focusInfo, tabbables, positiveTabindex, targetSizeFailures, smallestTarget,
    horizontalOverflow, clippedText, applyTextSpacing, weakControlBorders, structure, motion, edgelessIn,
  };
})();
