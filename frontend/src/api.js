/* Workbench API client.
   - /api/v1/* uses the stored v1 access token; a 401 triggers ONE silent
     refresh attempt (POST /api/v1/auth/refresh) and a single retry; if the
     refresh fails the session is cleared and the caller sees an honest
     "sign in again" error.
   - /api/* (legacy) uses the legacy token only — never a v1 token (legacy
     endpoints read claims the v1 token does not carry).
   - Honest, stack-free messages for 401/403/404/413/415/422/429/500/502/503.
   None of this replaces server enforcement: every endpoint re-checks auth,
   permissions, and ownership. UI presentation only. */
import {
  api, getAccess, getRefresh, getLegacyToken, refreshTokens,
} from './session.js';
import { friendlyError } from './status.js';

/* Friendly per-status text lives in status.js (friendlyError) so every
   caller shares one mapping. normalizeDetail is kept for callers that
   render structured (array/object) details. */

export function normalizeDetail(detail) {
  if (!detail) return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((x) => (x && x.msg ? x.msg : String(x))).join('; ');
  }
  return String(detail);
}

export function statusMessage(status, detail) {
  // Backend details are scrubbed of implementation names before display.
  return friendlyError(status, detail);
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(statusMessage(status, detail));
    this.name = 'ApiError';
    this.status = status;
    this.detail = normalizeDetail(detail);
  }
}

async function readJson(r) {
  try { return await r.json(); } catch { return null; }
}

/* Core request. opts:
     method   HTTP method (default GET)
     body     object (JSON-encoded) | FormData | string
     token    'v1' (default: stored access + refresh-once on 401)
            | 'legacy' | 'none' | an explicit bearer string
     raw      true -> return the Response as-is (SSE / blobs)
     headers  extra headers */
export async function request(path, opts = {}) {
  const method = opts.method || 'GET';
  const kind = opts.token === undefined ? 'v1' : opts.token;
  let body = opts.body;
  const headers = { ...(opts.headers || {}) };
  const isForm = typeof FormData !== 'undefined' && body instanceof FormData;
  const bearerFor = (k) => (k === 'v1' ? getAccess()
    : k === 'legacy' ? getLegacyToken()
    : k === 'none' ? '' : k);
  const send = (tok) => {
    const h = { ...headers };
    if (tok) h.Authorization = 'Bearer ' + tok;
    return fetch(api(path), { method, headers: h, body: body ?? undefined });
  };
  if (body !== undefined && body !== null && !isForm) {
    if (typeof body !== 'string') body = JSON.stringify(body);
    if (!headers['Content-Type']) headers['Content-Type'] = 'application/json';
  }
  let r;
  try {
    r = await send(bearerFor(kind));
  } catch {
    throw new ApiError(0, '');
  }
  if (r.status === 401 && kind === 'v1' && getRefresh()) {
    const ok = await refreshTokens();
    if (ok) {
      try { r = await send(getAccess()); } catch { throw new ApiError(0, ''); }
    }
  }
  if (opts.raw) {
    if (!r.ok) throw new ApiError(r.status, (await readJson(r))?.detail);
    return r;
  }
  if (!r.ok) throw new ApiError(r.status, (await readJson(r))?.detail);
  if (r.status === 204) return null;
  return readJson(r);
}

export const v1 = (path, opts = {}) => request(path, opts);
export const legacy = (path, opts = {}) => request(path, { ...opts, token: 'legacy' });

/* Multipart upload WITH progress (XHR — fetch has no upload progress). */
export function upload(path, file, onPct, fieldName = 'f') {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('POST', api(path));
    const tok = getAccess();
    if (tok) x.setRequestHeader('Authorization', 'Bearer ' + tok);
    x.upload.onprogress = (e) => {
      if (e.lengthComputable && onPct) onPct(Math.round((e.loaded / e.total) * 100));
    };
    x.onload = () => {
      let b = null;
      try { b = JSON.parse(x.responseText); } catch { /* non-JSON */ }
      if (x.status >= 200 && x.status < 300) resolve(b);
      else reject(new ApiError(x.status, b && b.detail));
    };
    x.onerror = () => reject(new ApiError(0, ''));
    const fd = new FormData();
    fd.append(fieldName, file, file.name);
    x.send(fd);
  });
}

/* Raw blob helper (audio / images / downloads with an Authorization header). */
export async function blob(path, opts = {}) {
  const r = await request(path, { ...opts, raw: true });
  return r.blob();
}
