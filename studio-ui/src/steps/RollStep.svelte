<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { project, renderRequest } from '../lib/project.svelte.js';
  import { engine, goTo, STEPS } from '../lib/app-state.svelte.js';
  import { announce, announceError } from '../lib/announce.svelte.js';
  import { renderProblems } from '../lib/validate.js';
  import { frameLayout } from '../lib/regions.js';
  import { duration, timestamp } from '../lib/format.js';
  import JobProgress from '../components/JobProgress.svelte';

  const PRESETS = [
    { id: 'hd720', label: '16:9 HD', size: [1280, 720], note: 'Video calls, presentations' },
    { id: 'fhd1080', label: '16:9 Full HD', size: [1920, 1080], note: 'Sharper; larger file' },
    { id: 'vertical1080', label: '9:16 Vertical', size: [1080, 1920], note: 'Phones, stories' },
    { id: 'square1080', label: '1:1 Square', size: [1080, 1080], note: 'Social feeds' },
    { id: 'source', label: 'Match source', size: null, note: 'Same size as the scene image' },
  ];

  let job = $state(null);
  let videoUrl = $state('');
  let vttUrl = $state('');
  let srtUrl = $state('');
  let rate = $state('1');
  let video = $state();
  let starting = $state(false);
  let startError = $state('');

  const problems = $derived(renderProblems(project, { models: engine.models, ffmpeg: engine.hardware?.ffmpeg?.available ?? null }));
  const running = $derived(job && (job.state === 'queued' || job.state === 'running'));
  const preset = $derived(PRESETS.find((p) => p.id === project.output.preset) ?? PRESETS[0]);
  const layout = $derived(project.image
    ? frameLayout(project.image.width, project.image.height, preset.size?.[0], preset.size?.[1], project.output.fit) : null);
  const result = $derived(job?.state === 'succeeded' ? job.result : null);
  const stepLabel = (id) => STEPS.find((s) => s.id === id)?.label ?? 'Setup';

  const framingText = $derived.by(() => {
    if (!layout) return 'Choose a scene image to preview the framing.';
    if (!preset.size) return `The video will be ${layout.width}×${layout.height}, the same as the scene.`;
    const bars = layout.offsetX > 0 ? 'bars at the left and right' : layout.offsetY > 0 ? 'bars at the top and bottom' : 'no bars';
    return project.output.fit === 'fit'
      ? `The whole scene is shown at ${layout.scaledW}×${layout.scaledH} inside the ${layout.width}×${layout.height} frame, with ${bars}.`
      : `The scene is enlarged to ${layout.scaledW}×${layout.scaledH} and cropped to fill the ${layout.width}×${layout.height} frame; edges are cut off.`;
  });

  async function showResult(final) {
    videoUrl = await api.fileUrl(final.id, 'video');
    vttUrl = final.result.vtt_path ? await api.fileUrl(final.id, 'vtt') : '';
    srtUrl = final.result.srt_path ? await api.fileUrl(final.id, 'srt') : '';
    announce(`Your video is ready: ${duration(final.result.duration_s)}, ${final.result.width}×${final.result.height}. The player and download links are below.`);
  }

  async function follow(id) {
    const final = await api.watchJob(id, (j) => (job = j)).done;
    if (final?.state === 'succeeded') await showResult(final);
    else if (final?.state === 'failed') announceError(`Render failed: ${final.error || final.message}`);
    else if (final?.state === 'cancelled') announce('Render cancelled.');
  }

  async function render() {
    startError = '';
    videoUrl = '';
    vttUrl = '';
    starting = true;
    try {
      job = await api.startRender(renderRequest(project));
      project.lastRenderJobId = job.id;
      announce('Render started. Progress updates will be announced; you can keep working or cancel.');
      starting = false;
      await follow(job.id);
    } catch (err) {
      startError = err.message;
      announceError(err.message);
    } finally {
      starting = false;
    }
  }

  async function cancel() {
    try {
      job = await api.cancelJob(job.id);
      announce('Cancelling the render…');
    } catch (err) {
      announceError(err.message);
    }
  }

  // Re-attach to the last render after a reload (it keeps running in the engine).
  onMount(async () => {
    if (!project.lastRenderJobId) return;
    try {
      job = await api.job(project.lastRenderJobId);
      if (job.state === 'succeeded') await showResult(job);
      else if (job.state === 'queued' || job.state === 'running') follow(job.id);
    } catch {
      project.lastRenderJobId = null; // the engine restarted; that job is gone
    }
  });

  $effect(() => {
    if (video) video.playbackRate = Number(rate);
  });
