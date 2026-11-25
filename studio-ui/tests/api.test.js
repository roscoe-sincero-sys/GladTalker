import { describe, it, expect, vi } from 'vitest';
import { createApi, readEngineToken, ApiError, basename, revokeMedia, isJobId } from '../src/lib/api.js';

const json = (status, body) => ({ ok: status < 400, status, json: async () => body, blob: async () => new Blob(['x']) });

describe('requests', () => {
  it('sends JSON with the bearer token when one is configured', async () => {
    const fetchImpl = vi.fn(async () => json(200, { ok: true }));
    const api = createApi({ fetchImpl, token: 's3cret' });
    await api.parseScript('A: hi', ['A']);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe('/script/parse');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer s3cret' });
    expect(JSON.parse(init.body)).toEqual({ text: 'A: hi', cast: ['A'] });
  });

  it('turns engine errors into readable messages', async () => {
    const api = createApi({ fetchImpl: async () => json(422, { detail: "Brain's region extends outside the image." }) });
    await expect(api.models()).rejects.toMatchObject({ name: 'ApiError', status: 422, message: "Brain's region extends outside the image." });

    const validation = createApi({ fetchImpl: async () => json(422, { detail: [{ loc: ['body', 'script', 0, 'text'], msg: 'Field required' }] }) });
    await expect(validation.models()).rejects.toThrow('script.0.text: Field required');
  });

  it('explains when the engine is not running', async () => {
    const api = createApi({ fetchImpl: async () => { throw new TypeError('Failed to fetch'); } });
    const err = await api.health().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toMatch(/studio_server\.py is running/);
  });

  it('uploads as multipart form data without a JSON content type', async () => {
    const fetchImpl = vi.fn(async () => json(200, { image_path: 'p', width: 1, height: 1 }));
    await createApi({ fetchImpl }).upload(new File(['x'], 'scene.png', { type: 'image/png' }));
    const [, init] = fetchImpl.mock.calls[0];
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.headers['Content-Type']).toBeUndefined();
  });

  it('releases blob: media URLs and leaves plain URLs alone', () => {
    const revoke = vi.spyOn(URL, 'revokeObjectURL');
    revokeMedia('/jobs/j/files/video');
    revokeMedia('');
    revokeMedia('blob:http://x/123');
    expect(revoke.mock.calls).toEqual([['blob:http://x/123']]);
    revoke.mockRestore();
  });

  it('builds media URLs from upload paths on any OS', async () => {
    const api = createApi({ fetchImpl: async () => json(200, {}) });
    expect(basename('C:\\ws\\uploads\\abc.png')).toBe('abc.png');
    expect(await api.uploadedImageUrl('/ws/uploads/abc.png')).toBe('/uploads/abc.png');
  });
});

describe('job ids', () => {
  it('never puts anything but an engine-made job id into a request URL', async () => {
    const fetchImpl = vi.fn(async () => json(200, {}));
    const api = createApi({ fetchImpl, token: 's3cret', WebSocketImpl: FakeSocket });
    for (const bad of ['../hardware', '../../models/download', 'abc', '0123456789AB', '0123456789abc', '', null, 7]) {
      await expect(api.job(bad)).rejects.toMatchObject({ status: 400 });
      await expect(api.cancelJob(bad)).rejects.toMatchObject({ status: 400 });
      await expect(api.fileUrl(bad, 'video')).rejects.toMatchObject({ status: 400 });
      expect(() => api.plainFileUrl(bad, 'video')).toThrow(ApiError);
      await expect(api.watchJob(bad, () => {}).done).rejects.toMatchObject({ status: 400 });
    }
    await expect(api.fileUrl('0123456789ab', '../../uploads')).rejects.toMatchObject({ status: 400 });
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(isJobId('0123456789ab')).toBe(true);
    expect(api.plainFileUrl('0123456789ab', 'srt')).toBe('/jobs/0123456789ab/files/srt');
  });
});

describe('jobWasRunning', () => {
  it('reports whether the job was still queued or running, and never throws', async () => {
    const states = { '00000000000a': 'running', '00000000000b': 'queued', '00000000000c': 'succeeded' };
    const fetchImpl = vi.fn(async (url) => {
      const id = url.split('/').pop();
      return states[id] ? json(200, { id, state: states[id] }) : json(404, { detail: 'gone' });
    });
    const api = createApi({ fetchImpl });
    expect(await api.jobWasRunning('00000000000a')).toBe(true);
    expect(await api.jobWasRunning('00000000000b')).toBe(true);
    expect(await api.jobWasRunning('00000000000c')).toBe(false);
    expect(await api.jobWasRunning('00000000000d')).toBe(false); // 404
    expect(await api.jobWasRunning('../x')).toBe(false); // not even requested
    expect(fetchImpl).toHaveBeenCalledTimes(4);
  });
});

describe('engine token from the URL fragment', () => {
  it('moves #token into sessionStorage and removes it from the address bar', () => {
    const replaceState = vi.fn();
    const win = { location: { hash: '#token=abc%20123&x=1', pathname: '/', search: '' },
      sessionStorage: { store: {}, setItem(k, v) { this.store[k] = v; }, getItem(k) { return this.store[k] ?? null; } },
      history: { replaceState } };
    expect(readEngineToken(win)).toBe('abc 123');
    expect(replaceState).toHaveBeenCalledWith(null, '', '/#x=1');
  });
});

