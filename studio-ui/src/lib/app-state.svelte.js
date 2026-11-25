// App-wide state: engine status and step navigation.

import { tick } from 'svelte';
import { api } from './api.js';

export const STEPS = [
  { id: 'cast', number: 1, label: 'Cast', title: 'Cast: choose who speaks' },
  { id: 'script', number: 2, label: 'Script', title: 'Script: write what they say' },
  { id: 'roll', number: 3, label: 'Roll', title: 'Roll: render the video' },
];

export const engine = $state({
  status: 'connecting', // connecting | ready | offline
  error: '',
  version: '',
  hardware: null,
  models: null,
  voices: [],
  semitoneRange: [-6, 6],
});

export const nav = $state({ step: 'cast' });

export async function connectEngine() {
  engine.status = 'connecting';
  try {
    const [health, hardware, voices] = await Promise.all([api.health(), api.hardware(), api.voices()]);
    engine.version = health.version;
    engine.hardware = hardware;
    engine.models = hardware.models;
    engine.voices = voices.presets;
    engine.semitoneRange = voices.semitone_range;
    engine.status = 'ready';
    engine.error = '';
  } catch (err) {
    engine.status = 'offline';
    engine.error = err.message;
  }
}

export async function refreshModels() {
  try {
    engine.models = await api.models();
  } catch {
    /* keep the last known state */
  }
}

/** Go to a step and move focus to its heading (so screen reader users hear where they are), or
 * to a specific field when fixing a problem from the checklist. */
export async function goTo(step, fieldId = null) {
  nav.step = step;
  await tick();
  const target = (fieldId && document.getElementById(fieldId)) || document.getElementById(`${step}-heading`);
  if (target) {
    target.focus({ preventScroll: false });
    target.scrollIntoView?.({ block: 'nearest' });
  }
}

export const deviceLabel = () => engine.hardware?.device?.label || 'Detecting…';
