import { defineConfig } from '@playwright/test';

// End-to-end tests of the built desktop app (Windows). Build it first with `npm run tauri:build`;
// the tests start the executable themselves (with GLADTALKER_HOME pointing at this repository),
// so no web server is configured here. Set GLADTALKER_DESKTOP_EXE to test another build.
export default defineConfig({
  testDir: 'e2e-desktop',
  timeout: 300_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
});
