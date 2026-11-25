<script>
  // Labelled number input with −/+ buttons: a non-drag alternative for every numeric setting
  // (WCAG 2.5.7), large targets (2.5.8), and errors tied to the field (3.3.1, aria-describedby).
  let {
    id,
    label,
    value = $bindable(0),
    min = -Infinity,
    max = Infinity,
    step = 1,
    unit = '',
    hint = '',
    error = '',
    hideLabel = false,
    onchange = () => {},
  } = $props();

  const decimals = $derived(String(step).includes('.') ? String(step).split('.')[1].length : 0);

  function set(next) {
    const n = Number(next);
    if (!Number.isFinite(n)) return;
    const clamped = Math.min(max, Math.max(min, n));
    value = Number(clamped.toFixed(decimals));
    onchange(value);
  }

  const describedBy = $derived([hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(' ') || undefined);
</script>

<div class="number-field">
  <label for={id} class:visually-hidden={hideLabel}>{label}</label>
  <div class="controls">
    <button type="button" class="icon" aria-label="Decrease {label}" disabled={value <= min}
      onclick={() => set(value - step)}>−</button>
    <input {id} type="number" inputmode="decimal" {min} {max} {step} value={value}
      aria-describedby={describedBy} aria-invalid={error ? 'true' : undefined}
      onchange={(e) => set(e.currentTarget.value)} />
    {#if unit}<span class="unit" aria-hidden="true">{unit}</span>{/if}
    <button type="button" class="icon" aria-label="Increase {label}" disabled={value >= max}
      onclick={() => set(value + step)}>+</button>
  </div>
  {#if hint}<p class="hint" id="{id}-hint">{hint}</p>{/if}
  {#if error}<p class="error-text" id="{id}-error">{error}</p>{/if}
</div>

<style>
  .number-field { display: flex; flex-direction: column; gap: 0.25rem; }
  .controls { display: flex; align-items: center; gap: 0.3rem; }
  input { width: 6.5rem; text-align: right; }
  .unit { color: var(--muted); min-width: 1.5rem; }
  .hint { color: var(--muted); font-size: 0.9rem; margin: 0; }
</style>
