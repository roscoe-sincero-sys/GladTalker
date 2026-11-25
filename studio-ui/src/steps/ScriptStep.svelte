<script>
  import { tick } from 'svelte';
  import { api } from '../lib/api.js';
  import { project, newId, nextStyleIndex, audioPreviewRequest } from '../lib/project.svelte.js';
  import { announce, announceError } from '../lib/announce.svelte.js';
  import { linesFromParse, moveLine, nameKey } from '../lib/script-lines.js';
  import { defaultRegion } from '../lib/regions.js';
  import { pacingLabel, timestamp, duration } from '../lib/format.js';
  import DialogueRow from '../components/DialogueRow.svelte';
  import NumberField from '../components/NumberField.svelte';
  import JobProgress from '../components/JobProgress.svelte';

  let pasted = $state('');
  let importing = $state(false);
  let importMessage = $state('');
  let unknownSpeakers = $state([]);
  let warnings = $state({}); // line id -> [{code, message, fix}]
  let playingId = $state(null);
  let previewJob = $state(null);
  let previewUrl = $state('');
  let previewKey = $state('');

  const castNames = $derived(project.cast.map((m) => m.name.trim()).filter(Boolean));
  const currentKey = $derived(JSON.stringify(audioPreviewRequest(project)));
  const previewStale = $derived(previewKey && previewKey !== currentKey);
  const previewResult = $derived(previewJob?.state === 'succeeded' ? previewJob.result : null);
  const canPreview = $derived(project.cast.length > 0 && project.script.length > 0
    && project.script.every((l) => l.castId && l.text.trim()));

  // Re-check pronunciation shortly after typing stops.
  let lintTimer;
  $effect(() => {
    const lines = project.script.map((l) => ({ id: l.id, text: l.text }));
    clearTimeout(lintTimer);
    lintTimer = setTimeout(async () => {
      if (!lines.length) return (warnings = {});
      try {
        const res = await api.lintLines(lines.map((l) => l.text));
        warnings = Object.fromEntries(lines.map((l, i) => [l.id, res.lines[i] ?? []]));
      } catch {
        /* lint is advisory: ignore failures */
      }
    }, 600);
    return () => clearTimeout(lintTimer);
  });

  async function importScript(mode) {
    importMessage = '';
    unknownSpeakers = [];
    if (!pasted.trim()) {
      importMessage = 'Paste a script first.';
      return;
    }
    importing = true;
    try {
      const parsed = await api.parseScript(pasted, castNames);
      const { lines, unknownSpeakers: unknown, warningCount } = linesFromParse(parsed, project.cast);
      project.script = mode === 'replace' ? lines : [...project.script, ...lines];
      unknownSpeakers = unknown;
      importMessage = `Imported ${lines.length} line${lines.length === 1 ? '' : 's'}`
        + (warningCount ? ` with ${warningCount} suggestion${warningCount === 1 ? '' : 's'} to review.` : '.')
        + (unknown.length ? ` Not in the cast: ${unknown.join(', ')}.` : '');
      announce(importMessage);
    } catch (err) {
      importMessage = err.message;
      announceError(err.message);
    } finally {
      importing = false;
    }
  }

  function addToCast(name) {
    const img = project.image;
    const region = img ? defaultRegion(img.width, img.height) : { x: 0, y: 0, w: 64, h: 64 };
    const member = { id: newId('c'), name, region, voice: 'neutral', semitones: 0, style: nextStyleIndex(project.cast) };
    project.cast.push(member);
    const key = nameKey(name);
    let linked = 0;
    for (const l of project.script) {
      if (!l.castId && l.pendingSpeaker && nameKey(l.pendingSpeaker) === key) {
        l.castId = member.id;
        l.pendingSpeaker = null;
        linked++;
      }
    }
    unknownSpeakers = unknownSpeakers.filter((n) => nameKey(n) !== key);
    announce(`Added ${name} to the cast and gave them ${linked} line${linked === 1 ? '' : 's'}. Set their region in the Cast step.`);
  }

  async function onmove(index, delta) {
    const next = moveLine(project.script, index, delta);
    if (!next) return;
    const focusedId = document.activeElement?.id;
    project.script = next;
    await tick();
    if (focusedId) document.getElementById(focusedId)?.focus();
    announce(`Line ${index + 1} moved to position ${index + delta + 1}.`);
  }

  async function ondelete(index) {
    const [removed] = project.script.splice(index, 1);
    announce(`Deleted line ${index + 1}${removed?.text ? `: ${removed.text}` : ''}.`);
    await tick();
    const next = project.script[Math.min(index, project.script.length - 1)];
    document.getElementById(next ? `text-${next.id}` : 'add-line')?.focus();
  }

  async function addLine() {
    const last = project.script.at(-1);
    const line = { id: newId('l'), castId: last?.castId ?? project.cast[0]?.id ?? null, text: '' };
    project.script.push(line);
    await tick();
    document.getElementById(`text-${line.id}`)?.focus();
  }

  async function onplay(line) {
    const member = project.cast.find((m) => m.id === line.castId);
    if (!member) return;
    playingId = line.id;
    try {
      const url = URL.createObjectURL(await api.previewVoice(line.text, member.semitones));
      const audio = new Audio(url);
      audio.onended = () => { URL.revokeObjectURL(url); playingId = null; };
      announce(`Playing ${member.name}: ${line.text}`);
      await audio.play();
    } catch (err) {
      playingId = null;
      announceError(err.message);
    }
  }

  function onfix(line, fixed) {
    line.text = fixed;
    announce('Applied the suggested wording.');
  }

  async function previewMix() {
    previewUrl = '';
    const body = audioPreviewRequest(project);
    previewKey = JSON.stringify(body);
    try {
      previewJob = await api.previewAudio(body);
      project.lastPreviewJobId = previewJob.id;
      announce('Building the full audio mix…');
      const final = await api.watchJob(previewJob.id, (j) => (previewJob = j)).done;
      if (final?.state === 'succeeded') {
        previewUrl = await api.fileUrl(final.id, 'audio');
        announce(`Audio mix ready: ${duration(final.result.duration_s)} at ${pacingLabel(final.result.tempo)}. Use the player to listen.`);
      } else if (final?.state === 'failed') {
        announceError(final.error || 'The audio preview failed.');
      }
    } catch (err) {
      previewKey = '';
      announceError(err.message);
      previewJob = { state: 'failed', error: err.message, message: err.message, progress: 0, stages: [] };
    }
  }
