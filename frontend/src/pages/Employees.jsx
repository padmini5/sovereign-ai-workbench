import React, { useEffect, useState } from 'react';
import { request } from '../api.js';
import { T } from '../i18n.js';
import { Card, Spinner, ErrorNote, Empty } from '../ui.jsx';
import AssignWork from './AssignWork.jsx';
import { STATUS_LABEL } from './StartWork.jsx';

/* Employee management (authorized staff only — backend enforces USER_CREATE /
   USER_UPDATE; UI only hides). Sections keep the form readable. */

const canCreate = (u) => (u.permissions || []).includes('USER_CREATE');
const canManage = (u) => (u.permissions || []).includes('USER_UPDATE');

const EMPTY_FORM = {
  username: '', password: '', role: 'EMPLOYEE',
  first_name: '', middle_name: '', last_name: '', phone: '', alt_phone: '',
  contact_email: '', dob: '', gender: '', blood_group: '',
  department: 'Production', designation: '', team: '', manager_id: '',
  joining_date: '', addr1: '', addr2: '', city: '', state: '', country: '',
  postal: '', emergency_name: '', emergency_phone: '', emergency_rel: '',
};

function strength(pw) {
  let s = 0;
  if (pw.length >= 8) s += 1;
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) s += 1;
  if (/\d/.test(pw)) s += 1;
  if (/[^A-Za-z0-9]/.test(pw)) s += 1;
  return ['Too weak', 'Weak', 'Fair', 'Good', 'Strong'][s];
}

function genPw() {
  const a = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789@#$!';
  let s = '';
  const rnd = crypto.getRandomValues(new Uint32Array(16));
  for (const r of rnd) s += a[r % a.length];
  return 'Emp@2026#' + s.slice(0, 6);
}

