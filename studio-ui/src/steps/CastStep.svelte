<script>
  import { api, revokeMedia } from '../lib/api.js';
  import { project, newId, nextStyleIndex, projectGuard, MAX_CAST } from '../lib/project.svelte.js';
  import { announce, announceError } from '../lib/announce.svelte.js';
  import { clampRegion, defaultRegion, overlap } from '../lib/regions.js';
  import Stage from '../components/Stage.svelte';
  import CastMemberCard from '../components/CastMemberCard.svelte';

  let imageUrl = $state('');
  let uploading = $state(false);
  let detecting = $state(false);
  let sensitivity = $state('0.2');
  let fileError = $state('');
  let dragOver = $state(false);
  let selectedId = $state(project.cast[0]?.id ?? null);

  const castFull = $derived(project.cast.length >= MAX_CAST);

  // Show the scene (also after a reload). With an engine token the image is a blob: URL, which
  // is released when the scene changes or the step closes.
  $effect(() => {
    const path = project.image?.path;
    if (!path) {
      imageUrl = '';
      return;
    }
    let cancelled = false;
    let url = '';
    api.uploadedImageUrl(path)
      .then((u) => {
        if (cancelled) revokeMedia(u);
        else imageUrl = url = u;
      })
      .catch(() => { if (!cancelled) fileError = 'The saved scene image is no longer available. Choose it again.'; });
    return () => {
      cancelled = true;
      revokeMedia(url);
    };
  });

  const nameErrors = $derived.by(() => {
    const errors = {};
    const seen = new Map();
    for (const m of project.cast) {
      const key = m.name.trim().split(/\s+/).join(' ').toLowerCase();
      if (!key) errors[m.id] = 'Enter a name.';
      else if (seen.has(key)) errors[m.id] = 'Another cast member already has this name.';
      else seen.set(key, m.id);
    }
    return errors;
  });

  async function chooseFile(file) {
    fileError = '';
    if (!file) return;
    if (!/^image\/(png|jpeg|bmp|webp)$/.test(file.type) && !/\.(png|jpe?g|bmp|webp)$/i.test(file.name)) {
      fileError = `“${file.name}” isn’t a supported image. Use PNG, JPEG, BMP, or WebP.`;
      return;
    }
    uploading = true;
    const current = projectGuard();
    try {
      const res = await api.upload(file);
      if (!current()) return; // a different project was opened meanwhile: don't touch it
      const hadCast = project.cast.length > 0;
      project.image = { path: res.image_path, name: file.name, width: res.width, height: res.height };
      project.detections = [];
      for (const m of project.cast) m.region = clampRegion(m.region, res.width, res.height);
      announce(`Scene loaded: ${file.name}, ${res.width} by ${res.height} pixels.${hadCast ? ' Check that each region still covers its character.' : ' Next, choose Auto-Detect Cast.'}`);
    } catch (err) {
      if (!current()) return;
      fileError = err.message;
      announceError(err.message);
    } finally {
      uploading = false;
    }
  }

  async function autoCast() {
    detecting = true;
    const current = projectGuard();
    const imagePath = project.image.path;
    try {
      const res = await api.detectCast(imagePath, Number(sensitivity));
      // Drop the answer if another project was opened or the scene was replaced meanwhile.
      if (!current() || project.image?.path !== imagePath) return;
      project.detections = res.cast;
      let added = 0;
      let skipped = 0;
      for (const s of res.cast) {
        if (project.cast.some((m) => overlap(m.region, s.region) > 0.3)) continue;
        if (project.cast.length >= MAX_CAST) {
          skipped++;
          continue;
        }
        const member = { id: newId('c'), name: `Character ${project.cast.length + 1}`, region: { ...s.region },
          voice: 'neutral', semitones: 0, style: nextStyleIndex(project.cast) };
        project.cast.push(member);
        added++;
      }
      selectedId ??= project.cast[0]?.id ?? null;
      announce(`${res.message} ${added ? `Added ${added} cast member${added === 1 ? '' : 's'}. Name each one in the list.` : skipped ? '' : 'No new cast members (they are already cast).'}`
        + (skipped ? ` ${skipped} more face${skipped === 1 ? ' was' : 's were'} not added: a video can have up to ${MAX_CAST} cast members.` : ''));
    } catch (err) {
      if (!current()) return;
      announceError(err.message);
      fileError = err.message;
    } finally {
      detecting = false;
    }
  }

  function addRegion() {
    if (castFull) return;
    const member = { id: newId('c'), name: `Character ${project.cast.length + 1}`,
      region: defaultRegion(project.image.width, project.image.height), voice: 'neutral', semitones: 0,
      style: nextStyleIndex(project.cast) };
    project.cast.push(member);
    selectedId = member.id;
    announce(`Added ${member.name} with a region in the middle of the scene. Move it with the arrow keys or the X and Y fields.`);
  }

  function removeMember(id) {
    const i = project.cast.findIndex((m) => m.id === id);
    if (i < 0) return;
    const [m] = project.cast.splice(i, 1);
    for (const line of project.script) if (line.castId === id) line.castId = null;
    if (selectedId === id) selectedId = project.cast[0]?.id ?? null;
    announce(`Removed ${m.name || 'cast member'}.${project.script.some((l) => !l.castId) ? ' Some lines now need a speaker.' : ''}`);
    // Keep keyboard users in the list: focus the next member's name (or Auto-Detect if none left).
    const next = project.cast[Math.min(i, project.cast.length - 1)];
    document.getElementById(next ? `name-${next.id}` : 'auto-cast')?.focus();
  }

  function setRegion(id, region) {
    const m = project.cast.find((c) => c.id === id);
    if (m) m.region = region;
  }
