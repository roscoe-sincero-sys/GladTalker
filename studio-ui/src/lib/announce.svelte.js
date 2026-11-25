// Screen-reader announcements through two persistent live regions rendered by App.svelte
// (WCAG 4.1.3): `polite` for progress and confirmations, `assertive` for errors. Focus never moves.

export const live = $state({ polite: '', assertive: '' });

let timers = { polite: null, assertive: null };

function set(kind, message) {
  // Clear first so repeating the same message is announced again.
  live[kind] = '';
  clearTimeout(timers[kind]);
  timers[kind] = setTimeout(() => {
    live[kind] = message;
  }, 50);
}

export const announce = (message) => set('polite', message);
export const announceError = (message) => set('assertive', message);
