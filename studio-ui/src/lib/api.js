// Client for the GladTalker Studio engine (studio_server.py). See docs/studio_api.md.

const TOKEN_KEY = 'gladtalker.token';
const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `Request failed (${status}).`);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** The engine's optional bearer token. A launcher can pass it as `#token=...` (fragments never
 * reach servers or logs); it is moved into sessionStorage and removed from the address bar. */
export function readEngineToken(win = globalThis.window) {
  if (!win) return null;
  try {
    const match = /(?:^#|&)token=([^&]+)/.exec(win.location.hash || '');
    if (match) {
      win.sessionStorage.setItem(TOKEN_KEY, decodeURIComponent(match[1]));
      const rest = win.location.hash.replace(/(?:^#|&)token=[^&]+/, '').replace(/^&/, '');
      win.history.replaceState(null, '', win.location.pathname + win.location.search + (rest ? `#${rest}` : ''));
    }
    return win.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function detailOf(body, status) {
  if (!body) return `Request failed (${status}).`;
  if (typeof body.detail === 'string') return body.detail;
  if (Array.isArray(body.detail)) {
    // FastAPI validation errors: [{loc, msg}] -> "script.0.text: Field required"
    return body.detail
      .map((d) => {
        const where = (d.loc || []).filter((p) => p !== 'body').join('.');
        return where ? `${where}: ${d.msg}` : d.msg;
      })
      .join(' ');
  }
  return `Request failed (${status}).`;
}

export function createApi({ base = '', fetchImpl = globalThis.fetch?.bind(globalThis), token = null,
  WebSocketImpl = globalThis.WebSocket, location = globalThis.location } = {}) {
  const headers = (extra = {}) => (token ? { ...extra, Authorization: `Bearer ${token}` } : extra);

  async function request(method, path, body, { asBlob = false, form = false } = {}) {
    const init = { method, headers: headers() };
    if (form) init.body = body;
    else if (body !== undefined) {
      init.headers = headers({ 'Content-Type': 'application/json' });
      init.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetchImpl(`${base}${path}`, init);
    } catch {
      throw new ApiError(0, 'Can’t reach the GladTalker engine. Make sure studio_server.py is running.');
    }
    if (!res.ok) {
      let payload = null;
      try {
        payload = await res.json();
      } catch {
        /* not JSON */
      }
      throw new ApiError(res.status, detailOf(payload, res.status));
    }
    return asBlob ? res.blob() : res.json();
  }

  /** URL for a file the UI shows in <img>/<video>. Without a token a plain URL works; with one,
   * the bytes are fetched with the Authorization header and exposed as a blob: URL. */
  async function mediaUrl(path) {
    if (!token) return `${base}${path}`;
    return URL.createObjectURL(await request('GET', path, undefined, { asBlob: true }));
  }

  function wsUrl(path) {
    const loc = location || { protocol: 'http:', host: '127.0.0.1:7860' };
    if (base) return base.replace(/^http/, 'ws') + path;
    return `${loc.protocol === 'https:' ? 'wss:' : 'ws:'}//${loc.host}${path}`;
  }

  /** Follow a job until it finishes: WebSocket (with a single-use ticket) first, polling as a
   * fallback. Calls onEvent(snapshot) for every update; resolves with the final snapshot. */
  function watchJob(jobId, onEvent, { pollMs = 1000 } = {}) {
    let closed = false;
    let socket = null;
    let timer = null;
    let resolveDone;
    const done = new Promise((r) => (resolveDone = r));
    const finish = (job) => {
      if (closed) return;
      closed = true;
      clearTimeout(timer);
      if (socket && socket.readyState <= 1) socket.close();
      resolveDone(job);
    };
    const deliver = (job) => {
      if (closed) return;
      onEvent?.(job);
      if (TERMINAL.has(job.state)) finish(job);
    };
    const poll = async () => {
      if (closed) return;
      try {
        deliver(await request('GET', `/jobs/${jobId}`));
      } catch {
        /* transient: keep polling */
      }
      if (!closed) timer = setTimeout(poll, pollMs);
    };
    (async () => {
      if (!WebSocketImpl) return poll();
      let gotEvent = false;
      try {
        const { url } = await request('POST', `/jobs/${jobId}/ws-ticket`);
        socket = new WebSocketImpl(wsUrl(url));
        socket.onmessage = (msg) => {
          gotEvent = true;
          try {
            deliver(JSON.parse(msg.data).job);
          } catch {
            /* ignore malformed */
          }
        };
        socket.onclose = () => {
          if (!closed) poll(); // dropped or refused: fall back to polling (also catches the end state)
        };
        socket.onerror = () => {
          if (!gotEvent && !closed && socket.readyState !== 1) socket.close();
        };
      } catch {
        poll();
      }
    })();
    return { done, close: () => finish(null) };
  }

  return {
    token,
    health: () => request('GET', '/health'),
    hardware: () => request('GET', '/hardware'),
    models: () => request('GET', '/models'),
    downloadModels: (force = false) => request('POST', '/models/download', { force }),
    voices: () => request('GET', '/voices'),
    upload(file) {
      const form = new FormData();
      form.append('file', file, file.name);
      return request('POST', '/uploads', form, { form: true });
    },
    uploadedImageUrl: (imagePath) => mediaUrl(`/uploads/${encodeURIComponent(basename(imagePath))}`),
    detectCast: (imagePath, minConfidence = 0.2) =>
      request('POST', '/cast/detect', { image_path: imagePath, min_confidence: minConfidence }),
    parseScript: (text, cast = []) => request('POST', '/script/parse', { text, cast }),
    lintLines: (lines) => request('POST', '/script/lint', { lines }),
    previewVoice: (text, semitones = 0, tempo = 1) =>
      request('POST', '/tts/preview', { text, semitones, tempo }, { asBlob: true }),
    previewAudio: (body) => request('POST', '/audio/preview', body),
    startRender: (body) => request('POST', '/render/start', body),
    job: (id) => request('GET', `/jobs/${id}`),
    cancelJob: (id) => request('POST', `/jobs/${id}/cancel`),
    fileUrl: (id, kind) => mediaUrl(`/jobs/${id}/files/${kind}`),
    plainFileUrl: (id, kind) => `${base}/jobs/${id}/files/${kind}`,
    watchJob,
  };
}

export function basename(path) {
  return String(path || '').split(/[\\/]/).pop();
}

export const api = createApi({ token: readEngineToken() });
