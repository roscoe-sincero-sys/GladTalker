// What a desktop end-to-end test leaves behind, cleaned up in the right order whatever way the
// test ends: passed, failed (an assertion, or a launch that never got a DevTools connection), or
// skipped (test.skip() ends a test by throwing, past any cleanup written after its try/finally).
//
// Every app is registered the moment it is spawned and every profile the moment it is created.
// Teardown first stops each app that is still running and waits for it (and what it started) to
// exit, and only then deletes the profiles: on Windows a running app keeps its WebView2 profile
// and logs open, and a folder with open files can't be deleted.

import fs from 'node:fs';

export function createTeardown({ stopApp, log = console.warn }) {
  const apps = [];
  const profiles = [];
  return {
    /** Registers a spawned app (any object `stopApp` accepts); returns it. */
    app(launched) {
      apps.push(launched);
      return launched;
    },
    /** Registers a profile folder to delete; returns it. */
    profile(root) {
      profiles.push(root);
      return root;
    },
    /** Stops the registered apps, then deletes the registered profiles. Never throws. */
    async run() {
      for (const launched of apps.splice(0).reverse()) {
        try {
          await stopApp(launched);
        } catch (err) {
          log(`Couldn’t stop a test app: ${err.message}`);
        }
      }
      for (const root of profiles.splice(0)) {
        try {
          // WebView2 can take a moment to let go of its files after the app has exited.
          fs.rmSync(root, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 });
        } catch (err) {
          log(`Couldn’t delete the test profile ${root}: ${err.message}`);
        }
      }
    },
  };
}
