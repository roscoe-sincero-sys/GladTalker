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

## Desktop app

`src-tauri/` wraps the same UI in a **Tauri v2** desktop app, **GladTalker Studio**. Python isn't bundled: the app uses your GladTalker folder and its Python environment, the same one you'd use for `python studio_server.py`.

**Finding the engine.** When it starts, the app looks for a folder that contains `studio_server.py`, in this order:
1. a folder you just chose with **Choose GladTalker folder…** (an explicit choice always wins);
2. the `GLADTALKER_HOME` environment variable;
3. the folder you chose in an earlier session;
4. the folders around the app itself (for example, a development build inside the repository).

Inside that folder it uses Python from `venv-dml`, `venv`, or `.venv`, in that order. Set `GLADTALKER_PYTHON` to use a different interpreter. If no folder is found, or the engine fails to start, the app explains why, shows the engine's last messages (the full log is in the app's log folder), and offers **Choose GladTalker folder…** and **Try again**.

**What the app does:**
- **Engine lifecycle:**
  - It starts the engine on a free port with a fresh random token.
  - The token goes in the engine's environment, never on its command line, which other programs can list.
  - The engine always listens on `127.0.0.1`, whatever `GLADTALKER_STUDIO_HOST` says for web mode. The app checks the address the engine reports before using it.
  - In development builds (`npm run tauri:dev`), the engine also accepts the dev server's origin (`build.devUrl`). Release builds never do.
  - It stops the engine and everything it started when you close the app (Windows: the whole process tree; elsewhere: the engine's process group). The engine also watches the app's process (`--exit-with-parent`), so it stops even if the app crashes.
  - Start, restart, and changing the folder happen one at a time, so a restart during a slow first start replaces the engine instead of returning the old one.
- **Saving:** **Save project** opens a native Save dialog. Saving is atomic: the file is written next to the old one and then replaces it, so a failed save never leaves a truncated project. Saving over a file keeps who may read it: on Windows the new contents get the old file's access list (DACL) before anything is written, and `ReplaceFileW` swaps them in; on macOS and Linux they get its permission bits and group. **Open…** uses the system file picker.
- **Finished videos:** **Show the video in its folder** opens the file manager. It only works for files the engine wrote, inside its workspace.
- **While rendering:**
  - Render progress appears on the taskbar icon.
  - A notification fires when a render finishes or fails while you're working in another window.

**Security:** the window's Content-Security-Policy only allows the app's own files plus connections to `127.0.0.1`. The page can call only the app's own commands; the dialog, notification, and opener plugins are used from Rust, and no plugin permissions are granted to the page.

### Building the desktop app

You need Node, plus [Rust](https://rustup.rs/) and the platform prerequisites for Tauri. On Windows that means the Microsoft C++ Build Tools ("Desktop development with C++") and WebView2, which comes with Windows 11.

```bash
cd studio-ui
npm install
npm run tauri:dev     # run it against the Vite dev server (hot reload)
npm run tauri:build   # release build + installer (Windows: src-tauri/target/release/bundle/nsis/*.exe)
```

### Testing the desktop app

- `cd src-tauri && cargo test` runs the Rust unit tests: engine discovery, the ready line, tokens, the workspace check, and atomic saving (on Windows, also that an owner-only file stays owner-only, even while being written).
- `npm run e2e:desktop` (Windows, after `npm run tauri:build`) launches the built app with `GLADTALKER_HOME` set to this repository, attaches Playwright to its WebView2 over the DevTools protocol, and checks that:
  - each run uses its own temporary profile, so it never touches your own autosaved project, settings, or renders: a WebView2 profile (`WEBVIEW2_USER_DATA_FOLDER`), settings and logs (`GLADTALKER_STUDIO_DATA_DIR`), and an engine workspace (`GLADTALKER_STUDIO_WORKSPACE`), plus a free DevTools port. The app runs in the profile folder with a relative workspace, which it must resolve to the same absolute folder for the engine and for **Show in folder**;
  - the engine starts;
  - there are no axe WCAG 2.2 A/AA violations;
  - an upload, face detection, and a real audio preview work;
  - the engine listens on 127.0.0.1 even with `GLADTALKER_STUDIO_HOST=::1` set, and the token isn't on its command line;
  - another profile's draft is untouched by a test run;
  - closing the app stops the engine;
  - killing the app stops the engine too.

  Set `GLADTALKER_DESKTOP_EXE` to test another build.

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
| `src/lib/` | API client, project state and persistence, region geometry, validation, palette, desktop bridge |
| `src-tauri/` | Desktop app (Tauri v2): engine discovery and lifecycle, native dialogs, notifications |
| `e2e-desktop/` | End-to-end tests of the built desktop app |
| `tests/` / `e2e/` | Vitest / Playwright |
