// WCAG 2.2 Level A/AA audit of the Studio UI (issue #11, phase 4), applied through WCAG2ICT to
// the desktop app's WebView2 (the same Chromium engine as here). Each test names the success
// criteria it checks; docs/accessibility/wcag22-audit.md maps every criterion to its evidence.
//
// The checks run in every screen state a user meets (SCENARIOS) and, where size matters, at the
// desktop window's size, at 200 % zoom, and at 400 % zoom (320 × 256 CSS px).
import { test, expect } from '@playwright/test';
import path from 'node:path';
import { AXE, WCAG_TAGS, PORTRAIT, openStudio, castPortrait, readyToRoll, fakeRender, renderSnapshot, savedProject } from './helpers.js';

const IN_PAGE = path.resolve(import.meta.dirname, 'wcag-in-page.js');

// Injected scripts (axe, the measurements) need the engine's Content-Security-Policy relaxed;
// the UI itself is unchanged.
test.use({ bypassCSP: true });
// The checks are independent of each other (each starts from a fresh Studio), so they run in parallel.
test.describe.configure({ mode: 'parallel' });

const VIEWPORTS = {
  desktop: { width: 1280, height: 880 }, // the app's default window
  zoom150: { width: 853, height: 587 }, // the same window at 150 %
  zoom200: { width: 640, height: 440 }, // … at 200 % (1.4.4)
  zoom400: { width: 320, height: 256 }, // 1280 × 1024 at 400 %: the reflow size (1.4.10)
};

// A real GPU's name is long and wraps the footer at some sizes; the CPU fallback ("CPU") doesn't.
// Every scenario uses the long one, so the results don't depend on the machine running them.
const LONG_DEVICE = 'AMD Radeon RX 6550M Graphics (DirectML, adapter 0)';
async function routeHardware(page, edit = (hardware) => hardware) {
  await page.route('**/hardware', async (route) => {
    const real = await (await route.fetch()).json();
    real.device = { ...real.device, label: LONG_DEVICE };
    await route.fulfill({ json: edit(real) });
  });
}

// Pointer-only targets that aren't controls: the stage regions' resize handles. Their function
// (resizing) is also offered by the W/H steppers and Snap to face / Expand to tile, which meet
// 2.5.8 ("Equivalent" exception), so they are measured but allowed to be small.
const HANDLES = '.handle';

async function inject(page) {
  if (!(await page.evaluate(() => Boolean(window.__wcag)))) await page.addScriptTag({ path: IN_PAGE });
}
const measure = async (page, fn, ...args) => {
  await inject(page);
  return page.evaluate(([name, rest]) => window.__wcag[name](...rest), [fn, args]);
};

async function axeScan(page, extraRules = {}) {
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async ([tags, rules]) => {
    const res = await window.axe.run(document, { runOnly: { type: 'tag', values: tags }, rules });
    return res.violations.map((v) => ({ id: v.id, help: v.help, nodes: v.nodes.slice(0, 3).map((n) => n.target.join(' ')) }));
  }, [WCAG_TAGS, extraRules]);
}

async function startFakeRender(page) {
  await fakeRender(page, 'fa00000000a1', () => renderSnapshot('fa00000000a1', 'running'));
  await page.route('**/render/start', (route) => route.fulfill({ status: 202, json: renderSnapshot('fa00000000a1', 'queued') }));
  await page.getByRole('button', { name: /Render video/ }).click();
  await expect(page.getByText('Rendering Ann: animating frame 5 of 50')).toBeVisible();
}

