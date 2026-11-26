<script>
  // First-run checks: FFmpeg and the pretrained models, with a one-click download and live
  // progress. Shown whenever something is missing, before any terminal command would be needed.
  import { onDestroy } from 'svelte';
  import { api } from '../lib/api.js';
  import { engine, pollBusyModels, refreshModels } from '../lib/app-state.svelte.js';
  import { announce, announceError } from '../lib/announce.svelte.js';
  import JobProgress from './JobProgress.svelte';

  let job = $state(null);
  let error = $state('');
  let watch = null;
  onDestroy(() => watch?.close());

  const ffmpeg = $derived(engine.hardware?.ffmpeg);
  const models = $derived(engine.models);
  // A busy file was being replaced or locked while the engine checked it: nothing to repair,
  // so it isn't offered for download; check again shortly instead.
  const missing = $derived(models ? models.files.filter((f) => !f.ready && !f.busy) : []);
  const busy = $derived(models ? models.files.filter((f) => f.busy) : []);
  const poller = pollBusyModels();
  onDestroy(() => poller.stop());
  $effect(() => {
    if (busy.length) poller.poke();
  });

  const downloading = $derived(Boolean(job && (job.state === 'queued' || job.state === 'running')));

  async function download() {
    error = '';
    try {
      job = await api.downloadModels(false);
      announce('Downloading models. This can take a while; progress is shown below.');
      watch?.close();
      watch = api.watchJob(job.id, (j) => (job = j));
      const final = await watch.done;
      if (final === null) return; // closed: the panel went away
      await refreshModels();
      if (final?.state === 'succeeded') {
        // Announce what the refreshed status shows: busy files may have been left for later.
        if (engine.models?.complete) announce('All models are downloaded and verified.');
        else announce(final.result?.message || 'Some models still need attention. See Setup.');
      }
      else if (final?.state === 'failed') announceError(final.error || 'The download failed.');
    } catch (err) {
      error = err.message;
      announceError(err.message);
    }
  }
</script>

<section class="setup card" aria-labelledby="setup-heading">
  <h2 id="setup-heading" tabindex="-1">Setup</h2>
  <p>GladTalker needs FFmpeg and its pretrained models before it can make videos.</p>
  <ul class="checks">
    <li>
      {#if ffmpeg?.available}
        <span class="ok-text">FFmpeg {ffmpeg.version ?? ''} found</span>
      {:else}
        <span class="error-text">FFmpeg not found.</span>
        Install it (Windows: <code>winget install Gyan.FFmpeg</code>; macOS: <code>brew install ffmpeg</code>; Linux: your package manager) and restart GladTalker.
      {/if}
    </li>
    <li>
      {#if models?.complete}
        <span class="ok-text">All {models.total} models downloaded and verified</span>
      {:else if models}
        {#if missing.length}
          <span class="error-text">{missing.length} of {models.total} models need downloading</span>
          <ul>
            {#each missing as f (f.path)}
              <li>{f.name}{f.damaged ? ' (damaged)' : f.incomplete ? ' (incomplete)' : ''}</li>
            {/each}
          </ul>
        {/if}
        {#if busy.length}
          <span>Checking again shortly: {busy.length === 1 ? 'a model is' : `${busy.length} models are`} being updated or in use by another program</span>
          <ul>
            {#each busy as f (f.path)}<li>{f.name}</li>{/each}
          </ul>
        {/if}
      {/if}
    </li>
  </ul>
  {#if missing.length}
    <button type="button" class="primary" onclick={() => { if (!downloading) download(); }}
      aria-disabled={downloading ? 'true' : undefined}>Download models (about 2.5 GB)</button>
  {/if}
  {#if error}<p class="error-text">{error}</p>{/if}
  <JobProgress {job} id="models-job" label="Model download" oncancel={job ? () => api.cancelJob(job.id) : null} />
</section>

<style>
  .setup { margin-bottom: 1.25rem; border-left: 6px solid var(--accent); }
  .checks { padding-left: 1.25rem; }
  .checks > li { margin-bottom: 0.5rem; }
</style>
