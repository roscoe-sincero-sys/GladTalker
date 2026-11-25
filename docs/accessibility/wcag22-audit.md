# GladTalker Studio: WCAG 2.2 AA and WCAG2ICT audit

Phase 4 of [#11](https://github.com/roscoe-sincero-sys/GladTalker/issues/11). This report covers every WCAG 2.2 Level A and AA success criterion as it applies to GladTalker Studio. For the desktop app, each criterion is read through [WCAG2ICT](https://www.w3.org/TR/wcag2ict-22/) (*Guidance on Applying WCAG 2.2 to Non-Web Information and Communications Technologies*). For each criterion it gives the result and the evidence: the automated test that checks it, or what was reviewed.

## 1. Summary

| | |
| :--- | :--- |
| **Product** | GladTalker Studio 0.2.0: the *Cast → Script → Roll* UI (`studio-ui/`), in two forms: the web version served by `studio_server.py`, and the desktop app (Tauri v2 + WebView2) |
| **Standard** | WCAG 2.2 Level A and AA, applied to non-web software through the WCAG2ICT Group Note |
| **Platforms tested** | Windows 11 (desktop app, WebView2). Chromium through Playwright 1.65.1 (web version; the same engine as WebView2) |
| **Result** | All 55 Level A and AA criteria **pass or are not applicable**, based on automated tests and review. **The checks in section 5 still need a person**, chiefly listening with NVDA and Narrator and using a real Windows High Contrast theme. Until they are done, this is an audit of what can be measured, not a formal conformance claim. |
| **Problems found and fixed** | Three, listed in section 3 |

## 2. How it was tested

**Automated tests.** These run on every change:

| Suite | What it covers |
| :--- | :--- |
| `studio-ui/e2e/wcag-audit.spec.js` (109 tests) | Built specifically for this audit. It drives the real UI against the real engine in **every screen state a user meets**: Cast (empty, and with a cast member), Script (with a pronunciation suggestion), Roll (ready, and while rendering), the Help dialog, first-run setup, and engine offline. Where size matters, each state is checked at the desktop window's size (1280 × 880), at **200 % zoom** (640 × 440 CSS px), and at **400 % zoom** (320 × 256 CSS px). The in-page measurements are in `e2e/wcag-in-page.js`. |
| `studio-ui/e2e-desktop/desktop.spec.js` | Launches the **built desktop app** and reads its **Windows UI Automation** tree. That tree is what Narrator, NVDA and JAWS receive. The test checks named controls, headings, landmarks, the skip link, and the window's document name. |
| `studio-ui/e2e/studio.spec.js` | End-to-end flows: keyboard editing, focus after reordering, Esc in Help, reflow, a real captioned render. |
| `studio-ui/tests/*.test.js` (Vitest) | axe-core scans of components, and WCAG contrast of every colour token and speaker badge in both themes (`palette.test.js`). |

The tools were axe-core 4.14.1 and Playwright 1.65.1 (Chromium). Each check runs in light and dark mode, and in forced colours (Chromium's emulation of Windows High Contrast). Some checks also run with reduced motion, with the WCAG 1.4.12 text-spacing overrides, and in portrait and landscape windows.

**Review.** Some criteria were checked against the source code and every user-facing string: instructions that rely on position or colour, tooltips, time limits, language, and media.

**Not tested here:** listening with a screen reader, real Windows High Contrast themes, Windows text-size and display-scaling settings in the desktop app, speech input, macOS and Linux. Section 5 lists these checks, with steps.

## 3. Problems found and fixed during the audit

| # | Criterion | Problem | Fix | Regression test |
| :--- | :--- | :--- | :--- | :--- |
| 1 | 3.3.7 Redundant Entry | The autosave ran 300 ms after the last change. An edit made just before closing or reloading the window was lost, and had to be entered again. | The pending change is also saved when the page is hidden or closed (`pagehide`, `visibilitychange`). | *nothing has to be entered twice*: it edits the name, reloads at once, and checks the name. It fails on the old code. |
| 2 | 1.4.10 Reflow, 1.4.12 Text Spacing | At 320 CSS px with WCAG text spacing, the **Detection sensitivity** field couldn't shrink below its longest option, so the page scrolled sideways by 5 px. | A field in a row may now shrink to the row's width, and its `select` narrows with it (`app.css`). | *Cast, one cast member with WCAG text spacing*. It fails on the old CSS. |
| 3 | 3.3.4 Error Prevention (Data) | **Open…** replaced the current cast and script without asking, although **New** asks first. | Opening a project over unsaved work now asks first, and answering No keeps the current project. | *opening or starting a project over unsaved work asks first, and No keeps it* |

One more wording change for 1.3.3: the Roll step said "Fix the items **above**". It now says "Fix the items under **“Ready to render?”**", so the instruction names its section instead of relying on position alone.

## 4. Results by success criterion

**Key:**
- **Pass:** meets the criterion; the *Evidence* column names the check.
- **Pass (review):** checked by reviewing the code and text.
- **N/A:** not applicable, with the reason.
- Test names are from `e2e/wcag-audit.spec.js` unless another file is named.

### Perceivable

| Criterion | Level | Result | WCAG2ICT reading and evidence |
| :--- | :---: | :---: | :--- |
| 1.1.1 Non-text Content | A | Pass | Images, icon buttons, speaker badges and stage regions have text alternatives. Decorative symbols (✓, ▸, ⚠) are hidden from assistive technology or come with text. *axe in every state*; *every interactive node … has a role and a name*. |
| 1.2.1 Audio-only and Video-only (Prerecorded) | A | Pass | The voice and full-mix previews play text that is shown on screen as the script, with the mix's timeline. The app has no video-only content. |
| 1.2.2 Captions (Prerecorded) | A | Pass | The finished video plays with a WebVTT captions track, and SRT and VTT files are offered for download (`studio.spec.js`: *render a short video with captions*). |
| 1.2.3 Audio Description or Media Alternative (Prerecorded) | A | Pass | The player has a full transcript beside it, with speaker names and timestamps. |
| 1.2.4 Captions (Live) | AA | N/A | No live media. |
| 1.2.5 Audio Description (Prerecorded) | AA | N/A | The app's own interface has no prerecorded video. The video the user makes is their content, made from their picture and script; the app shows it with captions and a transcript that name each speaker. |
| 1.3.1 Info and Relationships | A | Pass | The page has landmarks (banner, Steps navigation, main, contentinfo) and one `h1` with no skipped heading levels. Every field has a `label`, groups use `fieldset`/`legend`, and lists are real lists. *page language, title, headings, landmarks* in every state. Desktop: *UI Automation* test. |
| 1.3.2 Meaningful Sequence | A | Pass | The DOM order is the reading order, with no CSS reordering. *Tab follows the document order*, at all three sizes. |
| 1.3.3 Sensory Characteristics | A | Pass (review) | No instruction relies only on shape, colour, size, or position; see section 3 for the one wording fix. |
| 1.3.4 Orientation | AA | Pass | Nothing locks the orientation. *portrait and landscape both work*. |
| 1.3.5 Identify Input Purpose | AA | N/A | No field asks for information about the user, only names of characters and settings. |
| 1.4.1 Use of Color | A | Pass | Speakers are told apart by number, shape and name, and states are given in text and symbols. *speakers are told apart by number, shape, and name*. |
| 1.4.2 Audio Control | A | Pass | Nothing plays automatically, and media uses native players with pause and volume. *no media plays by itself*. |
| 1.4.3 Contrast (Minimum) | AA | Pass | axe's colour-contrast rule passes in every state in light and dark mode. Every colour token and badge is checked to 4.5:1 for text (`tests/palette.test.js`). |
| 1.4.4 Resize Text | AA | Pass | At 200 % zoom (640 × 440 CSS px) there is no horizontal scrolling, no clipped text, and every control can still be reached with Tab. *at zoom200 (1.4.4)* in every state. Section 5 lists a check with Windows text size, which isn't automated. |
| 1.4.5 Images of Text | AA | Pass (review) | All text is real text, and icons are Unicode characters with text labels. |
| 1.4.10 Reflow | AA | Pass | At 320 × 256 CSS px (400 % zoom) there is no horizontal scrolling and no clipped text, and focus stays visible. *at zoom400 (1.4.10)* in every state. |
| 1.4.11 Non-text Contrast | AA | Pass | The focus ring has 3:1 against what is around it, at every Tab stop. Text fields and selects have 3:1 boundaries in light and dark mode. Regions on the stage use a light and a dark stroke, so they show over any picture. *focus indicator contrast*; *form control boundaries have 3:1 contrast*. |
| 1.4.12 Text Spacing | AA | Pass | With the WCAG spacing overrides there is no lost content and no horizontal scrolling, at desktop size and at 320 px. *with WCAG text spacing* in every state. |
| 1.4.13 Content on Hover or Focus | AA | N/A | No content appears on hover or focus: no custom tooltips, no `title` attributes, and no popovers. |

### Operable

| Criterion | Level | Result | WCAG2ICT reading and evidence |
| :--- | :---: | :---: | :--- |
| 2.1.1 Keyboard | A | Pass | Tab reaches every control in every state, at three sizes. A complete workflow is done with the keyboard alone: choose a picture, detect the cast, name a speaker, nudge the region with the arrow keys, import a script, reorder lines with Alt+↑, choose framing, start a render and cancel it. *Tab never reaches* (none missing); *the whole workflow, start to finish, with the keyboard alone*. |
| 2.1.2 No Keyboard Trap | A | Pass | Tab goes all the way around every state, and in the modal Help dialog, Esc closes it and returns focus to Help. *no keyboard trap*; `studio.spec.js` *help dialog: Esc closes it*. |
| 2.1.4 Character Key Shortcuts | A | Pass | There are no single-character shortcuts. On the stage, only the arrow keys act, and only while a region has focus. *no single-character key shortcuts act on their own*. |
| 2.2.1 Timing Adjustable | A | N/A | There are no time limits. |
| 2.2.2 Pause, Stop, Hide | A | Pass | Nothing moves or updates unless the user started it. Render progress is a status the user started, and Cancel stops it. Nothing animates under reduced motion. *with reduced motion, nothing animates, even while rendering*. |
| 2.3.1 Three Flashes or Below Threshold | A | Pass (review) | The interface doesn't flash. |
| 2.4.1 Bypass Blocks | A | Pass | WCAG2ICT applies this only to *sets of software*, so it doesn't apply to one program. A **Skip to the current step** link is provided anyway, and UI Automation exposes it. |
| 2.4.2 Page Titled | A | Pass | The window is titled **GladTalker Studio** (WCAG2ICT: the software's title). *page language, title*; desktop *UI Automation*: the document is named GladTalker Studio. |
| 2.4.3 Focus Order | A | Pass | There is no positive `tabindex`. Tab follows the document order, and Shift+Tab visits the same stops in reverse. After reordering, focus stays on the moved line (`studio.spec.js`). |
| 2.4.4 Link Purpose (In Context) | A | Pass | The only links are the skip link and the downloads (*Download video (MP4)*, the caption files), and each says what it does. |
| 2.4.5 Multiple Ways | AA | N/A | WCAG2ICT applies this only to *sets of software*. The three steps can also be reached from the step navigation and from Back and Next. |
| 2.4.6 Headings and Labels | AA | Pass (review) | Headings name each step and section (*Step 2 of 3: Script*, *Ready to render?*), and labels name their fields. |
| 2.4.7 Focus Visible | AA | Pass | Every Tab stop shows a 3 px outline, in light, dark and forced colours. *focus visible*, *focus visible in forced colours*. |
| 2.4.11 Focus Not Obscured (Minimum) | AA | Pass | No Tab stop is hidden behind the sticky header or footer, at 1280 × 880, at 640 × 440 and at 320 × 256. Smaller than a third of the viewport, each one is fully in view. *focus not obscured*, *focused control fully in view*. |
| 2.5.1 Pointer Gestures | A | Pass (review) | No multipoint or path gestures. |
| 2.5.2 Pointer Cancellation | A | Pass (review) | Controls are native buttons, which act on release. Dragging a region can be undone by dragging back before releasing, and the steppers can always correct it. |
| 2.5.3 Label in Name | A | Pass | Each visible label is part of its accessible name. axe's `label-content-name-mismatch` rule is turned on in every state. |
| 2.5.4 Motion Actuation | A | N/A | No motion input. |
| 2.5.7 Dragging Movements | AA | Pass | Moving or resizing a region also works with X, Y, W and H steppers, **Snap to face**, **Expand to tile**, and the arrow keys. Script lines move with ↑ and ↓ buttons. *a region can be moved and resized with single clicks, never a drag*. |
| 2.5.8 Target Size (Minimum) | AA | Pass | Every target is at least 24 × 24 CSS px, or has 24 px spacing, in every state at 1280 and 320 px; the UI's own controls are 36 px. The stage's corner resize handles are smaller, but fall under the *Equivalent* exception: the same resize is available through the W/H steppers and Snap/Expand, which meet the size. *target size (2.5.8)*. |

### Understandable

| Criterion | Level | Result | WCAG2ICT reading and evidence |
| :--- | :---: | :---: | :--- |
| 3.1.1 Language of Page | A | Pass | `lang="en"` (WCAG2ICT: the software's default language). *page language*. |
| 3.1.2 Language of Parts | AA | N/A | All text is in English. The user's own script can be in any language. |
| 3.2.1 On Focus | A | Pass | Tabbing through every control never navigates, opens a dialog, or starts work. *focusing or changing a control never …*. |
| 3.2.2 On Input | A | Pass | Changing any select with the keyboard has no side effects: no step change, no dialog, no request beyond the live pronunciation check. Same test. |
| 3.2.3 Consistent Navigation | AA | N/A | WCAG2ICT applies this only to *sets of software*. The header and footer are the same on every step anyway (see 3.2.6). |
| 3.2.4 Consistent Identification | AA | N/A | WCAG2ICT applies this only to *sets of software*. Within the app, each function keeps one name throughout. |
| 3.2.6 Consistent Help | A | Pass | **Help** is in the same place on every step, in first-run setup, and on the offline screen. *the Help button is in the same place on every step and screen*. |
| 3.3.1 Error Identification | A | Pass | A missing or duplicate name sets `aria-invalid` and gives a text message tied to the field with `aria-describedby`. Roll lists every missing item in words. *errors are identified in text, tied to their field*. Number fields can't hold an invalid value: they keep it within the range their hint states. |
| 3.3.2 Labels or Instructions | A | Pass | Every field has a visible label, and formats and limits are given in hints (for example *PNG, JPEG, BMP, or WebP, up to 50 MB*). axe `label` and `form-field-multiple-labels` rules. |
| 3.3.3 Error Suggestion | AA | Pass | Messages say how to fix the problem: *Enter a name.*, *Another cast member already has this name.*, and the Roll checklist, whose buttons go to the field to fix. |
| 3.3.4 Error Prevention (Legal, Financial, Data) | AA | Pass | **New** and **Open…** ask before replacing the project. In the desktop app, the system Save dialog asks before overwriting a file, and a failed save keeps the previous file; the web version downloads a new file. *opening or starting a project over unsaved work asks first*. |
| 3.3.7 Redundant Entry | A | Pass | The project autosaves and is restored after a reload or restart, including an edit made just before closing. *nothing has to be entered twice*. |
| 3.3.8 Accessible Authentication (Minimum) | AA | Pass (review) | The desktop app needs no sign-in. Web mode with a token uses a link (`#token=`), with nothing to remember or transcribe. |

### Robust

| Criterion | Level | Result | WCAG2ICT reading and evidence |
| :--- | :---: | :---: | :--- |
| 4.1.1 Parsing | A | N/A | Obsolete; removed from WCAG 2.2. |
| 4.1.2 Name, Role, Value | A | Pass | Controls are native HTML. Every interactive node in Chromium's accessibility tree has a role and a name, and no focusable element is a generic node. States are exposed (`aria-pressed` on regions, `aria-invalid`, `aria-current` on the step). In the desktop app, every keyboard-focusable control in the **Windows UI Automation** tree has a name. *every interactive node in the accessibility tree has a role and a name*; desktop *UI Automation* test. |
| 4.1.3 Status Messages | AA | Pass | Results go to a polite live region, and errors to an assertive one, without moving focus. This covers detection, import, previews, render start, progress, render end, and errors. *results are announced through live regions without moving focus*. |

## 5. Still to be checked by a person

Automated checks can show *what* assistive technology receives, but not how it sounds or feels. Each row below is a short manual check, with what to expect. They should be done before claiming formal conformance.

| # | Check | How | Expected |
| :--- | :--- | :--- | :--- |
| M1 | **NVDA** (Windows, desktop app and Edge) | Install the app. Start NVDA. Do a whole video with the keyboard: choose a picture, Auto-Detect Cast, name a speaker, import a script, preview the mix, and render. | Every control is announced with its name, role and state. Step headings are read on arrival. Detection, import, preview and render progress are announced without moving focus. Errors are read as alerts. |
| M2 | **Narrator** (Windows) | The same walkthrough with Narrator, in scan mode and with Tab. | Same as M1. |
| M3 | **Windows High Contrast** (Settings → Accessibility → Contrast themes: *Aquatic* and *Desert*) | Use all three steps in the desktop app. | All text, borders, region outlines, badges and the focus ring use the theme's colours and stay visible. Nothing disappears. |
| M4 | **Windows text size 200 %** (Settings → Accessibility → Text size) | Use all three steps in the desktop app, with the window at its default size and maximized. | No clipped or overlapping text, and every control can still be reached. |
| M5 | **Display scaling 200 %** and **Magnifier** | Use the desktop app at 200 % scaling, and with Magnifier following focus. | The layout reflows (as at 640 × 440 CSS px), and Magnifier follows focus. |
| M6 | **Voice Access** (Windows 11) | Say "click Next: Script", "click Auto-Detect Cast", and similar commands. | Each control is activated by its visible label (2.5.3). |
| M7 | **macOS VoiceOver** and **Linux Orca** | Repeat M1 on a macOS or Linux build. | Same as M1. *The desktop app hasn't been built or run on macOS or Linux yet.* |

## 6. Running the checks

```bash
cd studio-ui
npm test                                                # Vitest: components, axe, contrast tokens
npx playwright test e2e/wcag-audit.spec.js --workers=4  # this audit, against a real engine
npm run e2e                                             # all web end-to-end tests (includes the audit)
npm run tauri:build && npm run e2e:desktop              # the desktop app, including UI Automation (Windows)
```