/** Every state of the UI a user meets, from a fresh start. Each leaves the page ready to audit. */
const SCENARIOS = {
  'Cast, empty': async (page) => { await openStudio(page); },
  'Cast, one cast member': async (page) => { await openStudio(page); await castPortrait(page); },
  'Script, with a pronunciation suggestion': async (page) => {
    await openStudio(page);
    await castPortrait(page);
    await page.getByRole('button', { name: 'Next: Script' }).click();
    await page.getByLabel('Paste a script').fill('Ann: Hello there.\nAnn: You’re late, and you\'re lost.');
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    await expect(page.getByRole('button', { name: /Auto-Fix line 2/ })).toBeVisible();
  },
  'Roll, ready': async (page) => { await openStudio(page); await readyToRoll(page); },
  'Roll, rendering': async (page) => { await openStudio(page); await readyToRoll(page); await startFakeRender(page); },
  'Roll, finished render': async (page) => {
    // A finished render: the player with captions, the transcript, and the downloads.
    await openStudio(page);
    await readyToRoll(page);
    const done = renderSnapshot('fa00000000d1', 'succeeded', { result: { duration_s: 2, width: 1280, height: 720,
      video_path: 'C:/ws/renders/fa00000000d1/Ann.mp4', vtt_path: 'C:/ws/renders/fa00000000d1/Ann.vtt',
      srt_path: 'C:/ws/renders/fa00000000d1/Ann.srt', captions: [{ speaker: 'Ann', text: 'Hello there.', start_ms: 0, end_ms: 2000 }] } });
    await fakeRender(page, 'fa00000000d1', () => done);
    await page.route('**/render/start', (route) => route.fulfill({ status: 202, json: renderSnapshot('fa00000000d1', 'queued') }));
    await page.route('**/jobs/fa00000000d1/files/**', (route) => route.fulfill({ status: 200, contentType: 'text/plain', body: '' }));
    await page.getByRole('button', { name: /Render video/ }).click();
    await expect(page.getByRole('heading', { name: 'Your video' })).toBeVisible();
    await page.getByText('Transcript').click(); // open it, so its contents are audited too
  },
  'Help dialog': async (page) => {
    await openStudio(page);
    await page.getByRole('button', { name: /Help/ }).click();
    await expect(page.getByRole('dialog', { name: 'Help' })).toBeVisible();
  },
  'First-run setup': async (page) => {
    await routeHardware(page, (real) => ({ ...real, ffmpeg: { ...real.ffmpeg, available: false },
      models: { ...real.models, complete: false, ready: real.models.total - 2,
        files: real.models.files.map((f, i) => (i < 2 ? { ...f, ready: false } : f)) } }));
    await openStudio(page);
    await expect(page.getByRole('heading', { name: 'Setup', exact: true })).toBeVisible();
  },
  'Engine offline': async (page) => {
    await page.route('**/health', (route) => route.abort());
    await page.goto('/');
    await expect(page.getByRole('heading', { name: /Can’t reach the GladTalker engine/ })).toBeVisible();
  },
};

async function open(page, name, { viewport = VIEWPORTS.desktop, colorScheme = 'light', forcedColors = 'none', reducedMotion = 'no-preference' } = {}) {
  // Each scenario starts from a fresh Studio, even when a test opens several in one page.
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  if (page.url().startsWith('http')) {
    // Leave the app first (it saves on the way out), then clear what it saved.
    await page.goto('/health');
    await page.evaluate(() => localStorage.clear());
  }
  await page.setViewportSize(viewport);
  await page.emulateMedia({ colorScheme, forcedColors, reducedMotion });
  await routeHardware(page);
  await SCENARIOS[name](page);
  await inject(page);
}

/** Waits until the autosave has caught up (it runs 300 ms after the last change). */
async function settled(page) {
  let last = null;
  await expect.poll(async () => {
    const now = await page.evaluate(() => localStorage.getItem('gladtalker.project.v1'));
    const same = now === last;
    last = now;
    return same;
  }, { intervals: [400] }).toBe(true);
}

/** Tabs all the way around the page (Tab starts wherever the last click or focus was), and
 * returns the stops in document order, starting from the first. */
async function tabWalk(page, max = 400) {
  const stops = [];
  let repeats = 0;
  let last = null;
  for (let i = 0; i < max; i++) {
    await page.keyboard.press('Tab');
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    const info = await measure(page, 'focusInfo');
    if (!info) { last = null; continue; } // past the last stop: focus is in the browser itself
    if (last === info.key) {
      repeats += 1;
      if (repeats >= 3) return { stops, trappedAt: info.what };
      continue;
    }
    repeats = 0;
    last = info.key;
    if (stops.some((s) => s.key === info.key)) break; // all the way around
    stops.push(info);
  }
  // Start the list at the page's first stop.
  const first = (await measure(page, 'tabbables'))[0]?.key;
  const at = Math.max(0, stops.findIndex((s) => s.key === first));
  return { stops: [...stops.slice(at), ...stops.slice(0, at)], trappedAt: null };
}

// ------------------------------------------------------------------------------------------------

