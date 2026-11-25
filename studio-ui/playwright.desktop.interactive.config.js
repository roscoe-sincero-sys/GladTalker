// `npm run e2e:desktop:interactive`: the desktop tests, with the checks that type real keystrokes
// into the app (Ctrl+Plus zoom) required to run instead of being skipped when Windows won't
// bring the app's window to the front. For a desktop session; don't use the keyboard meanwhile.
process.env.GLADTALKER_DESKTOP_INTERACTIVE = '1';
export { default } from './playwright.desktop.config.js';
