import React, { useEffect, useState } from 'react';
import { request } from '../api.js';
import { T } from '../i18n.js';
import { Card, Spinner, ErrorNote, Empty } from '../ui.jsx';

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

  const load = () => {
    setErr('');
    request('/api/v1/employees').then((j) => setRows(j.employees))
      .catch((e) => { setErr(e.message); setRows([]); });
  };
  useEffect(load, []);

  const open = async (id) => {
    setErr('');
    try { setSel(await request(`/api/v1/employees/${id}`)); }
    catch (e) { setErr(e.message); }
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

      <div className="grid-2">
        <Card title={T(lang, 'employees')}>
          {rows === null && <Spinner label={T(lang, 'loading')} />}
          {rows !== null && rows.length === 0 && <Empty title={T(lang, 'noResults')} />}
          {(rows || []).map((r) => (
            <div key={r.id} className="row" style={{ justifyContent: 'space-between', width: '100%' }}>
              <span><b>{r.full_name || r.username}</b>
                <span className="mut"> · {r.employee_id} · {r.department} · {r.emp_status}</span></span>
              <button className="btn btn-ghost btn-mini" onClick={() => open(r.id)}>{T(lang, 'viewReport').replace('Report', 'Profile')}</button>
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
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
