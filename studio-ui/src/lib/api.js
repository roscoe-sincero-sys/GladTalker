// Client for the GladTalker Studio engine (studio_server.py). See docs/studio_api.md.

const TOKEN_KEY = 'gladtalker.token';
const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);
// Answers that won't change by asking again: the token is wrong, or the job is gone (the engine
// restarted or dropped it from its history).
const PERMANENT = new Set([401, 403, 404]);
const MAX_POLL_MS = 10_000;
// Job ids as the engine makes them (src/studio/jobs.py: 12 hex characters), and the kinds of
// file a job can serve. Anything else (say, from an edited project file) never reaches a URL.
export const JOB_ID_RE = /^[0-9a-f]{12}$/;
const FILE_KINDS = new Set(['video', 'srt', 'vtt', 'audio']);

export const isJobId = (id) => typeof id === 'string' && JOB_ID_RE.test(id);

function jobPath(id, rest = '') {
  if (!isJobId(id)) throw new ApiError(400, 'That isn’t a GladTalker job id.');
  return `/jobs/${id}${rest}`;
}

function filePath(id, kind) {
  if (!FILE_KINDS.has(kind)) throw new ApiError(400, `Unknown file kind “${kind}”.`);
  return jobPath(id, `/files/${kind}`);
}

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
   * fallback. Calls onEvent(snapshot) for every update.
   *
   * `done` resolves with the final snapshot, or with null after `close()`. It rejects with an
   * ApiError when the job can't be followed any more (401/403/404: wrong token, or the engine
   * restarted and the job is gone). Other failures retry with growing pauses (up to 10 s).
   * Call `close()` when the caller goes away, so nothing keeps polling in the background. */
  function watchJob(jobId, onEvent, { pollMs = 1000 } = {}) {
    if (!isJobId(jobId)) {
      return { done: Promise.reject(new ApiError(400, 'That isn’t a GladTalker job id.')), close() {} };
    }
    let closed = false;
    let socket = null;
    let timer = null;
    let delay = pollMs;
    let resolveDone;
    let rejectDone;
    const done = new Promise((resolve, reject) => {
      resolveDone = resolve;
      rejectDone = reject;
    });
    const stop = () => {
      closed = true;
      clearTimeout(timer);
      if (socket && socket.readyState <= 1) socket.close();
    };
    const finish = (job) => {
      if (closed) return;
      stop();
      resolveDone(job);
    };
    const fail = (err) => {
      if (closed) return;
      stop();
      rejectDone(err);
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
        delay = pollMs;
      } catch (err) {
        if (PERMANENT.has(err.status)) return fail(err);
        delay = Math.min(delay * 2, MAX_POLL_MS); // engine busy or restarting: back off
      }
      if (!closed) timer = setTimeout(poll, delay);
    };
    (async () => {
      if (!WebSocketImpl) return poll();
      let gotEvent = false;
      try {
        const { url } = await request('POST', `/jobs/${jobId}/ws-ticket`);
        if (closed) return; // closed while the ticket was on its way
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
      } catch (err) {
        if (PERMANENT.has(err.status)) fail(err);
        else poll();
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
    job: async (id) => request('GET', jobPath(id)),
    cancelJob: async (id) => request('POST', jobPath(id, '/cancel')),
    fileUrl: async (id, kind) => mediaUrl(filePath(id, kind)),
    plainFileUrl: (id, kind) => `${base}${filePath(id, kind)}`,
    watchJob,
  };
}

/** Release a URL made by mediaUrl() (a no-op for plain URLs). */
export function revokeMedia(url) {
  if (typeof url === 'string' && url.startsWith('blob:')) URL.revokeObjectURL(url);
}

export function basename(path) {
  return String(path || '').split(/[\\/]/).pop();
}

export const api = createApi({ token: readEngineToken() });
