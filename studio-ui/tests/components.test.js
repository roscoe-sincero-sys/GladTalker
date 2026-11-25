// Component behaviour and accessibility in jsdom. (Color contrast needs real layout, so axe's
// color-contrast rule runs in the Playwright e2e scans; tests/palette.test.js checks the tokens.)
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/svelte';
import axe from 'axe-core';
import NumberField from '../src/components/NumberField.svelte';
import DialogueRow from '../src/components/DialogueRow.svelte';
import Stepper from '../src/components/Stepper.svelte';
import JobProgress from '../src/components/JobProgress.svelte';
import { nav } from '../src/lib/app-state.svelte.js';
import { reactive } from './state.svelte.js';

async function violations(container) {
  const res = await axe.run(container, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] },
    rules: { 'color-contrast': { enabled: false } },
  });
  return res.violations.map((v) => `${v.id}: ${v.help}`);
}

describe('NumberField', () => {
  it('has a label, −/+ buttons named after it, and clamps to its range', async () => {
    const onchange = vi.fn();
    const { container } = render(NumberField, { id: 'pitch', label: 'Pitch', value: 5.5, min: -6, max: 6, step: 0.5, unit: 'st', onchange });
    const input = screen.getByLabelText('Pitch');
    await fireEvent.click(screen.getByRole('button', { name: 'Increase Pitch' }));
    expect(onchange).toHaveBeenLastCalledWith(6);
    expect(input.value).toBe('6');
    // At the limit it is unavailable but keeps focus (aria-disabled, not disabled), and does nothing.
    const increase = screen.getByRole('button', { name: 'Increase Pitch' });
    expect(increase.getAttribute('aria-disabled')).toBe('true');
    expect(increase.disabled).toBe(false);
    increase.focus();
    const calls = onchange.mock.calls.length;
    await fireEvent.click(increase);
    await fireEvent.click(increase);
    expect(onchange.mock.calls.length, 'an unavailable button changes nothing').toBe(calls);
    expect(input.value).toBe('6');
    expect(document.activeElement).toBe(increase);
    input.value = '-20';
    await fireEvent.change(input);
    expect(onchange).toHaveBeenLastCalledWith(-6);
    const decrease = screen.getByRole('button', { name: 'Decrease Pitch' });
    expect(decrease.getAttribute('aria-disabled')).toBe('true');
    const atMin = onchange.mock.calls.length;
    await fireEvent.click(decrease);
    expect(onchange.mock.calls.length).toBe(atMin);
    expect(await violations(container)).toEqual([]);
  });

  it('leaves a value from outside its range alone when the unavailable button is pressed', async () => {
    // A project saved elsewhere with pitch 7 (the range is −6…6): Increase is unavailable and
    // must not "fix" the value to 6 behind the user's back; Decrease works.
    const onchange = vi.fn();
    render(NumberField, { id: 'pitch2', label: 'Pitch', value: 7, min: -6, max: 6, step: 0.5, onchange });
    const increase = screen.getByRole('button', { name: 'Increase Pitch' });
    expect(increase.getAttribute('aria-disabled')).toBe('true');
    await fireEvent.click(increase);
    expect(onchange).not.toHaveBeenCalled();
    await fireEvent.click(screen.getByRole('button', { name: 'Decrease Pitch' }));
    expect(onchange).toHaveBeenLastCalledWith(6);
  });

  it('links hints and errors to the input (aria-describedby / aria-invalid)', () => {
    render(NumberField, { id: 'x', label: 'X', value: 900, hint: 'Pixels from the left.', error: 'Keep the region inside the image.' });
    const input = screen.getByLabelText('X');
    expect(input.getAttribute('aria-describedby')).toBe('x-hint x-error');
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(document.getElementById('x-error').textContent).toMatch(/inside the image/);
  });
});

