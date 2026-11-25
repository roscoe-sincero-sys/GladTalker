# GladTalker Studio UI

The accessible **Cast → Script → Roll** frontend for GladTalker Studio (issue [#11](https://github.com/roscoe-sincero-sys/GladTalker/issues/11), phase 2), built with Svelte 5 and Vite.

You don't need Node to *use* it. The built files in `dist/` are committed, and the engine serves them:

```bash
python studio_server.py        # then open http://127.0.0.1:7860
```

## The three steps

1. **Cast:** choose a scene image (a portrait, a video-call screenshot, or a comic panel), then choose **Auto-Detect Cast**. Each face gets a numbered badge that combines color, shape, and number. Name each character, pick a voice, and **▶ Preview voice**. You can adjust regions in four ways:
   - drag them;
   - select one and use the **Arrow** keys to move it, **Alt+Arrow** to resize, and **Shift** for 10 px steps;
   - edit the X / Y / W / H fields;
   - use **Snap to face** or **Expand to tile**.
2. **Script:** paste a script (`Brain: Click it.`), or add lines one by one. Reorder lines with Up/Down or Alt+↑/↓, play any line, and apply the pronunciation Auto-Fix. Choose natural pace or a target length, and **▶ Preview full audio mix** to hear the whole soundtrack before rendering. Captions (`.srt` / `.vtt`) are on by default.
3. **Roll:** a checklist shows anything still missing and jumps straight to the field to fix. Pick the video size (16:9 HD / Full HD, 9:16, 1:1, or the source size), fit or fill, and Standard or Enhanced quality, then render. Progress is shown by stage and announced. The finished video plays with captions, playback speed, and a transcript, and has download links.

The project saves itself in the browser as you work. **Save project** / **Open…** store it as a `.gladtalker.json` file.

## Accessibility (WCAG 2.2 A & AA)

- **Structure:** native HTML controls with visible labels; `header` / `nav` / `main` / `footer` landmarks; a skip link. Errors are linked to their fields (`aria-describedby`, `aria-invalid`) and say how to fix the problem.
- **Keyboard and pointer:**
  - Everything works from the keyboard.
  - Dragging always has an alternative: steppers, numeric fields, and Up/Down buttons (2.5.7).
  - Shortcuts work only while their control has focus and need a modifier where it matters (2.1.4).
  - Targets are ≥ 36 px (2.5.8).
- **Focus:** a 3 px focus ring on everything, and scroll padding so the sticky bars never hide the focused element (2.4.7, 2.4.11).
- **Announcements:** status, progress, and errors go through persistent live regions without moving focus (4.1.3).
- **Speakers:** each one is shown by color plus shape plus number, never color alone (1.4.1).
- **Contrast:** ≥ 4.5:1 for text and ≥ 3:1 for borders, focus rings, and region outlines, in both light and dark themes. `tests/palette.test.js` checks this.
- **Display settings:** follows the OS light/dark theme, Windows High Contrast (`forced-colors`), and reduced motion. The layout reflows down to 320 px wide (1.4.10).
- **Help:** the Help button is in the same top-right place on every step (3.2.6).

## Development

```bash
cd studio-ui
npm install
npm run dev      # http://localhost:5173, proxies the API to an engine on :7860 (GLADTALKER_ENGINE to change)
npm test         # Vitest: logic, components, axe-core, contrast
npm run build    # writes dist/ (commit it)
npm run e2e      # Playwright: starts an engine and drives the built dist/ in Chromium (build first)
```

- **Setup:** `npm run e2e` needs a one-time `npx playwright install chromium`. It uses `../venv` by default; set `GLADTALKER_PYTHON=../venv-dml/Scripts/python.exe` for the DirectML venv.
- **What it covers:**
  - axe-core WCAG 2.2 A/AA scans of every step (light and dark);
  - keyboard editing, including focus after reordering at the top and bottom of the list;
  - the real audio preview and autosave;
  - Auto-Fix suggestions hidden as soon as their line changes;
  - a slow import racing an Open project;
  - a render accepted after leaving Roll, which Roll follows when shown again, a slow re-attach to a saved render that blocks a second one, and a late Cancel acknowledgement (render jobs are mocked here, so no GPU time);
  - a project file with forged job ids, which makes no job request;
  - a saved project file that contains no dialogue removed after a preview;
  - a duplicate preview click while one is being submitted;
  - in token mode, a caption download failure that still shows the video and offers a retry;
  - the `#token=` launch against a second engine started with `--token`;
  - the Help dialog and 320 px reflow.
- **Real render:** set `GLADTALKER_E2E_RENDER=1` to also render a short video (a few minutes).
- **Engine token:** if the engine runs with `--token`, open the UI as `http://127.0.0.1:7860/#token=…`. The page itself loads without the token. The token then moves into session storage, is removed from the address bar, and is sent with every request. Images and media are fetched with it and shown through `blob:` URLs.
- **Dev server and the Origin check:** `npm run dev` forwards requests to the engine unchanged, Origin and Host included. A page on `http://localhost:5173` is therefore same-origin to the engine, while any other site's requests through the proxy are still refused with 403. No `--allow-origin` is needed.

## Layout

| Path | What |
| :--- | :--- |
| `src/App.svelte` | Shell: header with stepper, project buttons, and Help; live regions; autosave |
| `src/steps/` | `CastStep`, `ScriptStep`, `RollStep` |
| `src/components/` | Stage, cast card, dialogue row, number field, progress, setup, help, badge |
| `src/lib/` | API client, project state and persistence, region geometry, validation, palette |
| `tests/` / `e2e/` | Vitest / Playwright |
