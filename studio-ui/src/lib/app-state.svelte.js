// App-wide state: engine status and step navigation.

import { tick } from 'svelte';
import { api, configureApi } from './api.js';
import { isDesktop, desktopCall } from './desktop.js';

export const STEPS = [
  { id: 'cast', number: 1, label: 'Cast', title: 'Cast: choose who speaks' },
  { id: 'script', number: 2, label: 'Script', title: 'Script: write what they say' },
  { id: 'roll', number: 3, label: 'Roll', title: 'Roll: render the video' },
];

export const engine = $state({
  status: 'connecting', // connecting | ready | offline | setup (desktop: no usable engine yet)
  desktop: null, // desktop app only: the shell's engine status ({ state, message, folder, log, ... })
  notice: '', // something worth knowing that doesn't stop anything (shown until dismissed)
  error: '',
  version: '',
  hardware: null,
  models: null,
  voices: [],
  semitoneRange: [-6, 6],
});

export const nav = $state({ step: 'cast' });

/** Connect to the engine. In the desktop app, the shell first starts it (or reports why it
 * can't); `restart` stops and starts it again. */
export async function connectEngine({ restart = false } = {}) {
  engine.status = 'connecting';
  if (isDesktop() && !(await startDesktopEngine(restart ? 'restart_engine' : 'start_engine'))) return;
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

async function startDesktopEngine(command, args) {
  let status;
  try {
    status = await desktopCall(command, args);
  } catch (err) {
    status = { state: 'failed', message: err.message, log: '' };
  }
  if (status === null) return false; // a dialog was cancelled: nothing changed
  engine.desktop = status;
  if (status.state !== 'ready') {
    engine.status = 'setup';
    return false;
  }
  configureApi({ base: status.base, token: status.token });
  if (status.note) engine.notice = status.note;
  return true;
}

/** Desktop app: let the user pick their GladTalker folder, then start the engine from it. */
export async function chooseEngineFolder() {
  const previous = engine.status;
  engine.status = 'connecting';
  const started = await startDesktopEngine('choose_engine_folder');
  if (started) return connectEngine();
  if (engine.status === 'connecting') engine.status = previous; // cancelled
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

export const deviceLabel = () => engine.hardware?.device?.label
  || (engine.status === 'connecting' ? 'Detecting…' : 'the engine isn’t running');
