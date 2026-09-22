import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './workbench.css';
import { initTheme } from './theme.js';
import { apiFetch, login, navFor, hasPerm } from './rbac.js';
import { ChatView } from './ai-chat.jsx';
import { DocsView } from './docs.jsx';
import { AgentsView } from './agents.jsx';
import { SysAdminView } from './admin.jsx';
import { LANGS, STR, t } from './ui-strings.js';

const NAV_LABEL = { dashboard: 'dashboard', chat: 'chat', agents: 'agents', documents: 'documents', users: 'users', audit: 'audit' };

/* Phase 1 standalone console: login, session, role nav, admin users, audit.
   Served at /phase1.html (vite). Uses ONLY /api/v1 (permission-enforced). */

const DEMO = [
  ['admin', 'Admin123!', 'ADMIN'],
  ['manager', 'Mgr123!', 'MANAGER'],
  ['operator', 'Op123!', 'OPERATOR'],
  ['reviewer', 'Rev123!', 'REVIEWER'],
  ['user', 'User123!', 'USER'],
];
const LS = 'sov_phase1_session';

function useSession() {
  const [s, setS] = useState(() => {
    try { return JSON.parse(localStorage.getItem(LS) || 'null'); } catch { return null; }
  });
  const save = (v) => { setS(v); v ? localStorage.setItem(LS, JSON.stringify(v)) : localStorage.removeItem(LS); };
  return [s, save];
}

