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

// Model status can be fetched by several callers at once (the busy poll, the end of a download,
// a reconnect), and answers can arrive out of order. Each request is numbered; an answer is shown
// only if nothing newer has been shown and the engine hasn't been reconnected since it was asked.
let modelsSession = 0;
let modelsRequested = 0;
let modelsShown = 0;

/** Connect to the engine. In the desktop app, the shell first starts it (or reports why it
 * can't); `restart` stops and starts it again. */
export async function connectEngine({ restart = false } = {}) {
  engine.status = 'connecting';
  modelsSession += 1; // answers to model-status requests sent before this connection are stale
  if (isDesktop() && !(await startDesktopEngine(restart ? 'restart_engine' : 'start_engine'))) return;
  const modelsId = ++modelsRequested;
  try {
    const [health, hardware, voices] = await Promise.all([api.health(), api.hardware(), api.voices()]);
    engine.version = health.version;
    engine.hardware = hardware;
    if (modelsId >= modelsShown) {
      modelsShown = modelsId;
      engine.models = hardware.models;
    }
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

/** Fetch the model status into `engine.models`, unless a newer answer is already shown, the engine
 * was reconnected meanwhile, or `isCurrent()` (e.g. "the panel that asked is still open") is false.
 * A failed request keeps the last known state. */
export async function refreshModels({ isCurrent = () => true } = {}) {
  const id = ++modelsRequested;
  const session = modelsSession;
  try {
    const status = await api.models();
    if (session !== modelsSession || id < modelsShown || !isCurrent()) return;
    modelsShown = id;
    engine.models = status;
  } catch {
    /* keep the last known state */
  }
}

/** While the last known model status has busy files (being replaced, or locked by another
 * program), check it again every `delayMs`, one request at a time, until none is busy or `stop()`
 * is called. A failed check keeps the last known state and is simply tried again. `poke()` starts
 * polling again after the status changes (it does nothing while a check is already pending). */
export function pollBusyModels({ delayMs = 3000 } = {}) {
  let timer = null;
  let inFlight = false;
  let stopped = false;
  const schedule = () => {
    if (stopped || timer || inFlight || !engine.models?.busy) return;
    timer = setTimeout(async () => {
      timer = null;
      inFlight = true;
      try {
        await refreshModels({ isCurrent: () => !stopped }); // a stopped poll's answer is dropped
      } finally {
        inFlight = false;
      }
      schedule();
    }, delayMs);
  };
  schedule();
  return {
    poke: schedule,
    stop() {
      stopped = true;
      clearTimeout(timer);
      timer = null;
    },
  };
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
