<script>
  // Cast → Script → Roll navigation. Native buttons in an ordered list; the current step is
  // marked with aria-current="step"; completed steps say so in text, not just with a check.
  import { STEPS, nav, goTo } from '../lib/app-state.svelte.js';

  let { complete = {} } = $props();
</script>

<nav aria-label="Steps" class="stepper">
  <ol>
    {#each STEPS as step, i (step.id)}
      <li>
        <button type="button" class="step" aria-current={nav.step === step.id ? 'step' : undefined}
          onclick={() => goTo(step.id)}>
          <span class="number" aria-hidden="true">{step.number}</span>
          <span>{step.label}</span>
          {#if complete[step.id]}<span class="done" aria-hidden="true">✓</span><span class="visually-hidden">(done)</span>{/if}
        </button>
        {#if i < STEPS.length - 1}<span class="arrow" aria-hidden="true">→</span>{/if}
      </li>
    {/each}
  </ol>
</nav>

<style>
  .stepper ol { list-style: none; display: flex; flex-wrap: wrap; align-items: center; gap: 0.35rem; margin: 0; padding: 0; }
  .stepper li { display: flex; align-items: center; gap: 0.35rem; }
  .step { gap: 0.5rem; border-radius: 999px; padding-inline: 0.9rem 1rem; }
  .number {
    display: inline-grid; place-items: center; width: 1.6rem; height: 1.6rem; border-radius: 50%;
    border: 2px solid currentColor; font-weight: 700; font-size: 0.9rem;
  }
  .step[aria-current='step'] { background: var(--accent); color: var(--accent-text); border-color: var(--accent); font-weight: 700; }
  .done { color: var(--success); font-weight: 800; }
  .step[aria-current='step'] .done { color: inherit; }
  .arrow { color: var(--muted); }
  @media (forced-colors: active) {
    .step[aria-current='step'] { forced-color-adjust: none; background: Highlight; color: HighlightText; }
  }
</style>
