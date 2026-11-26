// jsdom gaps used by the UI.
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/svelte';

if (typeof HTMLDialogElement !== 'undefined' && !HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function close() {
    this.open = false;
    this.dispatchEvent(new Event('close'));
  };
}
if (typeof HTMLMediaElement !== 'undefined') {
  HTMLMediaElement.prototype.play = () => Promise.resolve();
  HTMLMediaElement.prototype.pause = () => {};
}
URL.createObjectURL ??= () => 'blob:mock';
URL.revokeObjectURL ??= () => {};

afterEach(() => {
  cleanup();
  globalThis.localStorage?.clear(); // absent in files that run in plain Node (// @vitest-environment node)
});