class FakeSocket {
  static last = null;
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    FakeSocket.last = this;
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
  emit(job) {
    this.readyState = 1;
    this.onmessage?.({ data: JSON.stringify({ type: 'progress', job }) });
  }
}

describe('watchJob', () => {
  it('streams events over a ticketed WebSocket until the job ends', async () => {
    const fetchImpl = vi.fn(async (url) => json(200, { url: '/ws/jobs/000000000001?ticket=T', ticket: 'T' }));
    const api = createApi({ fetchImpl, WebSocketImpl: FakeSocket, location: { protocol: 'http:', host: '127.0.0.1:7860' } });
    const seen = [];
    const watch = api.watchJob('000000000001', (j) => seen.push(j.state));
    await vi.waitFor(() => expect(FakeSocket.last?.url).toBe('ws://127.0.0.1:7860/ws/jobs/000000000001?ticket=T'));
    FakeSocket.last.emit({ id: '000000000001', state: 'running', progress: 0.5 });
    FakeSocket.last.emit({ id: '000000000001', state: 'succeeded', progress: 1 });
    expect((await watch.done).state).toBe('succeeded');
    expect(seen).toEqual(['running', 'succeeded']);
    expect(fetchImpl.mock.calls[0][0]).toBe('/jobs/000000000001/ws-ticket');
  });

  it('falls back to polling when the WebSocket is refused', async () => {
    const states = ['running', 'succeeded'];
    const fetchImpl = vi.fn(async (url) => (url.endsWith('ws-ticket')
      ? json(200, { url: '/ws/jobs/000000000002?ticket=T' })
      : json(200, { id: '000000000002', state: states.shift() ?? 'succeeded' })));
    const api = createApi({ fetchImpl, WebSocketImpl: FakeSocket, location: { protocol: 'http:', host: 'h' } });
    const watch = api.watchJob('000000000002', () => {}, { pollMs: 5 });
    await vi.waitFor(() => expect(FakeSocket.last?.url).toContain('/ws/jobs/000000000002'));
    FakeSocket.last.close(); // refused before any event
    expect((await watch.done).state).toBe('succeeded');
  });

  it('stops with an error when the job is gone (engine restarted or job evicted)', async () => {
    const fetchImpl = vi.fn(async () => json(404, { detail: "No job with id '000000000003'." }));
    const api = createApi({ fetchImpl, WebSocketImpl: null });
    const watch = api.watchJob('000000000003', () => {}, { pollMs: 1 });
    await expect(watch.done).rejects.toMatchObject({ name: 'ApiError', status: 404, message: "No job with id '000000000003'." });
    const calls = fetchImpl.mock.calls.length;
    await new Promise((r) => setTimeout(r, 20));
    expect(fetchImpl.mock.calls.length).toBe(calls); // and stops polling
  });

  it('also stops when the ticket request is refused (wrong token)', async () => {
    const fetchImpl = vi.fn(async () => json(401, { detail: 'Missing or invalid Studio token.' }));
    const api = createApi({ fetchImpl, WebSocketImpl: FakeSocket, location: { protocol: 'http:', host: 'h' } });
    await expect(api.watchJob('000000000004', () => {}).done).rejects.toMatchObject({ status: 401 });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('backs off while the engine is unreachable, then carries on', async () => {
    vi.useFakeTimers();
    try {
      let n = 0;
      const fetchImpl = vi.fn(async () => {
        n++;
        if (n <= 3) throw new TypeError('Failed to fetch');
        return json(200, { id: '000000000005', state: 'succeeded' });
      });
      const api = createApi({ fetchImpl, WebSocketImpl: null });
      const watch = api.watchJob('000000000005', () => {}, { pollMs: 100 });
      await vi.advanceTimersByTimeAsync(0);
      expect(n).toBe(1);
      await vi.advanceTimersByTimeAsync(199);
      expect(n).toBe(1); // waits 200 ms after the first failure, not 100
      await vi.advanceTimersByTimeAsync(1);
      expect(n).toBe(2);
      await vi.advanceTimersByTimeAsync(400); // then 400 ms
      expect(n).toBe(3);
      await vi.advanceTimersByTimeAsync(800); // then 800 ms
      expect((await watch.done).state).toBe('succeeded');
    } finally {
      vi.useRealTimers();
    }
  });

  it('close() resolves with null and stops all traffic', async () => {
    const fetchImpl = vi.fn(async () => json(200, { id: '000000000006', state: 'running' }));
    const api = createApi({ fetchImpl, WebSocketImpl: null });
    const watch = api.watchJob('000000000006', () => {}, { pollMs: 1 });
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    watch.close();
    expect(await watch.done).toBeNull();
    const calls = fetchImpl.mock.calls.length;
    await new Promise((r) => setTimeout(r, 20));
    expect(fetchImpl.mock.calls.length).toBe(calls);
  });
});