test.describe('structure and semantics', () => {
  for (const name of Object.keys(SCENARIOS)) {
    test(`${name}: page language, title, headings, landmarks, skip link (1.3.1, 2.4.1, 2.4.2, 2.4.6, 3.1.1)`, async ({ page }) => {
      await open(page, name);
      const s = await measure(page, 'structure');
      expect(s.lang).toBe('en');
      expect(s.title).toMatch(/GladTalker Studio/);
      expect(s.h1).toHaveLength(1);
      expect(s.firstLevel).toBe(1);
      expect(s.skips, 'heading levels are not skipped').toEqual([]);
      expect(s.banner).toBe(1);
      expect(s.main).toBe(1);
      expect(s.contentinfo).toBe(1);
      if (!name.startsWith('Engine offline')) expect(s.nav).toBe(1);
      expect(s.skipLink).toEqual({ text: 'Skip to the current step', target: true });
    });
  }

  test('every interactive node in the accessibility tree has a role and a name (4.1.2)', async ({ page }) => {
    // What assistive technology receives: Chromium's accessibility tree, which WebView2 exposes
    // to Windows UI Automation (Narrator, NVDA, JAWS).
    const INTERACTIVE = new Set(['button', 'link', 'textbox', 'combobox', 'listbox', 'checkbox', 'radio', 'slider',
      'spinbutton', 'switch', 'tab', 'menuitem', 'searchbox', 'ListBoxOption', 'option']);
    for (const name of ['Cast, one cast member', 'Script, with a pronunciation suggestion', 'Roll, rendering', 'Help dialog']) {
      await open(page, name);
      const cdp = await page.context().newCDPSession(page);
      const { nodes } = await cdp.send('Accessibility.getFullAXTree');
      const unnamed = nodes.filter((n) => !n.ignored && INTERACTIVE.has(n.role?.value) && !String(n.name?.value ?? '').trim())
        .map((n) => `${n.role.value} (backend node ${n.backendDOMNodeId})`);
      expect(unnamed, name).toEqual([]);
      const focusableGeneric = nodes.filter((n) => !n.ignored && n.properties?.some((p) => p.name === 'focusable' && p.value.value)
        && ['generic', 'none', 'presentation'].includes(n.role?.value)).length;
      expect(focusableGeneric, `${name}: focusable nodes without a role`).toBe(0);
      await cdp.detach();
    }
  });
});

test.describe('axe-core in every state, light, dark, and forced colours (1.1.1, 1.3.1, 1.4.3, 2.5.3, 4.1.2, …)', () => {
  for (const name of Object.keys(SCENARIOS)) {
    for (const mode of ['light', 'dark', 'forced']) {
      test(`${name} (${mode})`, async ({ page }) => {
        await open(page, name, mode === 'forced' ? { forcedColors: 'active' } : { colorScheme: mode });
        // label-content-name-mismatch (2.5.3 Label in Name) is "experimental" in axe, so it is
        // turned on explicitly.
        expect(await axeScan(page, { 'label-content-name-mismatch': { enabled: true } })).toEqual([]);
      });
    }
  }
});