describe('DialogueRow', () => {
  const cast = [{ id: 'c1', name: 'Brain', style: 0 }, { id: 'c2', name: 'Pinky', style: 1 }];
  let props;
  beforeEach(() => {
    props = { line: reactive({ id: 'l2', castId: 'c1', text: "You're late." }), index: 1, total: 3, cast,
      warnings: [{ code: 'tts_contraction', message: 'Say “your”.', fix: 'your late.' }],
      onmove: vi.fn(), ondelete: vi.fn(), onplay: vi.fn(), onfix: vi.fn() };
  });

  function renderRow() {
    const list = document.createElement('ol');
    document.body.appendChild(list);
    return render(DialogueRow, { props, target: list });
  }

  it('reorders with buttons or Alt+Arrow keys, never dragging (WCAG 2.5.7)', async () => {
    renderRow();
    await fireEvent.click(screen.getByRole('button', { name: 'Move line 2 up' }));
    expect(props.onmove).toHaveBeenLastCalledWith(1, -1);
    await fireEvent.keyDown(screen.getByLabelText('What is said in line 2'), { key: 'ArrowDown', altKey: true });
    expect(props.onmove).toHaveBeenLastCalledWith(1, 1);
    await fireEvent.keyDown(screen.getByLabelText('What is said in line 2'), { key: 'ArrowDown' });
    expect(props.onmove).toHaveBeenCalledTimes(2); // plain arrows are left alone (WCAG 2.1.4)
  });

  it('gives the move buttons ids, so focus can be put back after a reorder', () => {
    renderRow();
    expect(screen.getByRole('button', { name: 'Move line 2 up' }).id).toBe('up-l2');
    expect(screen.getByRole('button', { name: 'Move line 2 down' }).id).toBe('down-l2');
  });

  it('offers the lint fix and labels every control', async () => {
    const { container } = renderRow();
    await fireEvent.click(screen.getByRole('button', { name: /Auto-Fix/ }));
    expect(props.onfix).toHaveBeenCalledWith(props.line, 'your late.');
    expect(screen.getByLabelText('Speaker for line 2').value).toBeTruthy();
    expect(screen.getByLabelText('What is said in line 2').getAttribute('aria-describedby')).toBe('warn-l2');
    expect(await violations(container)).toEqual([]);
  });

  it('marks a missing speaker and empty text as errors', () => {
    props.line = reactive({ id: 'l9', castId: null, text: ' ' });
    props.warnings = [];
    renderRow();
    expect(screen.getByLabelText('Speaker for line 2').getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByLabelText('What is said in line 2').getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByRole('button', { name: 'Play line 2' }).disabled).toBe(true);
  });
});

describe('Stepper', () => {
  it('marks the current step and says which steps are done', async () => {
    nav.step = 'script';
    const { container } = render(Stepper, { complete: { cast: true } });
    expect(screen.getByRole('navigation', { name: 'Steps' })).toBeTruthy();
    const current = container.querySelector('[aria-current="step"]');
    expect(current.textContent).toMatch(/Script/);
    expect(screen.getByRole('button', { name: /Cast.*\(done\)/ })).toBeTruthy();
    expect(await violations(container)).toEqual([]);
  });
});

describe('JobProgress', () => {
  it('shows a labelled progress bar, stages with text status, and Cancel', async () => {
    const oncancel = vi.fn();
    const job = { state: 'running', progress: 0.42, message: 'Rendering Brain: animating frame 120 of 591', eta_s: 610,
      elapsed_s: 300, stage: 'render:Brain', stages: ['audio', 'render:Brain', 'compose'], cancel_requested: false };
    const { container } = render(JobProgress, { job, id: 'r', label: 'Render progress', oncancel,
      stageLabels: { audio: 'Voices and timing' } });
    const bar = screen.getByLabelText('Render progress, percent complete');
    expect(bar.tagName).toBe('PROGRESS');
    expect(Number(bar.value)).toBeCloseTo(0.42);
    expect(screen.getByText(/About 10 min 10 s left/)).toBeTruthy();
    expect(container.querySelector('.stages').textContent).toMatch(/Voices and timing\s*\(done\).*Animate Brain\s*\(in progress\).*compose\s*\(waiting\)/s);
    await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(oncancel).toHaveBeenCalled();
    expect(await violations(container)).toEqual([]);
  });
});
