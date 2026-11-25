<script>
  import { onDestroy, tick, untrack } from 'svelte';
  import { api, revokeMedia, whenNews } from '../lib/api.js';
  import { project, newId, nextStyleIndex, audioPreviewRequest, projectGuard, previewDigest,
    MAX_CAST, MAX_LINES, MAX_LINE_CHARS } from '../lib/project.svelte.js';
  import { submitting, submitPreview } from '../lib/jobs.svelte.js';
  import { goTo } from '../lib/app-state.svelte.js';
  import { announce, announceError } from '../lib/announce.svelte.js';
  import { linesFromParse, moveLine, nameKey } from '../lib/script-lines.js';
  import { audioProblems } from '../lib/validate.js';
  import { playBlob } from '../lib/player.js';
  import { defaultRegion } from '../lib/regions.js';
  import { pacingLabel, timestamp, duration } from '../lib/format.js';
  import DialogueRow from '../components/DialogueRow.svelte';
  import NumberField from '../components/NumberField.svelte';
  import JobProgress from '../components/JobProgress.svelte';

  let pasted = $state('');
  let importing = $state(false);
  let importMessage = $state('');
  let unknownSpeakers = $state([]);
  // line id -> { text, items: [{code, message, fix}] }. Suggestions belong to the exact text they
  // were made for: once the line changes they are hidden, and Auto-Fix can't apply an old one.
  let warnings = $state({});
  let playingId = $state(null);
  let previewJob = $state(null); // progress, then the finished job (set together with its audio)
  let previewUrl = $state('');
  let previewError = $state('');
  let previewLoading = $state(false); // the job is done; its audio is being fetched
  let previewAttachedId = null;
  let previewWatch = null;
  let destroyed = false;

  const castNames = $derived(project.cast.map((m) => m.name.trim()).filter(Boolean));
  const currentKey = $derived(previewDigest(audioPreviewRequest(project)));
  const previewResult = $derived(previewJob?.state === 'succeeded' ? previewJob.result : null);
  const previewStale = $derived(Boolean(previewResult && project.lastPreviewKey && project.lastPreviewKey !== currentKey));
  // Busy also while re-attaching to a saved preview whose state isn't known yet, so coming back
  // to this step on a slow connection can't start a second preview on top of it.
  const previewSettled = $derived(previewError || ['succeeded', 'failed', 'cancelled'].includes(previewJob?.state));
  const previewBusy = $derived(submitting.preview || previewLoading || Boolean(project.lastPreviewJobId && !previewSettled));
  const previewBlockers = $derived(audioProblems(project));
  const castFull = $derived(project.cast.length >= MAX_CAST);
  const lintFor = (line) => (warnings[line.id]?.text === line.text ? warnings[line.id].items : []);

  function setPreviewUrl(url) {
    revokeMedia(previewUrl);
    previewUrl = url;
  }

  // Show whichever preview the project points at (one just started, possibly accepted while
  // this step was closed, or the last one after coming back).
  $effect(() => {
    const id = project.lastPreviewJobId;
    untrack(() => {
      if (id !== previewAttachedId) attachPreview(id);
    });
  });

  onDestroy(() => {
    destroyed = true;
    previewWatch?.close();
    revokeMedia(previewUrl);
  });

  // Re-check pronunciation shortly after typing stops. Each check gets a number; an answer that
  // arrives after a newer check was sent is dropped, so stale suggestions never come back.
  let lintTimer;
  let lintSeq = 0;
  $effect(() => {
    const lines = project.script.map((l) => ({ id: l.id, text: l.text }));
    clearTimeout(lintTimer);
    lintTimer = setTimeout(async () => {
      const seq = ++lintSeq;
      if (!lines.length) return (warnings = {});
      try {
        // A line over the engine's limit isn't checked (its fixes would replace the whole line
        // with a shortened one); it already shows an error asking for it to be split.
        const fits = (l) => l.text.length <= MAX_LINE_CHARS;
        const res = await api.lintLines(lines.slice(0, MAX_LINES).map((l) => (fits(l) ? l.text : '')));
        if (seq !== lintSeq || destroyed) return;
        warnings = Object.fromEntries(lines.map((l, i) => [l.id, { text: l.text, items: fits(l) ? res.lines[i] ?? [] : [] }]));
      } catch {
        /* lint is advisory: ignore failures */
      }
    }, 600);
    return () => clearTimeout(lintTimer);
  });

  async function importScript(mode) {
    if (importing) return;
    importMessage = '';
    unknownSpeakers = [];
    if (!pasted.trim()) {
      importMessage = 'Paste a script first.';
      return;
    }
    importing = true;
    const current = projectGuard();
    try {
      const parsed = await api.parseScript(pasted, castNames);
      if (!current()) return; // a different project was opened meanwhile: don't touch it
      const { lines, unknownSpeakers: unknown, warningCount } = linesFromParse(parsed, project.cast);
      const kept = mode === 'replace' ? 0 : project.script.length;
      if (kept + lines.length > MAX_LINES) {
        importMessage = `That would make ${kept + lines.length} lines, but a script can have up to ${MAX_LINES}. `
          + 'Import a shorter script, or delete some lines first.';
        announceError(importMessage);
        return;
      }
      project.script = mode === 'replace' ? lines : [...project.script, ...lines];
      unknownSpeakers = unknown;
      importMessage = `Imported ${lines.length} line${lines.length === 1 ? '' : 's'}`
        + (warningCount ? ` with ${warningCount} suggestion${warningCount === 1 ? '' : 's'} to review.` : '.')
        + (unknown.length ? ` Not in the cast: ${unknown.join(', ')}.` : '');
      announce(importMessage);
    } catch (err) {
      if (!current()) return;
      importMessage = err.message;
      announceError(err.message);
    } finally {
      importing = false;
    }
  }

  function addToCast(name) {
    if (castFull) return;
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

  // After a move, keep focus where the keyboard user was. A move button that is now disabled
  // (the line reached the top or bottom) hands focus to the line's other move button.
  async function onmove(index, delta) {
    const next = moveLine(project.script, index, delta);
    if (!next) return;
    const focusedId = document.activeElement?.id;
    const lineId = project.script[index].id;
    project.script = next;
    await tick();
    const target = [focusedId, delta < 0 ? `down-${lineId}` : `up-${lineId}`, `text-${lineId}`]
      .map((id) => id && document.getElementById(id))
      .find((el) => el && !el.disabled);
    target?.focus();
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
    if (project.script.length >= MAX_LINES) {
      announceError(`A script can have up to ${MAX_LINES} lines.`);
      return;
    }
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
      const blob = await api.previewVoice(line.text, member.semitones);
      announce(`Playing ${member.name}: ${line.text}`);
      await playBlob(blob, () => { if (playingId === line.id) playingId = null; });
    } catch (err) {
      if (playingId === line.id) playingId = null;
      announceError(err.message);
    }
  }

  function onfix(line, fixed) {
    if (!lintFor(line).some((w) => w.fix === fixed)) return; // the line changed since: not this fix
    if (line.text.length > MAX_LINE_CHARS) return;
    line.text = fixed;
    announce('Applied the suggested wording.');
  }

  async function previewMix() {
    if (previewBlockers.length || previewBusy) return;
    previewError = '';
    try {
      const accepted = await submitPreview(audioPreviewRequest(project));
      if (accepted) announce('Building the full audio mix…');
    } catch (err) {
      announceError(err.message);
      if (!destroyed) previewError = err.message;
    }
  }

  // One preview at a time on screen. Everything that arrives for an older attempt (progress,
  // the finished job, its audio) is dropped, and a fetched audio blob is released; the finished
  // job and its audio are shown together, so the timeline always matches what plays.
  function attachPreview(id) {
    previewAttachedId = id;
    previewWatch?.close();
    previewWatch = null;
    previewJob = null;
    previewLoading = false;
    setPreviewUrl('');
    if (!id) return;
    previewError = '';
    const mine = () => previewAttachedId === id && !destroyed;
    const wasRunning = api.jobWasRunning(id); // its end is news only if it hadn't ended yet
    previewWatch = api.watchJob(id, (j) => {
      if (mine() && j.state !== 'succeeded') previewJob = j;
    });
    previewWatch.done.then(
      async (final) => {
        if (!mine() || !final) return;
        // The result is shown at once; only the announcement waits for the lookup.
        if (final.state !== 'succeeded') {
          previewJob = final;
          if (final.state === 'failed') whenNews(wasRunning, mine, () => announceError(final.error || 'The audio preview failed.'));
          return;
        }
        previewLoading = true;
        try {
          const url = await api.fileUrl(id, 'audio');
          if (!mine()) return revokeMedia(url);
          previewJob = final;
          setPreviewUrl(url);
          whenNews(wasRunning, mine, () => announce(`Audio mix ready: ${duration(final.result.duration_s)} at ${pacingLabel(final.result.tempo)}. Use the player to listen.`));
        } catch (err) {
          if (!mine()) return;
          previewJob = null;
          previewError = `The audio mix is ready, but it couldn’t be loaded: ${err.message}`;
        } finally {
          if (mine()) previewLoading = false;
        }
      },
      () => {
        // The engine no longer has this preview (restarted, or older than its history): forget it.
        if (mine() && project.lastPreviewJobId === id) project.lastPreviewJobId = null;
      },
    );
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
      <button type="button" class="primary" onclick={() => importScript('replace')} aria-disabled={importing ? 'true' : undefined}>
        {project.script.length ? 'Import, replacing all lines' : 'Import'}
      </button>
      {#if project.script.length}
        <button type="button" onclick={() => importScript('append')} aria-disabled={importing ? 'true' : undefined}>Import, adding to the end</button>
      {/if}
    </div>
    {#if importMessage}<p class="import-msg">{importMessage}</p>{/if}
    {#if unknownSpeakers.length}
      <div class="row">
        {#each unknownSpeakers as name (name)}
          <button type="button" onclick={() => addToCast(name)} aria-disabled={castFull ? 'true' : undefined}
            aria-describedby={castFull ? 'cast-full-hint' : undefined}>Add “{name}” to the cast</button>
        {/each}
      </div>
      {#if castFull}
        <p class="hint" id="cast-full-hint">The cast is full ({MAX_CAST} members). Remove someone in the Cast step to add another, or give these lines to an existing speaker.</p>
      {/if}
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
            warnings={lintFor(line)} playing={playingId === line.id}
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
        <button type="button" onclick={previewMix} disabled={previewBlockers.length > 0} aria-disabled={previewBusy ? 'true' : undefined}
          aria-describedby="preview-mix-hint">▶ {submitting.preview ? 'Starting the preview…' : 'Preview full audio mix'}</button>
        {#if previewBlockers.length}
          <div class="hint" id="preview-mix-hint">
            <p>To preview the mix, first:</p>
            <ul class="blockers">
              {#each previewBlockers.slice(0, 3) as b, i (i)}
                <li>
                  <span>{b.message}</span>
                  <button type="button" onclick={() => goTo(b.step, b.fieldId)}>Fix<span class="visually-hidden">: {b.message}</span></button>
                </li>
              {/each}
            </ul>
            {#if previewBlockers.length > 3}<p class="muted">…and {previewBlockers.length - 3} more (see the Roll checklist).</p>{/if}
          </div>
        {:else}
          <p class="hint muted" id="preview-mix-hint">Hear every line with its voice and pacing, before rendering any video.</p>
        {/if}
        <JobProgress job={previewJob?.state === 'succeeded' ? null : previewJob} id="preview-job" label="Audio mix" />
        {#if previewLoading}<p class="muted">Loading the audio mix…</p>{/if}
        {#if previewError}
          <p class="error-text">{previewError}</p>
          {#if project.lastPreviewJobId && !previewBusy}
            <button type="button" onclick={() => attachPreview(project.lastPreviewJobId)}>Try loading the audio mix again</button>
          {/if}
        {/if}
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
  .blockers { margin: 0.25rem 0 0; padding-left: 1.25rem; display: grid; gap: 0.35rem; }
  .blockers li { display: flex; flex-wrap: wrap; gap: 0.25rem 0.75rem; align-items: center; }
  audio { width: 100%; }
  .timeline { border-collapse: collapse; width: 100%; margin-top: 0.5rem; }
  .timeline th, .timeline td { text-align: left; padding: 0.3rem 0.5rem; border-bottom: 1px solid var(--border); vertical-align: top; }
  .timeline td:first-child { font-variant-numeric: tabular-nums; white-space: nowrap; }
</style>