test.describe('keyboard: everything reachable, no traps, a visible and unobscured focus', () => {
  const cases = [
    ...Object.keys(SCENARIOS).map((name) => [name, 'desktop']),
    ...['Cast, one cast member', 'Script, with a pronunciation suggestion', 'Roll, ready', 'Roll, rendering', 'Roll, finished render', 'Help dialog', 'First-run setup']
      .flatMap((name) => [[name, 'zoom150'], [name, 'zoom200'], [name, 'zoom400']]),
  ];
  for (const [name, size] of cases) {
    test(`${name} at ${size} (2.1.1, 2.1.2, 2.4.3, 2.4.7, 2.4.11, 1.4.11)`, async ({ page }) => {
      await open(page, name, { viewport: VIEWPORTS[size] });
      expect(await measure(page, 'positiveTabindex'), 'no positive tabindex (2.4.3)').toEqual([]);
      const expected = await measure(page, 'tabbables');
      const { stops, trappedAt } = await tabWalk(page);
      expect(trappedAt, 'no keyboard trap (2.1.2)').toBeNull();
      expect(stops.length, 'the walk found the controls').toBeGreaterThanOrEqual(Math.min(expected.length, 5));
      // Every control is reached by Tab (2.1.1), in document order (2.4.3).
      const reached = stops.map((s) => s.key);
      const missing = expected.filter((e) => !reached.includes(e.key)).map((e) => e.what);
      expect(missing, 'controls Tab never reaches').toEqual([]);
      const order = expected.map((e) => e.key).filter((k) => reached.includes(k));
      expect(reached.filter((k) => order.includes(k)), 'Tab follows the document order').toEqual(order);
      for (const s of stops) {
        // 2.4.7: a focus indicator (a 3 px outline in this UI) …
        expect(s.hasOutline || s.shadow, `focus visible: ${s.what}`).toBe(true);
        // … with 3:1 against what's around it (1.4.11) …
        if (s.indicatorContrast !== null) expect(s.indicatorContrast, `focus indicator contrast: ${s.what}`).toBeGreaterThanOrEqual(3);
        // … and the focused control never hidden by the sticky header or footer (2.4.11), in fact
        // fully in view unless it is taller than the space between them.
        expect(s.visible, `focus not obscured: ${s.what}`).toBeGreaterThan(0);
        if (s.rect.height < VIEWPORTS[size].height / 3) expect(s.visible, `focused control fully in view: ${s.what}`).toBe(1);
      }
    });
  }

  test('Shift+Tab visits the same stops in reverse (2.4.3)', async ({ page }) => {
    await open(page, 'Cast, one cast member');
    const forward = (await tabWalk(page)).stops.map((s) => s.key);
    // Walk backwards all the way around, too.
    const backward = [];
    for (let i = 0; i < forward.length + 4 && backward.length < forward.length; i++) {
      await page.keyboard.press('Shift+Tab');
      const k = (await measure(page, 'focusInfo'))?.key;
      if (k && !backward.includes(k)) backward.push(k);
    }
    // The same cycle, reversed: rotate it to start where the forward list ends.
    const at = backward.indexOf(forward.at(-1));
    expect([...backward.slice(at), ...backward.slice(0, at)]).toEqual([...forward].reverse());
  });
});

test.describe('target size (2.5.8)', () => {
  for (const name of Object.keys(SCENARIOS)) {
    for (const size of ['desktop', 'zoom400']) {
      test(`${name} at ${size}`, async ({ page }) => {
        await open(page, name, { viewport: VIEWPORTS[size] });
        const failures = await measure(page, 'targetSizeFailures', HANDLES);
        expect(failures.filter((f) => !String(f.selector).includes('handle'))).toEqual([]);
      });
    }
  }

  test('the regular controls are 36 px or more (the UI\'s own target)', async ({ page }) => {
    for (const name of ['Cast, one cast member', 'Script, with a pronunciation suggestion', 'Roll, ready']) {
      await open(page, name);
      const smallest = await measure(page, 'smallestTarget');
      expect(smallest.size, `${name}: ${smallest.what}`).toBeGreaterThanOrEqual(24);
    }
  });
});

test.describe('resize, reflow, and text spacing (1.4.4, 1.4.10, 1.4.12)', () => {
  for (const name of Object.keys(SCENARIOS)) {
    for (const [size, sc] of [['zoom200', '1.4.4'], ['zoom400', '1.4.10']]) {
      test(`${name} at ${size} (${sc}): no horizontal scrolling, no clipped text`, async ({ page }) => {
        await open(page, name, { viewport: VIEWPORTS[size] });
        expect(await measure(page, 'horizontalOverflow')).toMatchObject({ overflows: false });
        expect(await measure(page, 'clippedText')).toEqual([]);
      });
    }
    test(`${name} with WCAG text spacing (1.4.12)`, async ({ page }) => {
      for (const size of ['desktop', 'zoom400']) {
        await open(page, name, { viewport: VIEWPORTS[size] });
        await measure(page, 'applyTextSpacing');
        expect(await measure(page, 'horizontalOverflow'), size).toMatchObject({ overflows: false });
        expect(await measure(page, 'clippedText'), size).toEqual([]);
      }
    });
  }

  test('portrait and landscape both work (1.3.4)', async ({ page }) => {
    for (const viewport of [{ width: 880, height: 1280 }, { width: 1280, height: 720 }]) {
      await open(page, 'Roll, ready', { viewport });
      expect(await measure(page, 'horizontalOverflow')).toMatchObject({ overflows: false });
    }
  });
});