</script>

<section aria-labelledby="cast-heading">
  <h2 id="cast-heading" tabindex="-1">Step 1 of 3: Cast</h2>
  <p class="muted">Choose a picture, find the faces, and give each character a name and a voice.</p>

  <div class="two-col">
    <section class="card" aria-labelledby="stage-heading">
      <h3 id="stage-heading">Stage</h3>
      <!-- Dropping a file is a pointer shortcut for the file input inside (the real control). -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div class="drop" class:over={dragOver}
        ondragover={(e) => { e.preventDefault(); dragOver = true; }} ondragleave={() => (dragOver = false)}
        ondrop={(e) => { e.preventDefault(); dragOver = false; chooseFile(e.dataTransfer?.files?.[0]); }}>
        <div class="field">
          <label for="scene-file">{project.image ? 'Replace the scene image' : 'Scene image'}</label>
          <input id="scene-file" type="file" accept="image/png,image/jpeg,image/bmp,image/webp"
            aria-describedby="scene-file-hint{fileError ? ' scene-file-error' : ''}" aria-invalid={fileError ? 'true' : undefined}
            disabled={uploading} onchange={(e) => chooseFile(e.currentTarget.files?.[0])} />
          <p class="hint" id="scene-file-hint">A portrait, a video-call screenshot, or a comic panel. PNG, JPEG, BMP, or WebP, up to 50 MB. You can also drop the file here.</p>
          {#if uploading}<p class="muted" role="status">Uploading…</p>{/if}
          {#if fileError}<p class="error-text" id="scene-file-error">{fileError}</p>{/if}
        </div>
      </div>

      {#if project.image && imageUrl}
        <Stage image={project.image} {imageUrl} cast={project.cast} {selectedId}
          onselect={(id) => (selectedId = id)} onregion={setRegion} />
      {/if}

      {#if project.image}
        <div class="row actions">
          <div class="field">
            <label for="sensitivity">Detection sensitivity</label>
            <select id="sensitivity" bind:value={sensitivity}>
              <option value="0.5">Standard (photos)</option>
              <option value="0.2">Sensitive (drawn characters)</option>
              <option value="0.05">Very sensitive</option>
            </select>
          </div>
          <button type="button" id="auto-cast" class="primary" onclick={autoCast} disabled={detecting}>
            <span aria-hidden="true">✨</span> {detecting ? 'Detecting…' : 'Auto-Detect Cast'}
          </button>
          <button type="button" onclick={addRegion} disabled={castFull}
            aria-describedby={castFull ? 'cast-full' : undefined}>+ Add region</button>
        </div>
        {#if castFull}
          <p class="hint" id="cast-full">The cast is full: a video can have up to {MAX_CAST} cast members. Remove one to add another.</p>
        {/if}
      {:else}
        <span id="auto-cast" tabindex="-1"></span>
      {/if}
    </section>

    <section aria-labelledby="inspector-heading">
      <h3 id="inspector-heading">Cast <span class="muted">({project.cast.length})</span></h3>
      {#if !project.cast.length}
        <p class="muted">No cast members yet.</p>
      {/if}
      {#each project.cast as member, i (member.id)}
        <CastMemberCard {member} index={i} image={project.image} detections={project.detections}
          selected={member.id === selectedId} nameError={nameErrors[member.id] ?? ''}
          onselect={(id) => (selectedId = id)} onremove={removeMember} />
      {/each}
    </section>
  </div>
</section>

<style>
  .drop { border: 2px dashed var(--border); border-radius: var(--radius); padding: 0.75rem 1rem; margin-bottom: 1rem; }
  .drop.over { border-color: var(--accent); background: var(--surface-2); }
  .actions { margin-top: 1rem; align-items: flex-end; }
  .actions .field { margin-bottom: 0; }
</style>
