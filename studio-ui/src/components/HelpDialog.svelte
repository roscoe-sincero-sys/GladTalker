<script>
  // Help: always opened from the same top-right button on every step (WCAG 3.2.6). A native
  // modal <dialog>: focus moves in, Esc closes, and focus returns to the Help button.
  let { open = $bindable(false), version = '' } = $props();
  let dialog;

  $effect(() => {
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  });
</script>

<dialog bind:this={dialog} aria-labelledby="help-title" onclose={() => (open = false)}>
  <div class="head">
    <h2 id="help-title">Help</h2>
    <button type="button" onclick={() => (open = false)}>Close <span class="visually-hidden">help</span></button>
  </div>

  <h3>How it works</h3>
  <ol>
    <li><strong>Cast</strong>: choose a picture, let Auto-Detect find the faces (or add regions), and give each character a name and voice.</li>
    <li><strong>Script</strong>: paste a script like <code>Brain: Click it.</code> or write lines one by one. Listen to lines and the full mix.</li>
    <li><strong>Roll</strong>: pick the video size and quality, then render. Captions are created from the script.</li>
  </ol>
  <p>Your project saves automatically in this browser. Use <em>Save project</em> to keep a copy as a file.</p>

  <h3>Keyboard</h3>
  <table>
    <caption class="visually-hidden">Keyboard shortcuts</caption>
    <thead><tr><th scope="col">Where</th><th scope="col">Keys</th><th scope="col">Action</th></tr></thead>
    <tbody>
      <tr><td>Anywhere</td><td><kbd>Tab</kbd> / <kbd>Shift</kbd>+<kbd>Tab</kbd></td><td>Move between controls</td></tr>
      <tr><td>Region on the stage</td><td><kbd>Arrow</kbd> keys</td><td>Move the region 1 px (<kbd>Shift</kbd>: 10 px)</td></tr>
      <tr><td>Region on the stage</td><td><kbd>Alt</kbd>+<kbd>Arrow</kbd> keys</td><td>Resize the region 1 px (<kbd>Shift</kbd>: 10 px)</td></tr>
      <tr><td>Dialogue line</td><td><kbd>Alt</kbd>+<kbd>↑</kbd> / <kbd>Alt</kbd>+<kbd>↓</kbd></td><td>Move the line up or down</td></tr>
      <tr><td>Dialog</td><td><kbd>Esc</kbd></td><td>Close</td></tr>
      <tr><td>Anywhere</td><td><kbd>Ctrl</kbd>+<kbd>+</kbd> / <kbd>Ctrl</kbd>+<kbd>−</kbd> / <kbd>Ctrl</kbd>+<kbd>0</kbd></td><td>Make everything bigger, smaller, or the normal size</td></tr>
    </tbody>
  </table>
  <p>Shortcuts only work while that control has focus, and every one has a button or field that does the same thing, so nothing needs a mouse, dragging, or a shortcut.</p>

  <h3>Troubleshooting</h3>
  <ul>
    <li><strong>No face found in a region</strong>: use <em>Expand to Tile</em> or make the region larger so the whole head is inside; try a higher Auto-Detect sensitivity for drawn characters.</li>
    <li><strong>Words sound wrong</strong>: the voice can split “you’re” into two words; use the line’s <em>Auto-Fix</em>.</li>
    <li><strong>Rendering is slow</strong>: each character is animated only while they speak; <em>Standard</em> quality is several times faster than <em>Enhanced</em>.</li>
    <li><strong>Can’t reach the engine</strong>: start it with <code>python studio_server.py</code> and reload this page.</li>
  </ul>

  <h3>About</h3>
  <p>GladTalker Studio{version ? ` ${version}` : ''}, built on SadTalker (Apache License 2.0). Voices by Coqui TTS; face restoration by GFPGAN.</p>
</dialog>

<style>
  dialog {
    width: min(46rem, calc(100vw - 2rem));
    max-height: calc(100vh - 2rem);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--bg);
    color: var(--text);
    padding: 1.25rem 1.5rem;
  }
  dialog::backdrop { background: rgb(0 0 0 / 0.55); }
  .head { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 0.75rem; }
  th, td { text-align: left; padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--border); vertical-align: top; }
  kbd { border: 1px solid var(--border); border-radius: 4px; padding: 0 0.3rem; font-family: inherit; font-size: 0.9em; background: var(--surface); }
</style>