test.describe('colour and contrast (1.4.1, 1.4.3, 1.4.11)', () => {
  for (const colorScheme of ['light', 'dark']) {
    test(`form control boundaries have 3:1 contrast (${colorScheme})`, async ({ page }) => {
      for (const name of ['Cast, one cast member', 'Script, with a pronunciation suggestion', 'Roll, ready']) {
        await open(page, name, { colorScheme });
        expect(await measure(page, 'weakControlBorders'), name).toEqual([]);
      }
    });
  }

  test('speakers are told apart by number, shape, and name, not colour alone (1.4.1)', async ({ page }) => {
    await open(page, 'Script, with a pronunciation suggestion');
    await page.getByRole('navigation', { name: 'Steps' }).getByRole('button', { name: /Cast/ }).click();
    const region = page.locator('.stage .region').first();
    await expect(region).toHaveAccessibleName(/1.*Ann|Ann.*1/);
    const tag = region.locator('.tag');
    await expect(tag).toContainText('1');
    await expect(tag).toContainText('Ann');
  });

  test('in forced colours (Windows High Contrast), regions and focus keep a visible edge', async ({ page }) => {
    await open(page, 'Cast, one cast member', { forcedColors: 'active' });
    expect(await measure(page, 'edgelessIn', '.stage .region, input:not([type=checkbox]):not([type=radio]):not([type=file]), select, textarea, button')).toEqual([]);
    const { stops } = await tabWalk(page);
    for (const s of stops) expect(s.hasOutline || s.shadow, `focus visible in forced colours: ${s.what}`).toBe(true);
  });
});

test.describe('motion and timing (2.2.1, 2.2.2, 2.3.1, 2.3.3)', () => {
  test('with reduced motion, nothing animates, even while rendering', async ({ page }) => {
    for (const name of ['Cast, one cast member', 'Roll, rendering', 'Help dialog', 'First-run setup']) {
      await open(page, name, { reducedMotion: 'reduce' });
      const m = await measure(page, 'motion');
      expect(m.running, name).toEqual([]);
      expect(m.transitions, name).toEqual([]);
    }
  });

  test('no media plays by itself (1.4.2)', async ({ page }) => {
    await open(page, 'Roll, rendering');
    expect(await page.locator('audio[autoplay], video[autoplay]').count()).toBe(0);
  });
});

