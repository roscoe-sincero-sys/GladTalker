<script>
  import { api } from '../lib/api.js';
  import { engine } from '../lib/app-state.svelte.js';
  import { announce, announceError } from '../lib/announce.svelte.js';
  import { clampRegion, regionProblems, snapToFace, expandToTile } from '../lib/regions.js';
  import { signed } from '../lib/format.js';
  import NumberField from './NumberField.svelte';
  import SpeakerBadge from './SpeakerBadge.svelte';

  let { member, index, image, detections = [], selected = false, nameError = '', onselect, onremove } = $props();

  let previewing = $state(false);
  let regionNote = $state('');
  const id = $derived(member.id);
  const problems = $derived(image ? regionProblems(member.region, image.width, image.height) : []);
  const label = $derived(member.name.trim() || `cast member ${index + 1}`);

  function pickVoice(voiceId) {
    member.voice = voiceId;
    const preset = engine.voices.find((v) => v.id === voiceId);
    if (preset) member.semitones = preset.semitones;
  }

  function pitchChanged(value) {
    const preset = engine.voices.find((v) => v.semitones === value);
    member.voice = preset ? preset.id : 'custom';
  }

  function setRegion(field, value) {
    member.region = clampRegion({ ...member.region, [field]: value }, image.width, image.height);
  }

  function useSuggestion(kind) {
    const next = kind === 'face'
      ? snapToFace(member.region, detections, image.width, image.height)
      : expandToTile(member.region, detections, image.width, image.height);
    if (next) {
      member.region = next;
      regionNote = '';
      announce(`${label}: region ${kind === 'face' ? 'snapped to the face' : 'expanded to the tile'}. X ${next.x}, Y ${next.y}, width ${next.w}, height ${next.h}.`);
    } else {
      regionNote = kind === 'face'
        ? 'No detected face for this region. Run Auto-Detect Cast first.'
        : 'No tile or panel was found around this face. Adjust the region with X, Y, W, H instead.';
      announce(regionNote);
    }
  }

  async function preview() {
    previewing = true;
    try {
      const blob = await api.previewVoice(`Hello, I'm ${member.name.trim() || 'a new character'}.`, member.semitones);
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      announce(`Playing ${label}'s voice.`);
      await audio.play();
    } catch (err) {
      announceError(err.message);
    } finally {
      previewing = false;
    }
  }
</script>

<fieldset class="member card" class:selected onfocusin={() => onselect(id)}>
  <legend>
    <SpeakerBadge index={member.style} number={index + 1} />
    <span>Cast member {index + 1}<span class="visually-hidden">: {member.name}</span></span>
  </legend>

  <div class="field">
    <label for="name-{id}">Name</label>
    <input id="name-{id}" type="text" bind:value={member.name} autocomplete="off" required
      aria-invalid={nameError ? 'true' : undefined} aria-describedby={nameError ? `name-error-${id}` : undefined} />
    {#if nameError}<p class="error-text" id="name-error-{id}">{nameError}</p>{/if}
  </div>

  <div class="voice-row">
    <div class="field">
      <label for="voice-{id}">Voice</label>
      <select id="voice-{id}" value={member.voice} onchange={(e) => pickVoice(e.currentTarget.value)}>
        {#each engine.voices as v (v.id)}
          <option value={v.id}>{v.label} ({signed(v.semitones)})</option>
        {/each}
        <option value="custom">Custom pitch</option>
      </select>
    </div>
    <NumberField id="pitch-{id}" label="Pitch" unit="st" bind:value={member.semitones}
      min={engine.semitoneRange[0]} max={engine.semitoneRange[1]} step={0.5} onchange={pitchChanged}
      hint="Semitones: lower is deeper, higher is lighter." />
  </div>
  <button type="button" onclick={preview} disabled={previewing} aria-describedby="preview-hint-{id}">
    <span aria-hidden="true">▶</span> {previewing ? 'Preparing…' : 'Preview voice'}
  </button>
  <p class="muted small" id="preview-hint-{id}">Plays a short sample, about 2 seconds.</p>

  {#if image}
    <fieldset class="region-fields">
      <legend>Region <span class="muted">(pixels in the scene)</span></legend>
      <div class="grid4">
        <NumberField id="region-x-{id}" label="X" value={member.region.x} min={0} max={image.width - 1}
          onchange={(v) => setRegion('x', v)} error={problems[0] ?? ''} />
        <NumberField id="region-y-{id}" label="Y" value={member.region.y} min={0} max={image.height - 1}
          onchange={(v) => setRegion('y', v)} />
        <NumberField id="region-w-{id}" label="W" value={member.region.w} min={1} max={image.width}
          onchange={(v) => setRegion('w', v)} />
        <NumberField id="region-h-{id}" label="H" value={member.region.h} min={1} max={image.height}
          onchange={(v) => setRegion('h', v)} />
      </div>
      <div class="row">
        <button type="button" onclick={() => useSuggestion('face')}>Snap to face</button>
        <button type="button" onclick={() => useSuggestion('tile')}>Expand to tile</button>
      </div>
      {#if regionNote}<p class="muted">{regionNote}</p>{/if}
      {#if problems.length > 1}
        <ul class="error-list" id="region-problems-{id}">
          {#each problems.slice(1) as p}<li class="error-text">{p}</li>{/each}
        </ul>
      {/if}
    </fieldset>
  {/if}

  <button type="button" class="danger" onclick={() => onremove(id)}>Remove {label}</button>
</fieldset>

<style>
  .member { margin-bottom: 1rem; }
  .member.selected { border: 2px solid var(--accent); }
  legend { display: inline-flex; align-items: center; gap: 0.5rem; }
  .voice-row { display: flex; flex-wrap: wrap; gap: 0.75rem 1.5rem; align-items: flex-start; }
  .grid4 { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: 0.5rem 1rem; margin-bottom: 0.75rem; }
  .small { font-size: 0.9rem; margin-top: 0.25rem; }
  .region-fields { margin-top: 0.75rem; }
  .error-list { list-style: none; padding: 0; margin: 0.5rem 0 0; }
</style>
