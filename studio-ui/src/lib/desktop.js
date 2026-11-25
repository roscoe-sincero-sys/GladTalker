// The bridge to the desktop app (Tauri). In a browser none of this is used: `isDesktop()` is
// false and the UI talks to the engine that served it. In the desktop app, the shell starts the
// engine on a private port with a fresh token and hands both to the UI (see src-tauri/src/lib.rs).

/** Overridable in tests: `host.invoke = async (cmd, args) => ...`. */
export const host = { invoke: null, window: globalThis.window };

export function isDesktop() {
  return Boolean(host.invoke || host.window?.__TAURI_INTERNALS__);
}

/** Calls a command of the desktop shell. */
export async function desktop(command, args = {}) {
  if (host.invoke) return host.invoke(command, args);
  const { invoke } = await import('@tauri-apps/api/core'); // only loaded inside the desktop app
  return invoke(command, args);
}

/** The shell's errors arrive as plain strings; make them Errors with that message. */
export async function desktopCall(command, args) {
  try {
    return await desktop(command, args);
  } catch (err) {
    throw err instanceof Error ? err : new Error(String(err));
  }
}