export default function Employees({ tok, user, lang }) {
  const [rows, setRows] = useState(null);
  const [sel, setSel] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [msg, setMsg] = useState('');
  const [tmpPw, setTmpPw] = useState('');
  const [q, setQ] = useState('');
  const [deptF, setDeptF] = useState('all');
  const [tab, setTab] = useState('work');
  const [detail, setDetail] = useState({});
  const [assignOpen, setAssignOpen] = useState(false);
  const [assignFor, setAssignFor] = useState('');
  const canAssign = ['ADMIN', 'MANAGER', 'approving_manager'].includes(user.role)
    && (user.permissions || []).includes('WORK_MANAGE');
  const canWork = (user.permissions || []).includes('WORK_READ');
  const canAn = (user.permissions || []).includes('ANALYTICS_READ');

  const load = () => {
    setErr('');
    request('/api/v1/employees').then((j) => setRows(j.employees))
      .catch((e) => { setErr(e.message); setRows([]); });
  };
  useEffect(load, []);

  const open = async (id) => {
    setErr(''); setTab('work'); setDetail({});
    try { setSel(await request(`/api/v1/employees/${id}`)); }
    catch (e) { setErr(e.message); return; }
    if (canWork) {
      request(`/api/v1/work/tasks?user_id=${id}`)
        .then((j) => setDetail((d) => ({ ...d, tasks: j.tasks || [] })))
        .catch(() => setDetail((d) => ({ ...d, tasks: [] })));
      request(`/api/v1/work/attendance?user_id=${id}`)
        .then((j) => setDetail((d) => ({ ...d, attendance: j.rows || [] })))
        .catch(() => setDetail((d) => ({ ...d, attendance: [] })));
      request('/api/v1/work/schedules')
        .then((j) => setDetail((d) => ({ ...d, schedules: j.schedules || [] })))
        .catch(() => setDetail((d) => ({ ...d, schedules: [] })));
    }
    if (canAn) {
      request('/api/v1/analytics/criteria')
        .then((c) => setDetail((d) => ({ ...d, crit: c })))
        .catch(() => {});
      request(`/api/v1/analytics/employee/${id}`)
        .then((p) => setDetail((d) => ({ ...d, perf: p })))
        .catch(() => setDetail((d) => ({ ...d, perf: null })));
    }
  };

  const create = async () => {
    setBusy(true); setErr(''); setMsg('');
    try {
      const { username, password, role, ...profile } = form;
      const j = await request('/api/v1/employees',
        { method: 'POST', body: { username, password, role, profile } });
      setMsg(j.message || T(lang, 'employeeCreated'));
      setForm(EMPTY_FORM);
      load();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const act = async (path, body) => {
    setErr(''); setMsg('');
    try {
      await request(`/api/v1/employees/${sel.id}${path}`, { method: 'POST', body: body || {} });
      setMsg('Done.');
      open(sel.id); load();
    } catch (e) { setErr(e.message); }
  };

  const F = (k, label, type) => (
    <label className="field">{label || k}
      <input className="input" type={type || 'text'} value={form[k] || ''}
        onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
    </label>
  );

  const depts = [...new Set((rows || []).map((r) => r.department).filter(Boolean))];
  const filtered = (rows || []).filter((r) => {
    const hay = `${r.full_name || ''} ${r.username || ''} ${r.employee_id || ''} ${r.department || ''}`.toLowerCase();
    if (q && !hay.includes(q.toLowerCase())) return false;
    if (deptF !== 'all' && (r.department || '') !== deptF) return false;
    return true;
  });

  const TABS = [
    canWork && { k: 'work', label: T(lang, 'myWork') },
    canWork && { k: 'attendance', label: T(lang, 'attendance') },
    canWork && { k: 'schedule', label: T(lang, 'schedule') },
    canAn && { k: 'performance', label: T(lang, 'performance') },
  ].filter(Boolean);
  const activeTab = TABS.some((t) => t.k === tab) ? tab : (TABS[0] ? TABS[0].k : '');

  const empSched = () => {
    const list = detail.schedules;
    if (!list || !sel) return null;
    return list.find((r) => r.scope_type === 'user' && r.scope_id === sel.id)
      || list.find((r) => r.scope_type === 'team' && r.scope_id === (sel.department || ''))
      || list.find((r) => r.scope_type === 'org')
      || null;
  };
  const schedScopeLabel = (st) =>
    T(lang, st === 'user' ? 'scopeUser' : st === 'team' ? 'scopeTeam' : 'scopeOrg');

  return (
    <div>
      <div className="page-head"><h2>{T(lang, 'employees')}</h2></div>
      <ErrorNote error={err} onRetry={load} />
      {msg && <p className="ok" style={{ fontSize: 13 }}>{msg}</p>}

      {canCreate(user) && (
        <Card title={T(lang, 'createEmployee')}>
          <h4 className="mut">{T(lang, 'personalInfo')}</h4>
          <div className="grid-3">
            {F('first_name', T(lang, 'firstName'))}
            {F('middle_name', T(lang, 'middleName'))}
            {F('last_name', T(lang, 'lastName'))}
            {F('dob', 'Date of Birth', 'date')}
            <label className="field">Gender
              <select className="select" value={form.gender}
                onChange={(e) => setForm({ ...form, gender: e.target.value })}>
                <option value="">—</option>
                {['male', 'female', 'other', 'prefer_not_to_say'].map((g) => (
                  <option key={g} value={g}>{g}</option>))}
              </select>
            </label>
            <label className="field">Blood Group
              <select className="select" value={form.blood_group}
                onChange={(e) => setForm({ ...form, blood_group: e.target.value })}>
                <option value="">—</option>
                {['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'].map((g) => (
                  <option key={g} value={g}>{g}</option>))}
              </select>
            </label>
          </div>
          <h4 className="mut">{T(lang, 'contactInfo')}</h4>
          <div className="grid-3">
            {F('username', T(lang, 'orgEmail'))}
            {F('phone', T(lang, 'phone'))}
            {F('alt_phone', 'Alternate Phone')}
            {F('contact_email', 'Personal/Contact Email')}
          </div>
          <h4 className="mut">{T(lang, 'employmentInfo')}</h4>
          <div className="grid-3">
            <label className="field">Role
              <select className="select" value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}>
                {['EMPLOYEE', 'OPERATOR', 'REVIEWER', 'MANAGER', 'USER',
                  'field_engineer', 'process_engineer', 'safety_inspector',
                  'approving_manager', 'auditor'].map((r) => (
                  <option key={r} value={r}>{r}</option>))}
              </select>
            </label>
            {F('department', T(lang, 'department'))}
            {F('designation', T(lang, 'designation'))}
            {F('team', T(lang, 'team'))}
            {F('joining_date', T(lang, 'joiningDate'), 'date')}
          </div>
          <h4 className="mut">{T(lang, 'addressInfo')}</h4>
          <div className="grid-3">
            {F('addr1', 'Address Line 1')}
            {F('addr2', 'Address Line 2')}
            {F('city', 'City')}
            {F('state', 'State')}
            {F('country', 'Country')}
            {F('postal', 'Postal Code')}
          </div>
          <h4 className="mut">{T(lang, 'emergencyInfo')}</h4>
          <div className="grid-3">
            {F('emergency_name', 'Contact Name')}
            {F('emergency_phone', 'Contact Phone')}
            {F('emergency_rel', 'Relationship')}
          </div>
          <h4 className="mut">{T(lang, 'securityInfo')}</h4>
          <div className="row">
            <label className="field" style={{ minWidth: 220 }}>{T(lang, 'tempPassword')}
              <input className="input" type="text" value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })} />
            </label>
            <button className="btn btn-ghost btn-mini" style={{ alignSelf: 'end' }}
              onClick={() => setForm({ ...form, password: genPw() })}>
              {T(lang, 'generatePassword')}
            </button>
            <span className="mut" style={{ alignSelf: 'end', fontSize: 12 }}>
              {form.password ? strength(form.password) : ''}
            </span>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn" disabled={busy} onClick={create}>
              {busy ? <Spinner label="…" /> : T(lang, 'createEmployee')}
            </button>
          </div>
        </Card>
      )}

      {assignOpen && (
        <AssignWork user={user} lang={lang} presetAssignee={assignFor}
          onDone={() => { setAssignOpen(false); setAssignFor(''); load(); }} />
      )}

      <div className="grid-2">
        <Card title={T(lang, 'employees')}>
          <div className="row" style={{ marginBottom: 8 }}>
            <input className="input" style={{ flex: 1, minWidth: 140 }}
              placeholder={T(lang, 'searchEmployees')} value={q}
              onChange={(e) => setQ(e.target.value)} />
            <select className="select" value={deptF}
              onChange={(e) => setDeptF(e.target.value)}>
              <option value="all">{T(lang, 'allDepartments')}</option>
              {depts.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
            {canAssign && (
              <button className="btn btn-ghost btn-mini"
                onClick={() => { setAssignFor(''); setAssignOpen(true); }}>
                {T(lang, 'assignWork')}</button>)}
          </div>
          {rows === null && <Spinner label={T(lang, 'loading')} />}
          {rows !== null && filtered.length === 0 && <Empty title={T(lang, 'noResults')} />}
          {filtered.map((r) => (
            <div key={r.id} className="row" style={{ justifyContent: 'space-between', width: '100%' }}>
              <span><b>{r.full_name || r.username}</b>
                <span className="mut"> · {r.employee_id} · {r.department} · {r.emp_status}</span></span>
              <span className="row">
                {canAssign && (
                  <button className="btn btn-ghost btn-mini"
                    onClick={() => { setAssignFor(r.id); setAssignOpen(true); }}>
                    {T(lang, 'assignWork')}</button>)}
                <button className="btn btn-ghost btn-mini" onClick={() => open(r.id)}>
                  {T(lang, 'viewReport').replace('Report', 'Profile')}</button>
              </span>
            </div>
          ))}
        </Card>
        <Card title={sel ? (sel.full_name || sel.username) : T(lang, 'employees')}>
          {!sel && <Empty title={T(lang, 'noResults')} />}
          {sel && (
            <div>
              <table className="tbl"><tbody>
                {['employee_id', 'username', 'role', 'department', 'team', 'designation',
                  'emp_status', 'phone', 'city', 'joining_date'].map((k) => (
                  <tr key={k}><td className="mut">{k}</td><td>{String(sel[k] ?? '')}</td></tr>
                ))}
              </tbody></table>
              {canManage(user) && (
                <div className="row" style={{ marginTop: 8 }}>
                  <button className="btn btn-ghost btn-mini" onClick={() => act('/suspend')}>{T(lang, 'suspend')}</button>
                  <button className="btn btn-ghost btn-mini" onClick={() => act('/archive')}>{T(lang, 'archive')}</button>
                  <input className="input" placeholder={T(lang, 'tempPassword')} value={tmpPw}
                    onChange={(e) => setTmpPw(e.target.value)} style={{ maxWidth: 180 }} />
                  <button className="btn btn-ghost btn-mini"
                    onClick={() => act('/reset-password', { password: tmpPw || genPw() })}>
                    {T(lang, 'resetPassword')}
                  </button>
                </div>
              )}
              <div className="row" style={{ marginTop: 10 }}>
                {TABS.map((t) => (
                  <button key={t.k}
                    className={'btn btn-mini ' + (activeTab === t.k ? '' : 'btn-ghost')}
                    style={activeTab === t.k ? { fontWeight: 700 } : undefined}
                    onClick={() => setTab(t.k)}>{t.label}</button>
                ))}
                {canAssign && (
                  <button className="btn btn-ghost btn-mini"
                    onClick={() => { setAssignFor(sel.id); setAssignOpen(true); }}>
                    {T(lang, 'assignWork')}</button>)}
              </div>
              <div style={{ marginTop: 8 }}>
                {activeTab === 'work' && (
                  detail.tasks == null ? <Spinner label={T(lang, 'loading')} />
                  : detail.tasks.length === 0 ? <Empty title={T(lang, 'noRecordsYet')} />
                  : detail.tasks.map((t) => (
                    <div key={t.id} className="row"
                      style={{ justifyContent: 'space-between', width: '100%', fontSize: 13 }}>
                      <span>{t.title}
                        <span className="mut"> · {t.priority || 'medium'}
                          {t.due_date ? ` · ${T(lang, 'dueDate')}: ${t.due_date}` : ''}
                          {t.created_by_name ? ` · ${T(lang, 'assignedBy')}: ${t.created_by_name}` : ''}</span></span>
                      <span className="badge badge-slate">{STATUS_LABEL(t.status, lang)} · {t.progress}%</span>
                    </div>))
                )}
                {activeTab === 'attendance' && (
                  detail.attendance == null ? <Spinner label={T(lang, 'loading')} />
                  : detail.attendance.length === 0 ? <Empty title={T(lang, 'noRecordsYet')} />
                  : <table className="tbl"><tbody>
                      {detail.attendance.slice(0, 31).map((r) => (
                        <tr key={r.id || r.date}>
                          <td className="mut">{r.date}</td>
                          <td>{r.status}</td>
                          <td className="mut">{r.check_in || '—'} – {r.check_out || '—'}</td>
                        </tr>))}
                    </tbody></table>
                )}
                {activeTab === 'schedule' && (
                  detail.schedules == null ? <Spinner label={T(lang, 'loading')} />
                  : !empSched() ? <Empty title={T(lang, 'noRecordsYet')}
                      hint={T(lang, 'orgDefaultSchedule')} />
                  : (() => {
                    const sc = empSched();
                    return (
                      <div>
                        <div className="row" style={{ alignItems: 'center' }}>
                          <b>{sc.name || sc.shift}</b>
                          <span className="mut"> · {sc.start} – {sc.end}</span>
                          {sc.demo && <span className="badge badge-amber">{T(lang, 'demoBadge')}</span>}
                        </div>
                        <table className="tbl"><tbody>
                          <tr><td className="mut">{T(lang, 'scope')}</td>
                            <td>{schedScopeLabel(sc.scope_type)}
                              {sc.scope_id ? `: ${sc.scope_type === 'user'
                                ? (sc.scope_id === sel.id ? (sel.full_name || sel.username) : sc.scope_id)
                                : sc.scope_id}` : ''}</td></tr>
                          <tr><td className="mut">{T(lang, 'workingDays')}</td>
                            <td>{(sc.working_days || []).join(', ') || '—'}</td></tr>
                          <tr><td className="mut">{T(lang, 'holidayList')}</td>
                            <td>{(sc.holidays || []).join(', ') || '—'}</td></tr>
                        </tbody></table>
                      </div>
                    );
                  })()
                )}
                {activeTab === 'performance' && (
                  detail.perf == null ? <Empty title={T(lang, 'insufficientData')} />
                  : (
                    <div>
                      <div style={{ fontSize: 26, fontWeight: 700 }}>
                        {detail.perf.overall != null ? Math.round(detail.perf.overall * 100) : '—'}
                        <span style={{ fontSize: 13 }}> / 100</span></div>
                      <table className="tbl"><tbody>
                        {Object.entries(detail.perf.parts || {}).map(([k, v]) => (
                          <tr key={k}>
                            <td className="mut">{k.replace(/_/g, ' ')}</td>
                            <td>{v == null ? T(lang, 'insufficientData') : Math.round(v * 100)}</td>
                            <td className="mut">
                              {detail.crit?.weights?.[k] != null
                                ? `${Math.round(detail.crit.weights[k] * 100)}%` : ''}</td>
                          </tr>))}
                      </tbody></table>
                      <p className="mut">{T(lang, 'recordedDataNote')}</p>
                    </div>
                  ))
                }
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
