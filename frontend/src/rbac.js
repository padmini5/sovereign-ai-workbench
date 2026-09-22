/* Phase 1 RBAC helpers — UI ONLY. Backend (rbac.py + auth_api.py) is the
   enforcement point. Never gate sensitive data on these alone. */

import { friendlyError } from './status.js';

export const API = (p) => p; // same-origin; vite proxies /api -> backend

export async function apiFetch(path, token, opts = {}) {
  const r = await fetch(API(path), {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}),
               ...(token ? { Authorization: 'Bearer ' + token } : {}) },
  });
  let body = null;
  try { body = await r.json(); } catch { /* non-JSON */ }
  if (!r.ok) {
    const err = new Error(friendlyError(r.status, body && body.detail));
    err.status = r.status; err.body = body;
    throw err;
  }
  return body;
}

export const login = (u, p) =>
  apiFetch('/api/v1/auth/login', null, { method: 'POST', body: JSON.stringify({ username: u, password: p }) });

/* Role -> navigation. Labels mirror backend ROLE_PERMISSIONS. */
const CHAT = { to: 'chat', label: 'AI Chat' };
const AGENTS = { to: 'agents', label: 'Agents' };
export const NAV_BY_ROLE = {
  ADMIN: [
    { to: 'dashboard', label: 'Dashboard' },
    CHAT,
    AGENTS,
    { to: 'users', label: 'User management' },
    { to: 'documents', label: 'Documents' },
    { to: 'reports', label: 'Reports' },
    { to: 'models', label: 'Models' },
    { to: 'audit', label: 'Audit log' },
    { to: 'system', label: 'System' },
  ],
  MANAGER: [
    { to: 'dashboard', label: 'Dashboard' },
    CHAT,
    AGENTS,
    { to: 'team', label: 'Team' },
    { to: 'documents', label: 'Documents' },
    { to: 'reports', label: 'Reports' },
    { to: 'models', label: 'Models' },
  ],
  OPERATOR: [
    { to: 'dashboard', label: 'Dashboard' },
    CHAT,
    AGENTS,
    { to: 'documents', label: 'Documents' },
    { to: 'reports', label: 'Reports' },
  ],
  REVIEWER: [
    { to: 'dashboard', label: 'Dashboard' },
    CHAT,
    { to: 'reviews', label: 'Review queue' },
    { to: 'documents', label: 'Documents' },
    { to: 'audit', label: 'Audit log' },
  ],
  USER: [
    { to: 'dashboard', label: 'Dashboard' },
    CHAT,
    { to: 'documents', label: 'Documents' },
  ],
};

export const navFor = (role) => NAV_BY_ROLE[role] || NAV_BY_ROLE.USER;
export const hasPerm = (session, perm) => !!session?.permissions?.includes(perm);
