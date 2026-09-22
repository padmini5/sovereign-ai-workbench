/* Session + token storage for the workbench shell (UI convenience only).
   The backend validates every request: stateless JWTs, role->permissions
   resolved server-side per request, active-status re-checked per request.
   Two token sets share one JWT secret:
     - v1 access/refresh  -> /api/v1/*   (claims: sub, username, role, typ)
     - legacy token       -> /api/*      (claims: user_id, role, tier, unit)
   Login is v1-first; the legacy token is fetched opportunistically with the
   same credentials so the legacy pipeline demo keeps working, and is simply
   absent (card hidden) when the legacy store does not know the account. */

const K_ACC = 'sov_access_token';
const K_REF = 'sov_refresh_token';
const K_LEG = 'sov_legacy_token';
const K_SES = 'sov_session';

const subs = new Set();
export function subscribe(fn) { subs.add(fn); return () => subs.delete(fn); }
function emit() { subs.forEach((f) => { try { f(); } catch { /* listener error is not fatal */ } }); }

export const getAccess = () => localStorage.getItem(K_ACC) || '';
export const getRefresh = () => localStorage.getItem(K_REF) || '';
export const getLegacyToken = () => localStorage.getItem(K_LEG) || '';

export function setTokens(access, refresh) {
  if (access) localStorage.setItem(K_ACC, access);
  if (refresh) localStorage.setItem(K_REF, refresh);
  emit();
}
export function setLegacyToken(tok) {
  if (tok) localStorage.setItem(K_LEG, tok); else localStorage.removeItem(K_LEG);
}
export function clearTokens() {
  [K_ACC, K_REF, K_LEG, K_SES].forEach((k) => localStorage.removeItem(k));
  emit();
}

export function getSession() {
  try { return JSON.parse(localStorage.getItem(K_SES) || 'null'); } catch { return null; }
}
function setSession(s) {
  if (s) localStorage.setItem(K_SES, JSON.stringify(s)); else localStorage.removeItem(K_SES);
  emit();
}
export function setUser(user) { const s = getSession() || {}; s.user = user; setSession(s); }

/* Deployment-aware endpoints (no hardcoded hosts; same-origin default). */
const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
export const api = (p) => API_BASE + p;
export const wsUrl = (p) => {
  const w = (import.meta.env.VITE_WS_BASE_URL || '').replace(/\/$/, '');
  if (w) return w + p;
  if (API_BASE) return API_BASE.replace(/^http/, 'ws') + p;
  return ((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host) + p;
};

function detailOf(j) {
  const d = j && j.detail;
  if (!d) return '';
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((x) => (x && x.msg) || String(x)).join('; ');
  return JSON.stringify(d);
}

async function post(path, body) {
  const r = await fetch(api(path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => null);
  if (!r.ok) {
    const { friendlyError } = await import('./status.js');
    const e = new Error(friendlyError(r.status, j && j.detail));
    e.status = r.status; throw e;
  }
  return j;
}

/* v1 login (primary) + opportunistic legacy login (powers the preserved
   legacy pipeline demo). Legacy failure is silent — never blocks sign-in. */
export async function login(username, password) {
  const j = await post('/api/v1/auth/login', { username, password });
  setTokens(j.access_token, j.refresh_token);
  setSession({ user: j.user });
  let legacy = '';
  try {
    const lg = await post('/api/login', { username, password });
    legacy = (lg && lg.token) || '';
  } catch { legacy = ''; }
  setLegacyToken(legacy);
  return j.user;
}

export async function refreshTokens() {
  const rt = localStorage.getItem(K_REF) || '';
  if (!rt) return false;
  try {
    const j = await post('/api/v1/auth/refresh', { refresh_token: rt });
    setTokens(j.access_token, j.refresh_token);
    if (j.user) setSession({ ...(getSession() || {}), user: j.user });
    return true;
  } catch {
    clearTokens(); // refresh failed -> honest sign-in-again state
    return false;
  }
}

export async function logout() {
  try {
    await fetch(api('/api/v1/auth/logout'), {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + getAccess() },
    });
  } catch { /* stateless JWT: discard locally either way */ }
  clearTokens();
}