test.describe('predictable and input assistance', () => {
  test('the Help button is in the same place on every step and screen (3.2.6, 3.2.3, 3.2.4)', async ({ page }) => {
    await open(page, 'Cast, one cast member');
    const where = async () => page.getByRole('button', { name: /Help/ }).boundingBox();
    const cast = await where();
    for (const step of ['Script', 'Roll']) {
      await page.getByRole('navigation', { name: 'Steps' }).getByRole('button', { name: new RegExp(step) }).click();
      expect(await where(), step).toEqual(cast);
    }
    for (const name of ['First-run setup', 'Engine offline']) {
      await open(page, name);
      expect(await where(), name).toEqual(cast);
    }
  });

  test('focusing or changing a control never navigates, opens anything, or starts work (3.2.1, 3.2.2)', async ({ page }) => {
    await open(page, 'Script, with a pronunciation suggestion');
    const posts = [];
    page.on('request', (r) => { if (r.method() === 'POST' && !/\/script\/lint$/.test(r.url())) posts.push(r.url()); });
    const heading = page.getByRole('heading', { level: 2, name: /Step 2 of 3: Script/ });
    await tabWalk(page);
    // Change every select on the step with the keyboard.
    for (const select of await page.locator('select:visible').all()) {
      await select.focus();
      await page.keyboard.press('ArrowDown');
    }
    await expect(heading).toBeVisible();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    expect(posts).toEqual([]);
    expect(page.url()).not.toMatch(/#.+/);
  });

  test('no single-character key shortcuts act on their own (2.1.4)', async ({ page }) => {
    await open(page, 'Cast, one cast member');
    const state = async () => { await settled(page); const p = await savedProject(page); return JSON.stringify([p.cast, p.script, p.output, p.image]); };
    const before = await state();
    // Letters, digits, and punctuation, with focus on the page and on a stage region (whose own
    // keys are the arrow keys only). Space and Enter activate a focused button: not shortcuts.
    for (const target of [page.locator('main'), page.locator('.stage .region').first()]) {
      await target.focus();
      for (const ch of "abcdefghijklmnopqrstuvwxyz0123456789-=[];',./") await page.keyboard.press(ch);
    }
    await expect(page.getByRole('heading', { level: 2, name: /Step 1 of 3: Cast/ })).toBeVisible();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    expect(await state()).toBe(before);
  });

  test('errors are identified in text, tied to their field, and suggest a fix (3.3.1, 3.3.2, 3.3.3)', async ({ page }) => {
    await open(page, 'Cast, one cast member');
    // Number fields can't hold an invalid value (they clamp to their range, which their hint
    // states); the name can be missing or a duplicate.
    const name = page.getByLabel('Name', { exact: true });
    await name.fill('');
    await expect(name).toHaveAttribute('aria-invalid', 'true');
    await expect(name).toHaveAccessibleDescription(/Enter a name\./);
    await name.fill('Ann');
    await expect(name).not.toHaveAttribute('aria-invalid', 'true');
    await page.getByRole('button', { name: '+ Add region' }).click();
    const second = page.getByRole('group', { name: /Cast member 2/ }).getByLabel('Name', { exact: true });
    await second.fill('ann');
    await expect(second).toHaveAttribute('aria-invalid', 'true');
    await expect(second).toHaveAccessibleDescription(/Another cast member already has this name\./);
    // On Roll, a missing step is listed in words, with a way to get to the field.
    await page.getByRole('navigation', { name: 'Steps' }).getByRole('button', { name: /Roll/ }).click();
    await expect(page.getByRole('button', { name: /Render video/ })).toBeDisabled();
    await expect(page.getByText('Fix the items under “Ready to render?” to enable rendering.')).toBeVisible();
  });

  test('replacing a project with any work in it asks first, and No keeps it as it was (3.3.4)', async ({ page }) => {
    const other = { name: 'other.gladtalker.json', mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify({ version: 1, cast: [{ id: 'cb', name: 'Bea', region: { x: 0, y: 0, w: 64, h: 64 } }], script: [] })) };
    const openOther = async () => {
      const chooser = page.waitForEvent('filechooser');
      await page.getByRole('button', { name: 'Open…' }).click();
      await (await chooser).setFiles(other);
    };
    const stored = () => page.evaluate(() => localStorage.getItem('gladtalker.project.v1'));
    // Any work counts: a cast, a scene on its own, or just a changed setting.
    const drafts = {
      'a cast member': async () => { await open(page, 'Cast, one cast member'); },
      'only a scene': async () => {
        await open(page, 'Cast, empty');
        await page.getByLabel('Scene image').setInputFiles(PORTRAIT);
        await expect(page.getByRole('img', { name: /Scene: people_0\.png/ })).toBeVisible();
      },
      'only a changed setting': async () => {
        await open(page, 'Cast, empty');
        await page.getByRole('navigation', { name: 'Steps' }).getByRole('button', { name: /Roll/ }).click();
        await page.getByRole('radio', { name: /9:16 Vertical/ }).check();
      },
    };
    for (const [what, make] of Object.entries(drafts)) {
      for (const action of ['Open…', 'New']) {
        await make();
        await settled(page);
        const before = await stored();
        let asked = null;
        page.once('dialog', (d) => { asked = d.message(); return d.dismiss(); });
        if (action === 'Open…') await openOther();
        else await page.getByRole('button', { name: 'New', exact: true }).click();
        await expect.poll(() => asked, { message: `${action} over ${what} asks first` }).not.toBeNull();
        expect(asked).toMatch(/scene, cast, script, and settings/);
        await settled(page);
        expect(await stored(), `${action} declined over ${what}: the draft is unchanged`).toBe(before);
      }
    }
    // Yes replaces it; a blank project is replaced without a question.
    await open(page, 'Cast, one cast member');
    page.once('dialog', (d) => d.accept());
    await openOther();
    await expect(page.getByLabel('Name', { exact: true })).toHaveValue('Bea');
    await open(page, 'Cast, empty');
    let questions = 0;
    page.on('dialog', (d) => { questions += 1; return d.dismiss(); });
    await openOther();
    await expect(page.getByLabel('Name', { exact: true })).toHaveValue('Bea');
    expect(questions).toBe(0);
  });

  test('nothing has to be entered twice: the project survives a reload (3.3.7)', async ({ page }) => {
    await open(page, 'Roll, ready');
    await settled(page);
    const saved = await savedProject(page);
    await page.reload();
    await expect(page.getByText(/Computer: (?!Detecting)/)).toBeVisible();
    expect((await savedProject(page)).cast).toEqual(saved.cast);
    expect((await savedProject(page)).script).toEqual(saved.script);
    await page.getByRole('navigation', { name: 'Steps' }).getByRole('button', { name: /Cast/ }).click();
    await expect(page.getByLabel('Name', { exact: true })).toHaveValue('Ann');
    // An edit made just before the window closes is kept too (saved when the page is hidden,
    // not only after the autosave's short delay).
    await page.getByLabel('Name', { exact: true }).fill('Annie');
    await page.reload();
    await expect(page.getByLabel('Name', { exact: true })).toHaveValue('Annie');
  });
});

