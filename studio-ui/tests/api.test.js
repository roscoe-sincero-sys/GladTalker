import { describe, it, expect, vi } from 'vitest';
import { createApi, readEngineToken, ApiError, basename } from '../src/lib/api.js';

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

  it('builds media URLs from upload paths on any OS', async () => {
    const api = createApi({ fetchImpl: async () => json(200, {}) });
    expect(basename('C:\\ws\\uploads\\abc.png')).toBe('abc.png');
    expect(await api.uploadedImageUrl('/ws/uploads/abc.png')).toBe('/uploads/abc.png');
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
    const fetchImpl = vi.fn(async (url) => json(200, { url: '/ws/jobs/j1?ticket=T', ticket: 'T' }));
    const api = createApi({ fetchImpl, WebSocketImpl: FakeSocket, location: { protocol: 'http:', host: '127.0.0.1:7860' } });
    const seen = [];
    const watch = api.watchJob('j1', (j) => seen.push(j.state));
    await vi.waitFor(() => expect(FakeSocket.last?.url).toBe('ws://127.0.0.1:7860/ws/jobs/j1?ticket=T'));
    FakeSocket.last.emit({ id: 'j1', state: 'running', progress: 0.5 });
    FakeSocket.last.emit({ id: 'j1', state: 'succeeded', progress: 1 });
    expect((await watch.done).state).toBe('succeeded');
    expect(seen).toEqual(['running', 'succeeded']);
    expect(fetchImpl.mock.calls[0][0]).toBe('/jobs/j1/ws-ticket');
  });

  it('falls back to polling when the WebSocket is refused', async () => {
    const states = ['running', 'succeeded'];
    const fetchImpl = vi.fn(async (url) => (url.endsWith('ws-ticket')
      ? json(200, { url: '/ws/jobs/j2?ticket=T' })
      : json(200, { id: 'j2', state: states.shift() ?? 'succeeded' })));
    const api = createApi({ fetchImpl, WebSocketImpl: FakeSocket, location: { protocol: 'http:', host: 'h' } });
    const watch = api.watchJob('j2', () => {}, { pollMs: 5 });
    await vi.waitFor(() => expect(FakeSocket.last?.url).toContain('/ws/jobs/j2'));
    FakeSocket.last.close(); // refused before any event
    expect((await watch.done).state).toBe('succeeded');
  });
});
