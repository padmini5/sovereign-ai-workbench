import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';

/* Shared workbench UI: toasts, spinners, skeletons, cards, honest error
   notes, status badges, and a UI-only permission gate. None of these
   enforce anything — the backend does. They only render truthfully. */

/* ---------------- toasts ---------------- */
const ToastCtx = createContext(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const idRef = useRef(0);
  const toast = useCallback((text, kind = 'info') => {
    const id = ++idRef.current;
    setItems((xs) => [...xs, { id, text, kind }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 5000);
  }, []);
  return (
    <ToastCtx.Provider value={toast}>
      {children}
      <div className="toast-host" role="status" aria-live="polite">
        {items.map((x) => (
          <div key={x.id} className={'toast toast-' + x.kind}>{x.text}</div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ---------------- basics ---------------- */
export function Spinner({ label = 'Loading…' }) {
  return (
    <span className="spinner" role="status" aria-label={label}>
      <span className="spinner-dot" /> {label}
    </span>
  );
}

export function Skeleton({ rows = 3 }) {
  return (
    <div className="skeleton-wrap" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => <div key={i} className="skeleton-row" />)}
    </div>
  );
}

export function Card({ title, actions, children, className = '' }) {
  return (
    <section className={'card ' + className}>
      {(title || actions) && (
        <header className="card-head">
          {title ? <h3>{title}</h3> : <span />}
          {actions ? <div className="card-actions">{actions}</div> : null}
        </header>
      )}
      {children}
    </section>
  );
}

export function Empty({ title, hint }) {
  return (
    <div className="empty">
      <div className="empty-icon">◌</div>
      <p className="empty-title">{title}</p>
      {hint && <p className="mut">{hint}</p>}
    </div>
  );
}

/* Honest error line: status-aware, never a stack trace. */
export function ErrorNote({ error, onRetry }) {
  if (!error) return null;
  const text = typeof error === 'string' ? error : error.message || String(error);
  return (
    <p className="err-note" role="alert">
      <span className="err-icon">⚠</span> {text}
      {onRetry && <button className="btn btn-ghost btn-mini" onClick={onRetry}>Retry</button>}
    </p>
  );
}

const DOC_STATUS = {
  UPLOADED: { cls: 'badge-blue', label: 'Uploaded' },
  PROCESSING: { cls: 'badge-amber', label: 'Processing' },
  READY: { cls: 'badge-green', label: 'Ready' },
  FAILED: { cls: 'badge-red', label: 'Failed' },
};
export function DocStatusBadge({ status }) {
  const s = DOC_STATUS[status] || { cls: 'badge-slate', label: String(status || '—') };
  return <span className={'badge ' + s.cls}>{s.label}</span>;
}

const RUN_COLOR = {
  COMPLETED: 'badge-green', FAILED: 'badge-red', TIMEOUT: 'badge-red',
  RUNNING: 'badge-blue', WAITING_CONFIRMATION: 'badge-amber',
  CANCELLED: 'badge-slate', CREATED: 'badge-slate',
};
export function StateBadge({ state }) {
  return <span className={'badge ' + (RUN_COLOR[state] || 'badge-slate')}>{state}</span>;
}

/* UI-only gate: honest "your role lacks X" note. Server ALSO returns 403. */
export function PermGate({ ok, need, children }) {
  if (ok) return children;
  return (
    <Card title="Restricted">
      <p className="mut">
        Your role ({need.role || 'unknown'}) does not include <code>{need.perm}</code>.
        This page is hidden for a reason — and the API returns 403 too.
      </p>
    </Card>
  );
}

/* Permission chips (server-resolved list from login /auth/me). */
export function PermChips({ permissions }) {
  if (!permissions?.length) return <p className="mut">No permissions resolved.</p>;
  return (
    <div className="chip-row">
      {permissions.map((p) => <span key={p} className="chip">{p}</span>)}
    </div>
  );
}

/* Live clock for "x minutes ago" style timestamps. */
export function useNow(intervalMs = 30000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export const fmtTs = (ts) => {
  if (!ts) return '—';
  const d = new Date(typeof ts === 'number' ? ts * 1000 : ts);
  return isNaN(d) ? String(ts) : d.toLocaleString();
};

/* Minimal theme-aware SVG charts (no dependencies). Values are plain
   numbers the caller computed; missing buckets are simply absent points. */
export function LineChart({ points, height = 120, label = 'trend' }) {
  const W = 560, H = height, P = 8;
  const vals = (points || []).map((p) => p.value).filter((v) => typeof v === 'number');
  const max = Math.max(1, ...vals);
  const step = points && points.length > 1 ? (W - P * 2) / (points.length - 1) : 0;
  const xy = (points || []).map((p, i) => [
    P + i * step,
    H - P - (typeof p.value === 'number' ? (p.value / max) * (H - P * 2) : 0),
    p.value, p.label,
  ]);
  const d = xy.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={label}
      style={{ background: 'var(--bg-deep)', borderRadius: 8 }}>
      <title>{label}</title>
      {xy.map(([x, y, v, l], i) => (
        <g key={i}>
          <circle cx={x} cy={y} r="3" fill="var(--accent)"><title>{`${l ?? i}: ${v ?? 'no data'}`}</title></circle>
        </g>
      ))}
      {xy.length > 1 && <path d={d} fill="none" stroke="var(--accent)" strokeWidth="2" />}
    </svg>
  );
}

export function BarChart({ bars, height = 120, label = 'bars' }) {
  const W = 560, H = height;
  const max = Math.max(1, ...(bars || []).map((b) => b.value || 0));
  const bw = bars && bars.length ? W / bars.length : W;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={label}
      style={{ background: 'var(--bg-deep)', borderRadius: 8 }}>
      <title>{label}</title>
      {(bars || []).map((b, i) => {
        const h = ((b.value || 0) / max) * (H - 24);
        return (
          <g key={i}>
            <rect x={i * bw + 6} y={H - 14 - h} width={Math.max(4, bw - 12)} height={h}
              fill="var(--accent)" rx="3"><title>{`${b.label}: ${b.value}`}</title></rect>
            <text x={i * bw + bw / 2} y={H - 2} fontSize="9" textAnchor="middle" fill="var(--mut)">
              {String(b.label).slice(0, 10)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