</script>

<section aria-labelledby="script-heading">
  <h2 id="script-heading" tabindex="-1">Step 2 of 3: Script</h2>
  <p class="muted">Write what each character says. Paste a whole script, or add lines one by one.</p>

  <section class="card block" aria-labelledby="import-heading">
    <h3 id="import-heading">Import a script</h3>
    <div class="field">
      <label for="script-import">Paste a script</label>
      <textarea id="script-import" bind:value={pasted} rows="6" aria-describedby="script-import-hint"
        placeholder="Brain: I'll show you how to change the time.&#10;Pinky: I'm clicking."></textarea>
      <p class="hint" id="script-import-hint">One line per speech, starting with the speaker’s name and a colon, for example “Brain: Click it.”. Lines without a name continue the previous line.</p>
    </div>
    <div class="row">
      <button type="button" class="primary" onclick={() => importScript('replace')} disabled={importing}>
        {project.script.length ? 'Import, replacing all lines' : 'Import'}
      </button>
      {#if project.script.length}
        <button type="button" onclick={() => importScript('append')} disabled={importing}>Import, adding to the end</button>
      {/if}
    </div>
    {#if importMessage}<p class="import-msg">{importMessage}</p>{/if}
    {#if unknownSpeakers.length}
      <div class="row">
        {#each unknownSpeakers as name (name)}
          <button type="button" onclick={() => addToCast(name)}>Add “{name}” to the cast</button>
        {/each}
      </div>
    {/if}
  </section>

  <section class="block" aria-labelledby="dialogue-heading">
    <h3 id="dialogue-heading">Dialogue <span class="muted">({project.script.length} line{project.script.length === 1 ? '' : 's'})</span></h3>
    {#if !project.cast.length}
      <p class="muted">Add cast members in the Cast step first, so lines can be given a speaker.</p>
    {/if}
    {#if project.script.length}
      <ol class="lines">
        {#each project.script as line, i (line.id)}
          <DialogueRow {line} index={i} total={project.script.length} cast={project.cast}
            warnings={warnings[line.id] ?? []} playing={playingId === line.id}
            {onmove} {ondelete} {onplay} {onfix} />
        {/each}
      </ol>
    {/if}
    <button type="button" id="add-line" onclick={addLine}>+ Add line</button>
  </section>

  <div class="two-col block">
    <section class="card" aria-labelledby="pacing-heading">
      <h3 id="pacing-heading">Pacing</h3>
      <fieldset>
        <legend>Speaking pace</legend>
        <label class="choice"><input type="radio" name="pacing" value="natural" bind:group={project.pacing.mode} />
          Natural pace <span class="muted">(the video is as long as the dialogue)</span></label>
        <label class="choice"><input type="radio" name="pacing" value="target" bind:group={project.pacing.mode} />
          Fit a target length <span class="muted">(speeds speech up, up to 2×)</span></label>
      </fieldset>
      {#if project.pacing.mode === 'target'}
        <NumberField id="target-seconds" label="Target length" unit="s" bind:value={project.pacing.targetSeconds}
          min={1} max={600} step={1} hint="The video will be exactly this long." />
      {:else}
        <NumberField id="gap-ms" label="Pause between lines" unit="ms" bind:value={project.pacing.gapMs}
          min={0} max={5000} step={50} />
      {/if}

      <div class="preview">
        <button type="button" onclick={previewMix} disabled={!canPreview || previewJob?.state === 'running' || previewJob?.state === 'queued'}
          aria-describedby="preview-mix-hint">▶ Preview full audio mix</button>
        <p class="hint muted" id="preview-mix-hint">
          {canPreview ? 'Hear every line with its voice and pacing, before rendering any video.' : 'Every line needs a speaker and text first.'}
        </p>
        <JobProgress job={previewJob?.state === 'succeeded' ? null : previewJob} id="preview-job" label="Audio mix" />
        {#if previewResult}
          {#if previewStale}<p class="error-text">The script or voices changed since this preview. Preview again to hear the changes.</p>{/if}
          <div class="field">
            <label for="pace-meter">Speaking pace: {pacingLabel(previewResult.tempo)}</label>
            <meter id="pace-meter" min="1" max="2" low="1.25" high="1.6" optimum="1" value={previewResult.tempo}>{pacingLabel(previewResult.tempo)}</meter>
          </div>
          <p>Length: {duration(previewResult.duration_s)} ({previewResult.frames} frames)</p>
          {#if previewUrl}
            <audio controls src={previewUrl} aria-label="Full audio mix">Your browser can’t play this audio.</audio>
          {/if}
          <details>
            <summary>Timeline</summary>
            <table class="timeline">
              <caption class="visually-hidden">When each line is spoken</caption>
              <thead><tr><th scope="col">Starts</th><th scope="col">Speaker</th><th scope="col">Line</th></tr></thead>
              <tbody>
                {#each previewResult.captions as c, i (i)}
                  <tr><td>{timestamp(c.start_ms)}</td><td>{c.speaker}</td><td>{c.text}</td></tr>
                {/each}
              </tbody>
            </table>
          </details>
        {/if}
      </div>
    </section>

    <section class="card" aria-labelledby="captions-heading">
      <h3 id="captions-heading">Captions</h3>
      <label class="choice"><input type="checkbox" id="captions" bind:checked={project.captions} />
        Create closed captions</label>
      <p class="hint muted">Makes <code>.srt</code> and <code>.vtt</code> caption files from the script, with each speaker named, so viewers who are deaf or hard of hearing can follow along. Recommended.</p>
    </section>
  </div>
</section>

<style>
  .block { margin-bottom: 1.25rem; }
  .lines { list-style: none; padding: 0; margin: 0 0 0.75rem; display: grid; gap: 0.6rem; }
  .choice { display: flex; align-items: center; gap: 0.5rem; font-weight: 400; min-height: var(--target); }
  .preview { margin-top: 1rem; display: grid; gap: 0.5rem; justify-items: start; }
  .preview > :global(*) { max-width: 100%; }
  .preview audio, .preview details, .preview .field { justify-self: stretch; }
  .import-msg { margin-top: 0.75rem; font-weight: 600; }
  audio { width: 100%; }
  .timeline { border-collapse: collapse; width: 100%; margin-top: 0.5rem; }
  .timeline th, .timeline td { text-align: left; padding: 0.3rem 0.5rem; border-bottom: 1px solid var(--border); vertical-align: top; }
  .timeline td:first-child { font-variant-numeric: tabular-nums; white-space: nowrap; }
</style>
