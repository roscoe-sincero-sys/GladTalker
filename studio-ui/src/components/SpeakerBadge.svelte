<script>
  // Color + shape + number for a cast member (never color alone, WCAG 1.4.1). Decorative by
  // default: the member's name and number are always given in text next to it.
  import { speakerStyle, SHAPE_PATHS } from '../lib/palette.js';

  let { index = 0, number = 1, size = 28, label = '' } = $props();
  const style = $derived(speakerStyle(index));
</script>

<span class="badge" style="--size:{size}px" role={label ? 'img' : undefined} aria-label={label || undefined}
  aria-hidden={label ? undefined : 'true'}>
  <svg viewBox="0 0 20 20" width={size} height={size} focusable="false">
    <path d={SHAPE_PATHS[style.shape]} fill={style.color} stroke="#ffffff" stroke-width="1" />
  </svg>
  <span class="num">{number}</span>
</span>

<style>
  .badge {
    position: relative;
    display: inline-grid;
    place-items: center;
    width: var(--size);
    height: var(--size);
    flex: none;
  }
  .badge svg { grid-area: 1 / 1; }
  .num {
    grid-area: 1 / 1;
    color: #ffffff;
    font-weight: 800;
    font-size: calc(var(--size) * 0.45);
    line-height: 1;
    padding-top: 1px;
  }
</style>
