import { describe, it, expect, vi, beforeEach } from 'vitest';
import { playBlob, stopPlayback } from '../src/lib/player.js';

class FakeAudio {
  static all = [];
  static failNext = false;
  constructor(src) {
    this.src = src;
    this.paused = true;
    FakeAudio.all.push(this);
  }
  async play() {
    if (FakeAudio.failNext) {
      FakeAudio.failNext = false;
      throw new Error('NotAllowedError');
    }
    this.paused = false;
  }
  pause() {
    this.paused = true;
  }
}

describe('player', () => {
  let n;
  beforeEach(() => {
    FakeAudio.all = [];
    n = 0;
    vi.spyOn(URL, 'createObjectURL').mockImplementation(() => `blob:clip${++n}`);
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
  });

  it('plays one clip at a time: a new clip stops the old one', async () => {
    const firstEnded = vi.fn();
    await playBlob(new Blob(['a']), firstEnded, FakeAudio);
    await playBlob(new Blob(['b']), () => {}, FakeAudio);
    expect(FakeAudio.all[0].paused).toBe(true);
    expect(FakeAudio.all[1].paused).toBe(false);
    expect(firstEnded).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:clip1');
    stopPlayback();
  });

  it('releases the clip however it ends: finished, errored, or failed to start', async () => {
    const ended = vi.fn();
    await playBlob(new Blob(['a']), ended, FakeAudio);
    FakeAudio.all[0].onended();
    FakeAudio.all[0].onerror(); // a late error after the end doesn't run onend twice
    expect(ended).toHaveBeenCalledTimes(1);

    await playBlob(new Blob(['b']), () => {}, FakeAudio);
    FakeAudio.all[1].onerror();

    FakeAudio.failNext = true;
    await expect(playBlob(new Blob(['c']), () => {}, FakeAudio)).rejects.toThrow('NotAllowedError');
    expect(URL.revokeObjectURL.mock.calls.map((c) => c[0])).toEqual(['blob:clip1', 'blob:clip2', 'blob:clip3']);
  });
});