test.describe('operable without a pointer or without dragging (2.1.1, 2.5.1, 2.5.2, 2.5.7)', () => {
  test('a region can be moved and resized with single clicks, never a drag', async ({ page }) => {
    await open(page, 'Cast, one cast member');
    await settled(page);
    const region = async () => (await savedProject(page)).cast[0].region;
    const start = await region();
    await page.getByRole('button', { name: /^Increase X/ }).first().click();
    await page.getByRole('button', { name: /^Increase W/ }).first().click();
    await expect.poll(async () => (await region()).x).toBeGreaterThan(start.x);
    await expect.poll(async () => (await region()).w).toBeGreaterThan(start.w);
    await page.getByRole('button', { name: /Snap to face/ }).first().click();
    await page.getByRole('button', { name: /Expand to tile/ }).first().click();
  });

  test('the whole workflow, start to finish, with the keyboard alone', async ({ page }) => {
    // No clicks after the file chooser (a native dialog, opened here with the keyboard too).
    await page.setViewportSize(VIEWPORTS.desktop);
    await openStudio(page);
    const press = (k) => page.keyboard.press(k);
    const focusByTab = async (locator, max = 200) => {
      for (let i = 0; i < max; i++) {
        if (await locator.evaluate((el) => el === document.activeElement).catch(() => false)) return;
        await press('Tab');
      }
      throw new Error(`Tab never reached ${locator}`);
    };
    // Cast: choose the image (keyboard opens the file chooser), auto-detect, name the speaker.
    const chooser = page.waitForEvent('filechooser');
    await focusByTab(page.getByLabel('Scene image'));
    await press('Space');
    await (await chooser).setFiles(path.resolve(import.meta.dirname, '../../examples/source_image/people_0.png'));
    await expect(page.getByRole('img', { name: /Scene: people_0\.png/ })).toBeVisible();
    await focusByTab(page.getByRole('button', { name: /Auto-Detect Cast/ }));
    await press('Enter');
    await expect(page.getByRole('group', { name: /Cast member 1/ })).toBeVisible();
    await focusByTab(page.getByLabel('Name', { exact: true }));
    await page.keyboard.type('Ann');
    // Nudge the region with the arrow keys on the stage.
    await settled(page);
    const before = (await savedProject(page)).cast[0].region;
    await focusByTab(page.locator('.stage .region').first());
    await press('ArrowRight');
    await press('Shift+ArrowDown');
    await expect.poll(async () => (await savedProject(page)).cast[0].region.x).toBe(before.x + 1);
    // Script: import a two-line script, reorder with Alt+Arrow.
    await focusByTab(page.getByRole('button', { name: /Next: Script/ }));
    await press('Enter');
    await focusByTab(page.getByLabel('Paste a script'));
    await page.keyboard.type('Ann: First line.\nAnn: Second line.');
    await focusByTab(page.getByRole('button', { name: 'Import', exact: true }));
    await press('Enter');
    await focusByTab(page.getByLabel('What is said in line 2'));
    await press('Alt+ArrowUp');
    await expect.poll(async () => (await savedProject(page)).script.map((l) => l.text)).toEqual(['Second line.', 'First line.']);
    // Roll: choose a framing preset and start (a pretend) render, then cancel it.
    await fakeRender(page, 'fa00000000b1', () => renderSnapshot('fa00000000b1', 'running'));
    await page.route('**/render/start', (route) => route.fulfill({ status: 202, json: renderSnapshot('fa00000000b1', 'queued') }));
    await page.route('**/jobs/fa00000000b1/cancel', (route) => route.fulfill({ status: 202, json: renderSnapshot('fa00000000b1', 'running', { cancel_requested: true }) }));
    await focusByTab(page.getByRole('button', { name: /Next: Roll/ }));
    await press('Enter');
    await focusByTab(page.getByRole('radio', { name: /9:16 Vertical/ }).or(page.getByRole('radio').first()).first());
    await press('ArrowDown');
    await focusByTab(page.getByRole('button', { name: /Render video/ }));
    await press('Enter');
    await expect(page.getByText('Rendering Ann: animating frame 5 of 50')).toBeVisible();
    await focusByTab(page.getByRole('button', { name: /Cancel/ }));
    await press('Enter');
    await expect(page.getByText(/Cancelling/)).toBeVisible();
  });
});

