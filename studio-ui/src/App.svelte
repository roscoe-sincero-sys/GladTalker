<script>
  import { onMount } from 'svelte';
  import { STEPS, engine, nav, connectEngine, goTo, deviceLabel } from './lib/app-state.svelte.js';
  import { project, saveProjectText, replaceProject, defaultProject } from './lib/project.svelte.js';
  import { live, announce, announceError } from './lib/announce.svelte.js';
  import { renderProblems, problemsForStep } from './lib/validate.js';
  import Stepper from './components/Stepper.svelte';
  import HelpDialog from './components/HelpDialog.svelte';
  import SetupPanel from './components/SetupPanel.svelte';
  import EngineSetup from './components/EngineSetup.svelte';
  import { isDesktop, desktopCall } from './lib/desktop.js';
  import { startRenderMonitor } from './lib/render-monitor.svelte.js';
  import CastStep from './steps/CastStep.svelte';
  import ScriptStep from './steps/ScriptStep.svelte';
  import RollStep from './steps/RollStep.svelte';

  let helpOpen = $state(false);
  let openInput = $state();

  const onDesktop = isDesktop();

  onMount(() => {
    connectEngine();
  });

  // Desktop app: taskbar progress and a notification when a render ends (whichever step is open).
  let stopMonitor = null;
  $effect(() => {
    if (onDesktop && engine.status === 'ready' && !stopMonitor) stopMonitor = startRenderMonitor();
  });

  // Auto-save shortly after any change (WCAG 3.3.7: nothing is entered twice), and at once when
  // the page is hidden or closed, so an edit made just before closing the window isn't lost.
  let saveTimer;
  let unsaved = null;
  function flushSave() {
    clearTimeout(saveTimer);
    if (unsaved !== null) saveProjectText(unsaved);
    unsaved = null;
  }
  $effect(() => {
    unsaved = JSON.stringify(project);
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flushSave, 300);
    return () => clearTimeout(saveTimer);
  });
  onMount(() => {
    const onHidden = () => { if (document.visibilityState === 'hidden') flushSave(); };
    window.addEventListener('pagehide', flushSave);
    document.addEventListener('visibilitychange', onHidden);
    return () => {
      flushSave();
      window.removeEventListener('pagehide', flushSave);
      document.removeEventListener('visibilitychange', onHidden);
    };
  });

  const problems = $derived(renderProblems(project));
  const complete = $derived({
    cast: Boolean(project.image && project.cast.length && !problemsForStep(problems, 'cast').length),
    script: Boolean(project.script.length && !problemsForStep(problems, 'script').length),
  });
  const needsSetup = $derived(engine.status === 'ready'
    && (!engine.models?.complete || !engine.hardware?.ffmpeg?.available));
  const index = $derived(STEPS.findIndex((s) => s.id === nav.step));

  async function saveFile() {
    const contents = JSON.stringify(project, null, 2);
    const base = (project.output.filename || project.cast.map((m) => m.name).join('-') || 'project')
      .replace(/[^\w.-]+/g, '_').slice(0, 60);
    if (onDesktop) {
      // A native Save dialog: the user picks the folder and name.
      try {
        const path = await desktopCall('save_project', { contents, suggestedName: `${base}.gladtalker.json` });
        if (path) announce(`Project saved to ${path}.`);
      } catch (err) {
        announceError(err.message);
      }
      return;
    }
    const blob = new Blob([contents], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${base}.gladtalker.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    announce(`Project saved as ${a.download}.`);
  }

  async function openFile(file) {
    if (!file) return;
    // Like New: opening replaces the current cast and script, so ask first (WCAG 3.3.4).
    if ((project.cast.length || project.script.length)
      && !confirm(`Open ${file.name}? It replaces the current cast and script. Save the project first if you want to keep it.`)) {
      openInput.value = '';
      return;
    }
    try {
      replaceProject(JSON.parse(await file.text()));
      announce(`Opened ${file.name}: ${project.cast.length} cast members, ${project.script.length} lines.`);
      goTo('cast');
    } catch (err) {
      announceError(err instanceof SyntaxError ? `${file.name} isn’t a valid project file.` : err.message);
    } finally {
      openInput.value = '';
    }
  }

  function newProject() {
    if (project.cast.length || project.script.length) {
      if (!confirm('Start a new project? The current cast and script will be cleared. Save the project first if you want to keep it.')) return;
    }
    replaceProject(defaultProject());
    announce('Started a new project.');
    goTo('cast');
  }
</script>

<a class="skip-link" href="#main">Skip to the current step</a>

<div class="app">
  <header class="app-header">
    <div class="brand">
      <h1>GladTalker Studio</h1>
      {#if engine.version}<span class="version">v{engine.version}</span>{/if}
    </div>
    <Stepper {complete} />
    <div class="row project" role="group" aria-label="Project">
      <button type="button" onclick={newProject}>New</button>
      <button type="button" onclick={() => openInput.click()}>Open…</button>
      <input bind:this={openInput} type="file" accept=".json,application/json" class="visually-hidden"
        tabindex="-1" aria-hidden="true" onchange={(e) => openFile(e.currentTarget.files?.[0])} />
      <button type="button" onclick={saveFile}>Save project</button>
    </div>
    <button type="button" class="help-button" aria-haspopup="dialog" onclick={() => (helpOpen = true)}>
      <span aria-hidden="true">?</span> Help
    </button>
  </header>

  <main id="main" tabindex="-1">
    {#if engine.notice}
      <div class="notice card" role="status">
        <p>{engine.notice}</p>
        <button type="button" onclick={() => (engine.notice = '')}>Dismiss</button>
      </div>
    {/if}
    {#if engine.status === 'offline'}
      <section class="card offline" aria-labelledby="offline-heading">
        <h2 id="offline-heading">Can’t reach the GladTalker engine</h2>
        <p>{engine.error}</p>
        {#if onDesktop}
          <p>The engine may have stopped. Try again to restart it.</p>
        {:else}
          <p>Start it with <code>python studio_server.py</code> (in the GladTalker folder), then try again.</p>
        {/if}
        <!-- Desktop: restart it (it may still be running but stuck). -->
        <button type="button" class="primary" onclick={() => connectEngine({ restart: onDesktop })}>Try again</button>
      </section>
    {:else if engine.status === 'setup'}
      <EngineSetup />
    {:else if engine.status === 'connecting'}
      <p role="status">
        {onDesktop ? 'Starting the GladTalker engine… This can take up to a minute the first time.' : 'Connecting to the GladTalker engine…'}
      </p>
    {:else}
      {#if needsSetup}<SetupPanel />{/if}
      {#if nav.step === 'cast'}<CastStep />{:else if nav.step === 'script'}<ScriptStep />{:else}<RollStep />{/if}
    {/if}
  </main>

  <footer class="app-footer">
    <p class="status">
      <span>Computer: {deviceLabel()}</span>
      {#if engine.models}<span> · Models: {engine.models.ready}/{engine.models.total} ready</span>{/if}
    </p>
    <button type="button" onclick={() => goTo(STEPS[index - 1].id)} disabled={index <= 0}>← Back</button>
    {#if index < STEPS.length - 1}
      <button type="button" class="primary" onclick={() => goTo(STEPS[index + 1].id)}>
        Next: {STEPS[index + 1].label} →
      </button>
    {/if}
  </footer>
</div>

<HelpDialog bind:open={helpOpen} version={engine.version} />

<!-- Persistent live regions (WCAG 4.1.3): status and progress are announced without moving focus. -->
<div class="visually-hidden" role="status" aria-live="polite" aria-atomic="true">{live.polite}</div>
<div class="visually-hidden" role="alert" aria-live="assertive" aria-atomic="true">{live.assertive}</div>

<style>
  .offline { border-left: 6px solid var(--error); }
  .notice { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; align-items: center; justify-content: space-between;
    margin-bottom: 1rem; border-left: 6px solid var(--accent); }
  .notice p { margin: 0; }
  .project { gap: 0.35rem; }
</style>
