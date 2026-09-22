import React, { useEffect, useState } from 'react';
import { apiFetch } from '../rbac.js';
import { T } from '../i18n.js';
import { SysAdminView } from '../admin.jsx';
import { Card, Skeleton, ErrorNote, useToast } from '../ui.jsx';

/* Admin: server-side gated (ADMIN / security_admin for system controls,
   USER_* perms for user management). Secrets never exist in these payloads
   — the backend builds them exclusively from the allowlisted schema and
   scrubs them again before responding. */

const FALLBACK_ROLES = [
  'ADMIN', 'MANAGER', 'OPERATOR', 'REVIEWER', 'USER',
  'field_engineer', 'process_engineer', 'safety_inspector',
  'approving_manager', 'security_admin', 'auditor',
];

function Users({ tok, lang }) {
  const [users, setUsers] = useState(null);
  const [roles, setRoles] = useState(FALLBACK_ROLES);
  const [err, setErr] = useState('');
  const [form, setForm] = useState({ username: '', password: '', role: 'field_engineer' });
  const toast = useToast();

  const load = () => {
    setErr('');
    apiFetch('/api/v1/admin/users', tok).then((j) => setUsers(j.users))
      .catch((e) => { setErr(e.message); setUsers([]); });
    apiFetch('/api/v1/roles', tok).then((j) => setRoles(j.roles))
      .catch(() => { /* UI fallback list; backend validates anyway */ });
  };
  useEffect(load, [tok]);

  const create = async () => {
    setErr('');
    try {
      await apiFetch('/api/v1/admin/users', tok, { method: 'POST', body: JSON.stringify(form) });
      setForm({ username: '', password: '', role: 'field_engineer' });
      toast('User created.', 'success');
      load();
    } catch (e) { setErr(e.message); }
  };
  const patch = async (u, body, msg) => {
    setErr('');
    try { await apiFetch(`/api/v1/admin/users/${u.id}`, tok, { method: 'PATCH', body: JSON.stringify(body) }); toast(msg, 'success'); load(); }
    catch (e) { setErr(e.message); }
  };
  const remove = async (u) => {
    if (!confirm(`Delete user ${u.username}?`)) return;
    try { await apiFetch(`/api/v1/admin/users/${u.id}`, tok, { method: 'DELETE' }); toast('User deleted.', 'success'); load(); }
    catch (e) { setErr(e.message); }
  };

  return (
    <Card title={T(lang, 'adminUsers')}>
      <ErrorNote error={err} />
      <div className="row" style={{ marginBottom: 12 }}>
        <input className="input" placeholder={T(lang, 'username')} value={form.username}
          onChange={(e) => setForm({ ...form, username: e.target.value })} />
        <input className="input" type="password" placeholder={T(lang, 'password')} value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value })} />
        <select className="select" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
          {roles.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <button className="btn" disabled={!form.username || !form.password}
          onClick={create}>{T(lang, 'save')}</button>
      </div>
      {users === null && <Skeleton rows={4} />}
      {users?.length > 0 && (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th>{T(lang, 'username')}</th><th>{T(lang, 'role')}</th><th>Status</th><th>{T(lang, 'actions')}</th></tr></thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>{u.username}</td>
                  <td>
                    <select className="select" value={u.role}
                      onChange={(e) => patch(u, { role: e.target.value }, `Role of ${u.username} → ${e.target.value}`)}>
                      {roles.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </td>
                  <td>
                    <span className={'badge ' + (u.active ? 'badge-green' : 'badge-slate')}>
                      {u.active ? 'active' : 'disabled'}
                    </span>
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button className="btn btn-ghost btn-mini" onClick={() => patch(u, { active: !u.active }, 'Toggled.')}>
                      {u.active ? 'Disable' : 'Enable'}
                    </button>
                    <button className="btn btn-danger btn-mini" onClick={() => remove(u)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mut">Roles come from the server (<code>GET /api/v1/roles</code>) when available; every mutation is re-validated server-side.</p>
    </Card>
  );
}

export default function Admin({ tok, user, lang }) {
  const [tab, setTab] = useState('system');
  const isPrivileged = user.role === 'ADMIN' || user.role === 'security_admin';
  const canUsers = (user.permissions || []).includes('USER_READ');

  if (!isPrivileged && !canUsers) {
    return (
      <div>
        <div className="page-head"><h2>{T(lang, 'admin')}</h2></div>
        <Card title="Restricted">
          <p className="mut">{T(lang, 'deniedNote')} — requires ADMIN / security_admin.</p>
        </Card>
      </div>
    );
  }

  return (
    <div>
      <div className="page-head">
        <h2>{T(lang, 'admin')}</h2>
        <span className="pill pill-role">{user.role}</span>
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        {isPrivileged && (
          <button className={'btn ' + (tab === 'system' ? '' : 'btn-ghost')}
            onClick={() => setTab('system')}>{T(lang, 'adminSystem')}</button>)}
        {canUsers && (
          <button className={'btn ' + (tab === 'users' ? '' : 'btn-ghost')}
            onClick={() => setTab('users')}>{T(lang, 'adminUsers')}</button>)}
      </div>
      {tab === 'system' && isPrivileged && <SysAdminView tok={tok} lang={lang} />}
      {tab === 'users' && canUsers && <Users tok={tok} lang={lang} />}
    </div>
  );
}
