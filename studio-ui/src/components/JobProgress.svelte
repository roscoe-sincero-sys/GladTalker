<script>
  // Live job progress: native <progress> with a text label, the stage list, ETA, and Cancel.
  // Status text goes to the polite live region in App (rate-limited by the engine), so focus
  // never moves (WCAG 4.1.3).
  import { duration, percent } from '../lib/format.js';

  let { job, label = 'Progress', stageLabels = {}, oncancel = null, id = 'job' } = $props();

  const running = $derived(job && (job.state === 'queued' || job.state === 'running'));
  const stageIndex = $derived(job?.stages?.indexOf(job.stage) ?? -1);

  function stageStatus(i) {
    if (!job) return '';
    if (job.state === 'succeeded') return 'done';
    if (i < stageIndex) return 'done';
    if (i === stageIndex) return job.state === 'running' ? 'in progress' : job.state;
    return 'waiting';
  }
  const stageName = (s) => stageLabels[s] ?? (s.startsWith('render:') ? `Animate ${s.slice(7)}` : s);
</script>

{#if job}
  <section class="job card" aria-labelledby="{id}-title">
    <h3 id="{id}-title">{label}</h3>
    <div class="bar">
      <label for="{id}-bar" class="visually-hidden">{label}, percent complete</label>
      <progress id="{id}-bar" max="1" value={job.progress}>{percent(job.progress)}</progress>
      <span class="pct" aria-hidden="true">{percent(job.progress)}</span>
    </div>
    <p class="message">{job.message}</p>
    {#if running && job.eta_s}
      <p class="muted">About {duration(job.eta_s)} left{#if job.elapsed_s} · {duration(job.elapsed_s)} so far{/if}</p>
    {/if}
    {#if job.stages?.length > 1}
      <ol class="stages">
        {#each job.stages as stage, i (stage)}
          <li class={stageStatus(i).replace(' ', '-')}>
            <span aria-hidden="true">{stageStatus(i) === 'done' ? '✓' : stageStatus(i) === 'in progress' ? '▸' : '○'}</span>
            {stageName(stage)} <span class="visually-hidden">({stageStatus(i)})</span>
          </li>
        {/each}
      </ol>
    {/if}
    {#if job.state === 'failed'}
      <p class="error-text" role="alert">{job.error || job.message}</p>
    {:else if job.state === 'cancelled'}
      <p class="muted">Cancelled.</p>
    {/if}
    {#if running && oncancel}
      <button type="button" class="danger" onclick={oncancel} disabled={job.cancel_requested}>
        {job.cancel_requested ? 'Cancelling…' : 'Cancel'}
      </button>
    {/if}
  </section>
{/if}

<style>
  .bar { display: flex; align-items: center; gap: 0.75rem; }
  .pct { font-variant-numeric: tabular-nums; min-width: 3.5ch; text-align: right; font-weight: 700; }
  .message { margin-top: 0.5rem; }
  .stages { list-style: none; padding: 0; margin: 0.5rem 0 1rem; display: grid; gap: 0.25rem; }
  .stages li { color: var(--muted); }
  .stages li.done { color: var(--success); }
  .stages li.in-progress { color: var(--text); font-weight: 700; }
</style>