</script>

<section aria-labelledby="roll-heading">
  <h2 id="roll-heading" tabindex="-1">Step 3 of 3: Roll</h2>
  <p class="muted">Choose how the video looks, then render it.</p>

  <section class="card block" aria-labelledby="checklist-heading">
    <h3 id="checklist-heading">Ready to render?</h3>
    {#if problems.length}
      <p>Fix these first ({problems.length}):</p>
      <ul class="problems">
        {#each problems as p, i (i)}
          <li>
            <span class="error-text">{p.message}</span>
            <button type="button" onclick={() => goTo(p.step === 'setup' ? 'cast' : p.step, p.fieldId)}>
              Fix in {stepLabel(p.step)}<span class="visually-hidden">: {p.message}</span>
            </button>
          </li>
        {/each}
      </ul>
    {:else}
      <p class="ok-text">Everything is ready: {project.cast.length} cast member{project.cast.length === 1 ? '' : 's'}, {project.script.length} line{project.script.length === 1 ? '' : 's'}.</p>
    {/if}
  </section>

  <div class="two-col block">
    <section class="card" aria-labelledby="framing-heading">
      <h3 id="framing-heading">Framing</h3>
      <fieldset>
        <legend>Video size</legend>
        <div class="cards">
          {#each PRESETS as p (p.id)}
            <label class="option">
              <input type="radio" name="preset" value={p.id} bind:group={project.output.preset} />
              <span><strong>{p.label}</strong><br />
                <span class="muted">{p.size ? `${p.size[0]}×${p.size[1]}` : 'Scene size'} · {p.note}</span></span>
            </label>
          {/each}
        </div>
      </fieldset>
      {#if preset.size}
        <fieldset>
          <legend>When the shapes differ</legend>
          <label class="choice"><input type="radio" name="fit" value="fit" bind:group={project.output.fit} />
            Fit: show the whole scene (adds bars)</label>
          <label class="choice"><input type="radio" name="fit" value="fill" bind:group={project.output.fit} />
            Fill: cover the frame (crops the edges)</label>
        </fieldset>
      {/if}
      {#if layout}
        <div class="preview-frame" style="aspect-ratio:{layout.width} / {layout.height}" aria-hidden="true">
          <div class="scene" style="left:{(layout.offsetX ?? 0) / layout.width * 100}%;top:{(layout.offsetY ?? 0) / layout.height * 100}%;width:{(layout.scaledW ?? layout.width) / layout.width * 100}%;height:{(layout.scaledH ?? layout.height) / layout.height * 100}%"></div>
        </div>
      {/if}
      <p id="framing-desc">{framingText}</p>
    </section>

    <section class="card" aria-labelledby="quality-heading">
      <h3 id="quality-heading">Quality and file</h3>
      <fieldset>
        <legend>Quality</legend>
        <label class="choice"><input type="radio" name="quality" value="standard" bind:group={project.quality.mode} />
          Standard <span class="muted">(faster)</span></label>
        <label class="choice"><input type="radio" name="quality" value="enhanced" bind:group={project.quality.mode} />
          Enhanced faces <span class="muted">(GFPGAN; about 3× slower)</span></label>
      </fieldset>
      <div class="field">
        <label for="filename">File name <span class="muted">(optional)</span></label>
        <input id="filename" type="text" bind:value={project.output.filename} autocomplete="off" aria-describedby="filename-hint" />
        <p class="hint" id="filename-hint">Leave empty for an automatic name. “.mp4” is added for you.</p>
      </div>
      <p><strong>Computer:</strong> {engine.hardware?.device?.label ?? 'Detecting…'}</p>
      <p class="muted">Each character is animated only while they speak. On a laptop GPU, expect roughly 1–2 seconds per spoken frame (25 frames per second). The time left is shown once rendering starts.</p>
    </section>
  </div>

  <section class="card block" aria-labelledby="render-heading">
    <h3 id="render-heading">Render</h3>
    <button type="button" class="primary big" onclick={render} disabled={problems.length > 0 || running || starting}
      aria-describedby={problems.length ? 'render-blocked' : undefined}>🎬 Render video</button>
    {#if problems.length}<p class="muted" id="render-blocked">Fix the items above to enable rendering.</p>{/if}
    {#if startError}<p class="error-text">{startError}</p>{/if}
    <JobProgress {job} id="render-job" label="Render progress" oncancel={running ? cancel : null}
      stageLabels={{ audio: 'Voices and timing', compose: 'Compose and encode' }} />

    {#if result && videoUrl}
      <section class="result" aria-labelledby="result-heading">
        <h3 id="result-heading">Your video</h3>
        <!-- svelte-ignore a11y_media_has_caption -->
        <video bind:this={video} controls preload="metadata" src={videoUrl} aria-describedby="result-details">
          {#if vttUrl}<track kind="captions" srclang="en" label="English" src={vttUrl} default />{/if}
          Your browser can’t play this video. Use the download link below.
        </video>
        <p id="result-details" class="muted">{result.width}×{result.height}, {duration(result.duration_s)}{vttUrl ? ', with captions' : ''}.</p>
        <div class="row">
          <div class="field">
            <label for="speed">Playback speed</label>
            <select id="speed" bind:value={rate}>
              <option value="0.5">0.5×</option><option value="0.75">0.75×</option><option value="1">1× (normal)</option>
              <option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="2">2×</option>
            </select>
          </div>
        </div>
        <ul class="downloads">
          <li><a href={videoUrl} download>Download video (MP4)</a></li>
          {#if srtUrl}<li><a href={srtUrl} download>Download captions (SRT)</a></li>{/if}
          {#if result.vtt_path}<li><a href={vttUrl} download>Download captions (WebVTT)</a></li>{/if}
        </ul>
        <p class="muted">Saved on this computer in <code>{result.output_dir}</code></p>
        {#if result.captions?.length}
          <details>
            <summary>Transcript</summary>
            <ol class="transcript">
              {#each result.captions as c, i (i)}
                <li><span class="muted">{timestamp(c.start_ms)}</span> <strong>{c.speaker}:</strong> {c.text}</li>
              {/each}
            </ol>
          </details>
        {/if}
      </section>
    {/if}
  </section>
</section>

<style>
  .block { margin-bottom: 1.25rem; }
  .problems { list-style: none; padding: 0; display: grid; gap: 0.5rem; }
  .problems li { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; align-items: center; justify-content: space-between; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: 0.5rem; }
  .option {
    display: flex; gap: 0.6rem; align-items: flex-start; font-weight: 400;
    border: 1px solid var(--border); border-radius: 8px; padding: 0.6rem; cursor: pointer;
  }
  .option:has(input:checked) { border: 2px solid var(--accent); background: var(--surface-2); }
  .option input { margin-top: 0.2rem; }
  .choice { display: flex; align-items: center; gap: 0.5rem; font-weight: 400; min-height: var(--target); }
  .preview-frame { position: relative; width: min(100%, 18rem); max-height: 18rem; background: #000; border: 1px solid var(--border); overflow: hidden; margin-bottom: 0.5rem; }
  .scene { position: absolute; background: repeating-linear-gradient(45deg, #6b8fd6, #6b8fd6 6px, #9db6ea 6px, #9db6ea 12px); }
  button.big { font-size: 1.1rem; padding: 0.6rem 1.4rem; margin-bottom: 0.75rem; }
  .result { margin-top: 1rem; }
  video { width: 100%; max-height: 70vh; background: #000; border-radius: 6px; }
  .downloads { display: grid; gap: 0.35rem; padding-left: 1.25rem; }
  .transcript { padding-left: 1.25rem; }
</style>
