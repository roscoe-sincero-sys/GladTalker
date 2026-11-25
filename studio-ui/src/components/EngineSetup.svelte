<script>
  // Desktop app only: shown when the shell can't find or start the GladTalker engine. Explains
  // what's wrong in plain words and offers the fix (choose the folder, or try again), with the
  // engine's last messages for troubleshooting.
  import { onMount } from 'svelte';
  import { engine, connectEngine, chooseEngineFolder } from '../lib/app-state.svelte.js';
  import { announceError } from '../lib/announce.svelte.js';

  const info = $derived(engine.desktop ?? {});
  const notFound = $derived(info.state === 'not_found');

  onMount(() => {
    document.getElementById('engine-setup-heading')?.focus();
    announceError(info.message || 'The GladTalker engine isn’t running.');
  });
</script>

<section class="card setup" aria-labelledby="engine-setup-heading">
  <h2 id="engine-setup-heading" tabindex="-1">{notFound ? 'Find GladTalker' : 'The GladTalker engine didn’t start'}</h2>
  <p>{info.message}</p>

  {#if notFound}
    <p class="muted">
      That folder also needs its Python environment (<code>venv-dml</code>, <code>venv</code>, or <code>.venv</code>).
      If you haven’t installed GladTalker yet, follow the steps in its README first.
    </p>
  {:else}
    {#if info.folder}<p class="muted">Folder: <code>{info.folder}</code><br />Python: <code>{info.python}</code></p>{/if}
    {#if info.log}
      <details>
        <summary>The engine’s last messages</summary>
        <!-- A labelled, focusable region, so keyboard users can scroll a long log (axe's
             scrollable-region-focusable; WCAG 2.1.1). Svelte flags any tabindex on a non-widget. -->
        <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
        <pre class="log" role="region" tabindex="0" aria-label="The engine’s last messages">{info.log}</pre>
      </details>
    {/if}
    {#if info.log_path}<p class="muted">The full log is in <code>{info.log_path}</code></p>{/if}
  {/if}

  <div class="row">
    <button type="button" class="primary" onclick={chooseEngineFolder}>Choose GladTalker folder…</button>
    {#if !notFound || info.folder}
      <button type="button" onclick={() => connectEngine({ restart: true })}>Try again</button>
    {/if}
  </div>
</section>

<style>
  .setup { max-width: 52rem; }
  .log { max-height: 18rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 0.85rem;
    background: var(--surface-2, var(--surface)); border: 1px solid var(--border); padding: 0.75rem; border-radius: var(--radius); }
  code { overflow-wrap: anywhere; }
</style>
