<script>
  // The visual stage: the scene with one focusable region per cast member.
  // Regions can be moved with the pointer (drag), the keyboard (Arrow: move, Alt+Arrow: resize,
  // Shift for 10 px; only while the region has focus, WCAG 2.1.4), or the X/Y/W/H fields and
  // Snap/Expand buttons in the inspector (WCAG 2.5.7: dragging is never required).
  import { moveRegion, resizeRegion, regionStyle } from '../lib/regions.js';
  import { speakerStyle } from '../lib/palette.js';
  import { announce } from '../lib/announce.svelte.js';
  import SpeakerBadge from './SpeakerBadge.svelte';

  let { image, imageUrl, cast, selectedId = null, onselect, onregion } = $props();

  let frame = $state();
  let drag = null;
  let announceTimer;

  const describe = (m, i) =>
    `Region ${i + 1}, ${m.name || 'unnamed'}: X ${m.region.x}, Y ${m.region.y}, width ${m.region.w}, height ${m.region.h}`;

  function settle(m, i) {
    clearTimeout(announceTimer);
    announceTimer = setTimeout(() => announce(describe(m, i)), 450);
  }

  function onkeydown(e, m, i) {
    const keys = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const d = keys[e.key];
    if (!d || e.ctrlKey || e.metaKey) return;
    e.preventDefault();
    const stepPx = e.shiftKey ? 10 : 1;
    const [dx, dy] = [d[0] * stepPx, d[1] * stepPx];
    onregion(m.id, e.altKey
      ? resizeRegion(m.region, dx, dy, image.width, image.height)
      : moveRegion(m.region, dx, dy, image.width, image.height));
    settle(m, i);
  }

  function pointerDown(e, m, mode) {
    if (e.button !== 0) return;
    onselect(m.id);
    const rect = frame.getBoundingClientRect();
    drag = { id: m.id, mode, startX: e.clientX, startY: e.clientY, start: { ...m.region },
      scaleX: image.width / rect.width, scaleY: image.height / rect.height, moved: false };
    e.currentTarget.setPointerCapture(e.pointerId);
    e.stopPropagation();
  }

  function pointerMove(e) {
    if (!drag) return;
    const dx = Math.round((e.clientX - drag.startX) * drag.scaleX);
    const dy = Math.round((e.clientY - drag.startY) * drag.scaleY);
    if (Math.abs(dx) + Math.abs(dy) > 0) drag.moved = true;
    onregion(drag.id, drag.mode === 'resize'
      ? resizeRegion(drag.start, dx, dy, image.width, image.height)
      : moveRegion(drag.start, dx, dy, image.width, image.height));
  }

  function pointerUp(i) {
    if (drag?.moved) {
      const m = cast.find((c) => c.id === drag.id);
      if (m) settle(m, i);
    }
    drag = null;
  }
</script>

<figure class="stage">
  <div class="frame" bind:this={frame}>
    <img src={imageUrl} alt="Scene: {image.name}, {image.width} by {image.height} pixels" draggable="false" />
    {#each cast as m, i (m.id)}
      {@const st = speakerStyle(m.style)}
      <button type="button" class="region" class:selected={m.id === selectedId}
        style="{regionStyle(m.region, image.width, image.height)};--c:{st.color}"
        aria-label={describe(m, i)} aria-pressed={m.id === selectedId}
        aria-keyshortcuts="ArrowLeft ArrowRight ArrowUp ArrowDown Alt+ArrowLeft Alt+ArrowRight Alt+ArrowUp Alt+ArrowDown"
        onclick={() => onselect(m.id)} onkeydown={(e) => onkeydown(e, m, i)}
        onpointerdown={(e) => pointerDown(e, m, 'move')} onpointermove={pointerMove} onpointerup={() => pointerUp(i)}
        onpointercancel={() => (drag = null)}>
        <span class="tag" aria-hidden="true">
          <SpeakerBadge index={m.style} number={i + 1} size={22} />
          <span class="tag-name">{m.name || 'Unnamed'}</span>
        </span>
        <span class="handle" aria-hidden="true" onpointerdown={(e) => pointerDown(e, m, 'resize')}
          onpointermove={pointerMove} onpointerup={() => pointerUp(i)}></span>
      </button>
    {/each}
  </div>
  <figcaption class="muted">
    {cast.length ? 'Select a region, then use the arrow keys to move it (Alt + arrows to resize), drag it, or edit X, Y, W, H.' : 'No cast yet: use Auto-Detect Cast or Add Region.'}
  </figcaption>
</figure>

<style>
  .stage { margin: 0; }
  .frame {
    position: relative;
    width: 100%;
    border-radius: 6px;
    overflow: hidden;
    background: var(--surface-2);
    touch-action: none;
    user-select: none;
  }
  img { display: block; width: 100%; height: auto; }
  .region {
    position: absolute;
    min-width: 0; min-height: 0; padding: 0;
    background: transparent;
    /* Speaker color plus a white and a black ring: visible on light and dark artwork (1.4.11). */
    border: 3px solid var(--c);
    border-radius: 4px;
    box-shadow: 0 0 0 2px #ffffff, 0 0 0 4px #000000;
    cursor: move;
    display: block;
  }
  .region:hover { background: rgb(255 255 255 / 0.08); }
  .region.selected { border-width: 5px; box-shadow: 0 0 0 2px #ffffff, 0 0 0 5px #000000; }
  .region:focus-visible { outline: 4px dashed var(--focus); outline-offset: 5px; }
  .tag {
    position: absolute; left: 0; top: 0;
    display: inline-flex; align-items: center; gap: 0.3rem;
    max-width: 100%;
    padding: 0.1rem 0.45rem 0.1rem 0.15rem;
    background: rgb(0 0 0 / 0.78); color: #ffffff;
    border-bottom-right-radius: 6px;
    font-size: 0.8rem; font-weight: 700;
    pointer-events: none;
  }
  .tag-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .handle {
    position: absolute; right: -2px; bottom: -2px;
    width: 18px; height: 18px;
    background: var(--c); border: 2px solid #ffffff; outline: 1px solid #000000;
    cursor: nwse-resize;
  }
  figcaption { margin-top: 0.5rem; font-size: 0.95rem; }
  @media (forced-colors: active) {
    .region { border-color: Highlight; box-shadow: none; }
    .tag { forced-color-adjust: none; background: Canvas; color: CanvasText; border: 1px solid CanvasText; }
  }
</style>