function LoginView({ onDone }) {
  const [u, setU] = useState('admin');
  const [p, setP] = useState('Admin123!');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const go = async (uu, pp) => {
    setBusy(true); setErr('');
    try { onDone(await login(uu ?? u, pp ?? p)); }
    catch (e) { setErr(e.status === 401 ? 'Invalid username or password.' : String(e.message)); }
    finally { setBusy(false); }
  };
  return (
    <div style={styles.box}>
      <h2 style={{ margin: '0 0 4px' }}>Sovereign AI Workbench</h2>
      <p style={styles.mut}>Secure sign-in — protected by role-based access control.</p>
      <label style={styles.lbl}>Username<input style={styles.in} value={u} onChange={(e) => setU(e.target.value)} /></label>
      <label style={styles.lbl}>Password<input style={styles.in} type="password" value={p} onChange={(e) => setP(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && go()} /></label>
      {err && <p style={styles.err}>{err}</p>}
      <button style={styles.btn} disabled={busy} onClick={() => go()}>{busy ? 'Signing in…' : 'Login'}</button>
      <div style={{ marginTop: 12 }}>
        <p style={styles.mut}>Demo accounts:</p>
        {DEMO.map(([du, dp, r]) => (
          <button key={du} style={styles.chip} onClick={() => go(du, dp)} title={dp}>{du} · {r}</button>
        ))}
      </div>
    </div>
  );
}

function AdminUsers({ tok }) {
  const [users, setUsers] = useState([]);
  const [err, setErr] = useState('');
  const [form, setForm] = useState({ username: '', password: '', role: 'USER' });
  const load = async () => {
    setErr('');
    try { setUsers((await apiFetch('/api/v1/admin/users', tok)).users); }
    catch (e) { setErr(e.status === 403 ? 'Forbidden: your role lacks USER_READ.' : e.message); }
  };
  useEffect(() => { load(); }, []);
  const create = async () => {
    setErr('');
    try {
      await apiFetch('/api/v1/admin/users', tok, { method: 'POST', body: JSON.stringify(form) });
      setForm({ username: '', password: '', role: 'USER' }); await load();
    } catch (e) { setErr(e.message); }
  };
  const cycleRole = async (usr) => {
    const order = ['USER', 'OPERATOR', 'REVIEWER', 'MANAGER', 'ADMIN'];
    const next = order[(order.indexOf(usr.role) + 1) % order.length];
    try {
      await apiFetch(`/api/v1/admin/users/${usr.id}`, tok, { method: 'PATCH', body: JSON.stringify({ role: next }) });
      await load();
    } catch (e) { setErr(e.message); }
  };
  const toggle = async (usr) => {
    try {
      await apiFetch(`/api/v1/admin/users/${usr.id}`, tok, { method: 'PATCH', body: JSON.stringify({ active: !usr.active }) });
      await load();
    } catch (e) { setErr(e.message); }
  };
  const remove = async (usr) => {
    if (!confirm(`Delete user ${usr.username}?`)) return;
    try { await apiFetch(`/api/v1/admin/users/${usr.id}`, tok, { method: 'DELETE' }); await load(); }
    catch (e) { setErr(e.message); }
  };
  return (
    <div style={styles.card}>
      <h3>User management (ADMIN)</h3>
      {err && <p style={styles.err}>{err}</p>}
      <table style={styles.tbl}>
        <thead><tr><th>Username</th><th>Role</th><th>Active</th><th>Actions</th></tr></thead>
        <tbody>{users.map((x) => (
          <tr key={x.id}><td>{x.username}</td><td>{x.role}</td><td>{x.active ? 'yes' : 'no'}</td>
            <td>
              <button style={styles.mini} onClick={() => cycleRole(x)}>cycle role</button>{' '}
              <button style={styles.mini} onClick={() => toggle(x)}>{x.active ? 'deactivate' : 'activate'}</button>{' '}
              <button style={styles.mini} onClick={() => remove(x)}>delete</button>
            </td></tr>))}</tbody>
      </table>
      <h4>Create user (needs USER_CREATE)</h4>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <input style={styles.in} placeholder="username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
        <input style={styles.in} placeholder="password (min 6)" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        <select style={styles.in} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
          {['USER', 'OPERATOR', 'REVIEWER', 'MANAGER', 'ADMIN'].map((r) => <option key={r}>{r}</option>)}
        </select>
        <button style={styles.btn} onClick={create}>Create</button>
      </div>
    </div>
  );
}

function AuditView({ tok }) {
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState('');
  useEffect(() => {
    apiFetch('/api/v1/admin/audit', tok).then((j) => setRows(j.rows))
      .catch((e) => setErr(e.status === 403 ? 'Forbidden: your role lacks AUDIT_READ.' : e.message));
  }, []);
  if (err) return <div style={styles.card}><h3>Audit log</h3><p style={styles.err}>{err}</p></div>;
  return (
    <div style={styles.card}><h3>Audit log (auth + permission events)</h3>
      <table style={styles.tbl}><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Resource</th><th>Decision</th></tr></thead>
        <tbody>{rows.slice(0, 50).map((r, i) => (
          <tr key={i}><td>{new Date(r.ts * 1000).toLocaleString()}</td><td>{r.actor_id || '—'}</td>
            <td>{r.action}</td><td>{r.resource || ''}</td><td>{r.decision}</td></tr>))}</tbody></table>
    </div>
  );
}

function App() {
  const [sess, save] = useSession();
  const [tab, setTab] = useState('dashboard');
  const [askDoc, setAskDoc] = useState(null); // [Ask AI] target from Documents
  const [me, setMe] = useState(null);
  const [uiLang, setUiLang] = useState('en'); // Step 19: persisted preference
  useEffect(() => {
    if (!sess) return;
    apiFetch('/api/v1/auth/me', sess.access_token).then(setMe).catch(() => { save(null); setMe(null); });
    apiFetch('/api/v1/auth/language', sess.access_token)
      .then((j) => setUiLang(j.lang || 'en')).catch(() => {});
  }, [sess]);
  if (!sess) return <div style={styles.wrap}><LoginView onDone={save} /></div>;
  const tok = sess.access_token;
  const user = me || sess.user;
  const logout = async () => { try { await apiFetch('/api/v1/auth/logout', tok, { method: 'POST' }); } catch { /* stateless */ } save(null); };
  const setLang = async (l) => {
    setUiLang(l);
    try { await apiFetch('/api/v1/auth/language', tok, { method: 'PUT', body: JSON.stringify({ lang: l }) }); }
    catch { /* preference persist is best-effort; chat still takes per-request lang */ }
  };
  const navLabel = (to, fallback) => {
    const k = NAV_LABEL[to];
    if (k && STR[uiLang] && STR[uiLang][k]) return STR[uiLang][k];
    if (k && uiLang === 'en') return t('en', k);
    return fallback; // untranslated sections stay in English (honest fallback)
  };
  return (
    <div style={styles.wrap}>
      <header style={styles.top}>
        <div><b>Sovereign AI Workbench</b> <span style={styles.pill}>{user.role}</span> <span style={styles.mut}>{user.username}</span></div>
        <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <label style={styles.mut}>{t(uiLang, 'language')}{' '}
            <select value={uiLang} onChange={(e) => setLang(e.target.value)} style={styles.sel}>
              {LANGS.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
            </select></label>
          <button style={styles.ghost} onClick={logout}>{t(uiLang, 'logout')}</button>
        </span>
      </header>
      <nav style={{ display: 'flex', gap: 8, margin: '12px 0', flexWrap: 'wrap' }}>
        {navFor(user.role).map((n) => (
          <button key={n.to} style={tab === n.to ? styles.btn : styles.ghost} onClick={() => setTab(n.to)}>{navLabel(n.to, n.label)}</button>
        ))}
      </nav>
      {tab === 'dashboard' && (
        <div style={styles.card}>
          <h3>{user.role} dashboard</h3>
          <p style={styles.mut}>Your permissions (server-resolved):</p>
          <p>{(user.permissions || sess.user?.permissions || []).join(', ')}</p>
          {!hasPerm(user, 'USER_READ') && <p style={styles.mut}>User admin is hidden for your role — and the API also returns 403.</p>}
        </div>
      )}
      {tab === 'users' && (hasPerm(user, 'USER_READ') ? <AdminUsers tok={tok} /> : <div style={styles.card}><p style={styles.err}>403 — requires USER_READ.</p></div>)}
      {tab === 'team' && <div style={styles.card}><h3>Team (MANAGER)</h3><AdminUsers tok={tok} /></div>}
      {tab === 'audit' && <AuditView tok={tok} />}
      {tab === 'chat' && (hasPerm(user, 'AI_CHAT') ? <ChatView tok={tok} user={user} askDoc={askDoc} uiLang={uiLang} /> : <div style={styles.card}><p style={styles.err}>403 — requires AI_CHAT.</p></div>)}
      {tab === 'agents' && <AgentsView tok={tok} user={user} />}
      {tab === 'documents' && (hasPerm(user, 'DOCUMENT_READ') ? <DocsView tok={tok} user={user} onAsk={(d) => { setAskDoc({ id: d.id, filename: d.filename }); setTab('chat'); }} /> : <div style={styles.card}><p style={styles.err}>403 — requires DOCUMENT_READ.</p></div>)}
      {tab === 'system' && (user.role === 'ADMIN' ? <SysAdminView tok={tok} /> : <div style={styles.card}><p style={styles.err}>403 — ADMIN only.</p></div>)}
      {(tab === 'reviews' || tab === 'reports' || tab === 'models') && (
        <div style={styles.card}><h3 style={{ textTransform: 'capitalize' }}>{tab}</h3>
          <p style={styles.mut}>Document/RAG workflows land in Phase 3+. Chat above is the working Phase 2 assistant.</p></div>
      )}
    </div>
  );
}

const styles = {
  wrap: { maxWidth: 980, margin: '0 auto', padding: 16, fontFamily: 'Segoe UI,system-ui,sans-serif', background: 'var(--bg)', color: 'var(--text)', minHeight: '100vh' },
  box: { maxWidth: 520, margin: '60px auto', background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24 },
  card: { background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 16, marginBottom: 12, color: 'var(--text)' },
  top: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--bg-soft)', border: '1px solid var(--border)', borderRadius: 12, padding: '10px 14px' },
  lbl: { display: 'block', margin: '8px 0', fontSize: 14 },
  in: { display: 'block', width: '100%', marginTop: 4, background: 'var(--input-bg)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 8, padding: 8 },
  btn: { background: 'var(--accent)', color: 'var(--btn-text)', border: 0, borderRadius: 8, padding: '8px 14px', cursor: 'pointer', fontWeight: 600 },
  ghost: { background: 'var(--ghost-bg)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 8, padding: '8px 14px', cursor: 'pointer' },
  chip: { background: 'var(--chip-bg)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 16, padding: '4px 10px', margin: '2px 4px 2px 0', cursor: 'pointer', fontSize: 12 },
  mini: { background: 'var(--ghost-bg)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 6, padding: '3px 8px', cursor: 'pointer', fontSize: 12 },
  pill: { background: 'var(--chip-bg)', border: '1px solid var(--border-strong)', borderRadius: 12, padding: '2px 10px', fontSize: 12 },
  sel: { background: 'var(--input-bg)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 6, padding: 4, fontSize: 12 },
  mut: { color: 'var(--mut)', fontSize: 13 },
  err: { color: 'var(--bad)', fontSize: 13 },
  tbl: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
};
document.body.style.margin = '0';
document.body.style.background = 'var(--bg)';
initTheme();
createRoot(document.getElementById('root')).render(<App />);
