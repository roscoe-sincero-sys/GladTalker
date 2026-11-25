<script>
  // One dialogue line. Reordering never needs dragging (WCAG 2.5.7): Up/Down buttons, or
  // Alt+Up / Alt+Down while focus is anywhere in the row (shortcut needs a modifier, 2.1.4).
  import SpeakerBadge from './SpeakerBadge.svelte';

  let { line, index, total, cast, warnings = [], playing = false, onmove, ondelete, onplay, onfix } = $props();

  const n = $derived(index + 1);
  const speaker = $derived(cast.find((m) => m.id === line.castId));
  const speakerIndex = $derived(cast.findIndex((m) => m.id === line.castId));
  const speakerError = $derived(!speaker ? 'Choose who says this line.' : '');
  const textError = $derived(!line.text.trim() ? 'Type what should be said, or delete the line.' : '');

  function onkeydown(e) {
    if (!e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
    e.preventDefault();
    onmove(index, e.key === 'ArrowUp' ? -1 : 1);
  }
</script>

<!-- The listener only catches Alt+Up/Down bubbling from the row's own controls (select, input,
     buttons); the <li> itself is not focusable or interactive. -->
<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<li class="line" {onkeydown}>
  <div class="head">
    <span class="num" id="line-label-{line.id}">Line {n}</span>
    {#if speaker}<SpeakerBadge index={speaker.style} number={speakerIndex + 1} size={22} />{/if}
  </div>
  <div class="inputs">
    <div class="field speaker">
      <label for="speaker-{line.id}" class="visually-hidden">Speaker for line {n}</label>
      <select id="speaker-{line.id}" bind:value={line.castId} aria-invalid={speakerError ? 'true' : undefined}
        aria-describedby={speakerError ? `speaker-error-${line.id}` : undefined}>
        <option value={null} disabled>Choose speaker…</option>
        {#each cast as m, i (m.id)}<option value={m.id}>{i + 1}. {m.name || 'Unnamed'}</option>{/each}
      </select>
    </div>
    <div class="field text">
      <label for="text-{line.id}" class="visually-hidden">What is said in line {n}</label>
      <input id="text-{line.id}" type="text" bind:value={line.text} autocomplete="off" spellcheck="true" maxlength="1000"
        aria-invalid={textError ? 'true' : undefined}
        aria-describedby={[textError && `text-error-${line.id}`, warnings.length && `warn-${line.id}`].filter(Boolean).join(' ') || undefined} />
    </div>
    <div class="buttons" role="group" aria-label="Line {n} actions">
      <button type="button" onclick={() => onplay(line)} disabled={!speaker || !line.text.trim() || playing}
        aria-label="Play line {n}">▶ <span class="word">{playing ? '…' : 'Play'}</span></button>
      <button type="button" id="up-{line.id}" onclick={() => onmove(index, -1)} disabled={index === 0} aria-label="Move line {n} up"
        aria-keyshortcuts="Alt+ArrowUp">↑ <span class="word">Up</span></button>
      <button type="button" id="down-{line.id}" onclick={() => onmove(index, 1)} disabled={index === total - 1} aria-label="Move line {n} down"
        aria-keyshortcuts="Alt+ArrowDown">↓ <span class="word">Down</span></button>
      <button type="button" class="danger" onclick={() => ondelete(index)} aria-label="Delete line {n}">✕ <span class="word">Delete</span></button>
    </div>
  </div>
  {#if speakerError}<p class="error-text" id="speaker-error-{line.id}">{speakerError}</p>{/if}
  {#if textError}<p class="error-text" id="text-error-{line.id}">{textError}</p>{/if}
  {#if warnings.length}
    <ul class="warnings" id="warn-{line.id}">
      {#each warnings as w (w.code)}
        <li>
          <span><span aria-hidden="true">💡</span> {w.message}</span>
          {#if w.fix && w.fix !== line.text}
            <button type="button" onclick={() => onfix(line, w.fix)}>Auto-Fix <span class="visually-hidden">line {n}</span></button>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</li>

<style>
  .line { padding: 0.75rem; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
  .head { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.35rem; }
  .num { font-weight: 700; }
  .inputs { display: grid; grid-template-columns: minmax(10rem, 14rem) minmax(12rem, 1fr) auto; gap: 0.5rem; align-items: start; }
  .inputs .field { margin: 0; }
  .text input { width: 100%; }
  .buttons { display: flex; flex-wrap: wrap; gap: 0.35rem; }
  .warnings { list-style: none; padding: 0; margin: 0.5rem 0 0; }
  .warnings li { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; color: var(--text); }
  @media (max-width: 60rem) {
    .inputs { grid-template-columns: minmax(0, 1fr); }
  }
</style>
