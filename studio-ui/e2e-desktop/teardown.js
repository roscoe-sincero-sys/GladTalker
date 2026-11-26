// What a desktop end-to-end test leaves behind, cleaned up in the right order whatever way the
// test ends: passed, failed (an assertion, or a launch that never got a DevTools connection), or
// skipped (test.skip() ends a test by throwing, past any cleanup written after its try/finally).
//
// Every app is registered the moment it is spawned, with the profile it uses, and every profile
// the moment it is created. Teardown first stops each app that is still running and confirms it
// (and the engine it started) has exited. A profile is deleted only once every app that used it
// is confirmed gone: on Windows a running app keeps its WebView2 profile and logs open, so
// deleting under it would fail partway and leave half a profile. If an app can't be confirmed
// stopped, its profile is kept and named in the log, and the other profiles are still cleaned up.

import fs from 'node:fs';
import path from 'node:path';

export function createTeardown({ stopApp, log = console.warn }) {
  const apps = [];
  const profiles = [];
  return {
    /** Registers a spawned app (any object `stopApp` accepts) that uses the profile folder
     * `root`; returns the app. */
    app(launched, root) {
      apps.push({ launched, root: root && path.resolve(root) });
      return launched;
    },
    /** Registers a profile folder to delete; returns it. */
    profile(root) {
      profiles.push(path.resolve(root));
      return root;
    },
    /** Stops the registered apps, then deletes each registered profile whose apps are all
     * confirmed stopped. Never throws. */
    async run() {
      const stillInUse = new Map(); // profile root -> why it can't be deleted yet
      for (const { launched, root } of apps.splice(0).reverse()) {
        try {
          await stopApp(launched); // resolves only once the app and its engine have exited
        } catch (err) {
          log(`Couldn’t confirm a test app stopped: ${err.message}`);
          if (root) stillInUse.set(root, err.message);
        }
      }
      for (const root of profiles.splice(0)) {
        if (stillInUse.has(root)) {
          log(`Kept the test profile ${root}: an app using it may still be running (${stillInUse.get(root)}). Delete it once that app has exited.`);
          continue;
        }
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
