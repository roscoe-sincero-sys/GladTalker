// One short audio clip at a time (voice and line previews), so clips never talk over each other,
// and every clip's blob: URL is released however playback ends: finished, failed, or replaced.

let current = null;

/** Stop whatever is playing (its onend still runs). */
export function stopPlayback() {
  const playing = current;
  current = null;
  if (playing) {
    playing.audio.pause();
    playing.finish();
  }
}

/** Play an audio Blob, stopping any clip already playing. Resolves once playback has started
 * (rejects if it can't start); `onend` runs exactly once when this clip stops for any reason. */
export async function playBlob(blob, onend = () => {}, AudioImpl = globalThis.Audio) {
  stopPlayback();
  const url = URL.createObjectURL(blob);
  const audio = new AudioImpl(url);
  let finished = false;
  const entry = {
    audio,
    finish() {
      if (finished) return;
      finished = true;
      if (current === entry) current = null;
      URL.revokeObjectURL(url);
      onend();
    },
  };
  current = entry;
  audio.onended = entry.finish;
  audio.onerror = entry.finish;
  try {
    await audio.play();
  } catch (err) {
    entry.finish();
    throw err;
  }
}