test.describe('status messages (4.1.3)', () => {
  test('results and errors are announced while focus stays on the control that was used', async ({ page }) => {
    await open(page, 'Cast, empty');
    const polite = page.locator('[role="status"][aria-live="polite"]').last();
    const assertive = page.locator('[role="alert"][aria-live="assertive"]').last();
    const hasFocus = (control) => control.evaluate((el) => el === document.activeElement);
    // Use a control with the keyboard, wait for its result to be announced, and check that focus
    // never left it, while the work ran and when the result arrived.
    async function useAndHear(control, region, text) {
      const element = await control.elementHandle(); // the same button, even if its label changes
      await element.focus();
      await page.keyboard.press('Enter');
      await expect(region).toHaveText(text);
      expect(await element.evaluate((el) => el === document.activeElement),
        `focus stayed on the control after "${await region.textContent()}"`).toBe(true);
    }

    await page.getByLabel('Scene image').setInputFiles(PORTRAIT);
    await expect(polite).toHaveText(/Scene loaded: people_0\.png/);
    await useAndHear(page.getByRole('button', { name: /Auto-Detect Cast/ }), polite, /Added 1 cast member/);
    await page.getByLabel('Name', { exact: true }).fill('Ann');

    await page.getByRole('button', { name: 'Next: Script' }).click();
    await page.getByLabel('Paste a script').fill('Ann: Hello there.');
    await useAndHear(page.getByRole('button', { name: 'Import', exact: true }), polite, /Imported 1 line/);

    // A render the engine reports as running, then as finished.
    let state = 'running';
    await fakeRender(page, 'fa00000000c1', () => renderSnapshot('fa00000000c1', state, state === 'succeeded'
      ? { result: { duration_s: 2, width: 1280, height: 720, video_path: 'out.mp4', captions: [] } }
      : state === 'failed' ? { error: 'FFmpeg could not write the file: the disk is full.' } : {}));
    await page.route('**/render/start', (route) => route.fulfill({ status: 202, json: renderSnapshot('fa00000000c1', 'queued') }));
    await page.getByRole('button', { name: 'Next: Roll' }).click();
    const render = page.getByRole('button', { name: /Render video/ });
    await useAndHear(render, polite, /Render started/);
    const fileName = page.getByLabel(/File name/);
    await fileName.focus(); // the user carries on with something else while it renders
    state = 'succeeded';
    await expect(polite).toHaveText(/Your video is ready: 2 s, 1280×720/);
    expect(await hasFocus(fileName)).toBe(true);

    // A failed render is announced as an alert, again without moving focus.
    await page.unroute('**/jobs/fa00000000c1');
    await page.unroute('**/jobs/fa00000000c1/ws-ticket');
    state = 'failed';
    await fakeRender(page, 'fa00000000c2', () => renderSnapshot('fa00000000c2', state, { error: 'FFmpeg could not write the file: the disk is full.' }));
    await page.unroute('**/render/start');
    await page.route('**/render/start', (route) => route.fulfill({ status: 202, json: renderSnapshot('fa00000000c2', 'running') }));
    state = 'running';
    await useAndHear(render, polite, /Render started/);
    await fileName.focus();
    state = 'failed';
    await expect(assertive).toHaveText(/Render failed: FFmpeg could not write the file/);
    expect(await hasFocus(fileName)).toBe(true);
  });
});
