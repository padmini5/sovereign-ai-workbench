import React, { useEffect, useState } from 'react';
import { request } from '../api.js';
import { T } from '../i18n.js';
import { Card, Spinner, ErrorNote, Empty } from '../ui.jsx';

/* Work schedule: shows the caller's resolved roster — assigned user/team/org
   schedule if one exists, otherwise the organization default — and, for
   managers/admins, scoped CRUD. Server enforces scope: a manager may only
   manage rows in their own department; org-wide rows are admin-only. */

const isSchedMgr = (u) => ['ADMIN', 'MANAGER', 'approving_manager'].includes(u.role)
  && (u.permissions || []).includes('WORK_MANAGE');

const EMPTY = {
  name: '', shift: 'Morning', start: '09:00', end: '18:00',
  break_start: '13:00', break_end: '14:00',
  working_days: 'Monday, Tuesday, Wednesday, Thursday, Friday',
  holidays: '', scope_type: 'user', scope_id: '', notes: '',
};

const csv = (s) => String(s || '').split(',').map((x) => x.trim()).filter(Boolean);
const scopeLabel = (lang, st) =>
  T(lang, st === 'user' ? 'scopeUser' : st === 'team' ? 'scopeTeam' : 'scopeOrg');

export default function Schedule({ tok, user, lang }) {
  const [s, setS] = useState(null);
  const [err, setErr] = useState('');
  const [rows, setRows] = useState(null);
  const [emps, setEmps] = useState([]);
  const [form, setForm] = useState(EMPTY);
  const [editId, setEditId] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const canManage = isSchedMgr(user);

  const load = () => {
    setErr('');
    request('/api/v1/work/schedule').then(setS)
      .catch((e) => { setErr(e.message); setS(null); });
    if (canManage) {
      request('/api/v1/work/schedules').then((j) => setRows(j.schedules || []))
        .catch((e) => { setErr(e.message); setRows([]); });
      request('/api/v1/employees').then((j) => setEmps(j.employees || []))
        .catch(() => setEmps([]));
    }
  };
  useEffect(load, []);

  const startEdit = (r) => {
    setEditId(r.id);
    setForm({
      name: r.name || '', shift: r.shift || 'Morning',
      start: r.start || '09:00', end: r.end || '18:00',
      break_start: r.break_start || '', break_end: r.break_end || '',
      working_days: (r.working_days || []).join(', '),
      holidays: (r.holidays || []).join(', '),
      scope_type: r.scope_type, scope_id: r.scope_id || '', notes: r.notes || '',
    });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const save = async () => {
    if (busy) return;
    setBusy(true); setErr(''); setMsg('');
    const body = {
      name: form.name, shift: form.shift, start: form.start, end: form.end,
      break_start: form.break_start, break_end: form.break_end,
      working_days: csv(form.working_days), holidays: csv(form.holidays),
      scope_type: form.scope_type, scope_id: form.scope_id, notes: form.notes,
    };
    try {
      if (editId) await request(`/api/v1/work/schedules/${editId}`, { method: 'PATCH', body });
      else await request('/api/v1/work/schedules', { method: 'POST', body });
      setEditId(''); setForm(EMPTY); setMsg('Saved.');
      load();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const del = async (sid) => {
    setErr(''); setMsg('');
    try {
      await request(`/api/v1/work/schedules/${sid}`, { method: 'DELETE' });
      if (editId === sid) { setEditId(''); setForm(EMPTY); }
      load();
    } catch (e) { setErr(e.message); }
  };

  if (err && !s) return <ErrorNote error={err} onRetry={load} />;
  if (!s) return <Spinner label={T(lang, 'loading')} />;

  const F = (k, label, type) => (
    <label className="field">{label}
      <input className="input" type={type || 'text'} value={form[k] || ''}
        onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
    </label>
  );
  const empName = (uid) => {
    const m = emps.find((e) => e.id === uid);
    return m ? (m.full_name || m.username) : uid;
  };

  return (
    <div>
      <div className="page-head"><h2>{T(lang, 'todaySchedule')}</h2>
        <p className="mut">
          {s.source === 'assigned'
            ? T(lang, 'assignedSchedule')
            : T(lang, 'orgDefaultSchedule')}
        </p></div>
      <ErrorNote error={err} />
      {msg && <p className="ok" style={{ fontSize: 13 }}>{msg}</p>}

      <div className="grid-2">
        <Card title={s.name || T(lang, 'todaySchedule')}>
          <div className="row" style={{ alignItems: 'center' }}>
            <span style={{ fontSize: 22, fontWeight: 700 }}>{s.start} – {s.end}</span>
            {s.demo && <span className="badge badge-amber">{T(lang, 'demoBadge')}</span>}
          </div>
          <table className="tbl"><tbody>
            <tr><td className="mut">{T(lang, 'shift')}</td><td>{s.shift}</td></tr>
            <tr><td className="mut">{T(lang, 'break_')}</td><td>{s.break || '—'}</td></tr>
            <tr><td className="mut">{T(lang, 'workingDays')}</td>
              <td>{(s.working_days || []).join(', ') || '—'}</td></tr>
            <tr><td className="mut">{T(lang, 'scope')}</td>
              <td>{s.scope ? scopeLabel(lang, s.scope) : '—'}</td></tr>
            <tr><td className="mut">{T(lang, 'holidayList')}</td>
              <td>{(s.holidays || []).join(', ') || '—'}</td></tr>
            <tr><td className="mut">{T(lang, 'nextWorkingDay')}</td>
              <td>{s.next_working_day}</td></tr>
            {s.notes && (
              <tr><td className="mut">{T(lang, 'notes')}</td><td>{s.notes}</td></tr>)}
          </tbody></table>
          <p className="mut">{s.note}</p>
        </Card>
        <Card title={T(lang, 'schedule')}>
          {(s.weekly || []).length === 0 && <Empty title={T(lang, 'noRecordsYet')} />}
          <table className="tbl"><tbody>
            {(s.weekly || []).map((w) => (
              <tr key={w.day}><td className="mut">{w.day}</td><td>{w.start} – {w.end}</td></tr>
            ))}
          </tbody></table>
          <p className="mut">{T(lang, 'leaveInfo')}</p>
        </Card>
      </div>

      {canManage && (
        <Card title={editId ? T(lang, 'editSchedule') : T(lang, 'addSchedule')}>
          <div className="grid-3">
            {F('name', T(lang, 'scheduleName'))}
            <label className="field">{T(lang, 'scope')}
              <select className="select" value={form.scope_type}
                onChange={(e) => setForm({ ...form, scope_type: e.target.value })}>
                <option value="user">{T(lang, 'scopeUser')}</option>
                <option value="team">{T(lang, 'scopeTeam')}</option>
                {user.role === 'ADMIN' && <option value="org">{T(lang, 'scopeOrg')}</option>}
              </select>
            </label>
            {form.scope_type === 'user' && (
              <label className="field">{T(lang, 'scopeUser')}
                <select className="select" value={form.scope_id}
                  onChange={(e) => setForm({ ...form, scope_id: e.target.value })}>
                  <option value="">—</option>
                  {emps.map((e) => (
                    <option key={e.id} value={e.id}>{e.full_name || e.username}</option>))}
                </select>
              </label>
            )}
            {form.scope_type === 'team' && F('scope_id', T(lang, 'department'))}
            {F('shift', T(lang, 'shift'))}
            {F('start', 'Start time', 'time')}
            {F('end', 'End time', 'time')}
            {F('break_start', 'Break start', 'time')}
            {F('break_end', 'Break end', 'time')}
            {F('working_days', T(lang, 'workingDays'))}
            {F('holidays', T(lang, 'holidayList'))}
            {F('notes', T(lang, 'notes'))}
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn"
              disabled={busy || (form.scope_type === 'user' && !form.scope_id)}
              onClick={save}>
              {busy ? <Spinner label="…" /> : T(lang, 'save')}
            </button>
            {editId && (
              <button className="btn btn-ghost"
                onClick={() => { setEditId(''); setForm(EMPTY); }}>Cancel</button>)}
          </div>
        </Card>
      )}

      {canManage && (
        <Card title={T(lang, 'schedule')}>
          {rows === null && <Spinner label={T(lang, 'loading')} />}
          {rows !== null && rows.length === 0 && <Empty title={T(lang, 'noRecordsYet')} />}
          {(rows || []).map((r) => (
            <div key={r.id} className="row"
              style={{ justifyContent: 'space-between', width: '100%' }}>
              <span>
                <b>{r.name || r.shift}</b>
                <span className="mut"> · {scopeLabel(lang, r.scope_type)}
                  {r.scope_id ? `: ${r.scope_type === 'user' ? empName(r.scope_id) : r.scope_id}` : ''}
                  {' · '}{r.start} – {r.end}</span>
                {r.demo && <span className="badge badge-amber">{T(lang, 'demoBadge')}</span>}
              </span>
              <span className="row">
                <button className="btn btn-ghost btn-mini" onClick={() => startEdit(r)}>
                  {T(lang, 'editSchedule')}</button>
                <button className="btn btn-ghost btn-mini" onClick={() => del(r.id)}>
                  {T(lang, 'deleteSchedule')}</button>
              </span>
            </div>
          ))}
        </Card>
      )}
    </div>
  );
}
