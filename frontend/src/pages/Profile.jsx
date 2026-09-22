import React, { useEffect, useRef, useState } from 'react';
import { request } from '../api.js';
import { getAccess } from '../session.js';
import { T } from '../i18n.js';
import { Card, Spinner, ErrorNote } from '../ui.jsx';

/* Profile for EVERY authenticated role: avatar, personal/employment/
   contact sections, self-edit of permitted fields, photo upload/camera,
   and Security (change password). Employment-controlled fields are
   read-only — staff-only paths enforce the rest server-side. */

const EDITABLE = ['phone', 'alt_phone', 'contact_email', 'addr1', 'addr2',
  'city', 'state', 'country', 'postal', 'emergency_name', 'emergency_phone',
  'emergency_rel', 'first_name', 'middle_name', 'last_name'];

function SecurityCard({ lang }) {
  const [f, setF] = useState({ cur: '', np: '', cp: '' });
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true); setErr(''); setMsg('');
    try {
      const j = await request('/api/v1/auth/password/change',
        { method: 'POST', body: { current_password: f.cur, new_password: f.np, confirm_password: f.cp } });
      setMsg(j.message || T(lang, 'passwordChanged'));
      setF({ cur: '', np: '', cp: '' });
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Card title={T(lang, 'changePassword')}>
      <label className="field">{T(lang, 'currentPassword')}
        <input className="input" type="password" value={f.cur}
          onChange={(e) => setF({ ...f, cur: e.target.value })} />
      </label>
      <label className="field">{T(lang, 'newPassword')}
        <input className="input" type="password" value={f.np}
          onChange={(e) => setF({ ...f, np: e.target.value })} />
      </label>
      <label className="field">{T(lang, 'confirmPassword')}
        <input className="input" type="password" value={f.cp}
          onChange={(e) => setF({ ...f, cp: e.target.value })} />
      </label>
      <ErrorNote error={err} />
      {msg && <p className="ok" style={{ fontSize: 13 }}>{msg}</p>}
      <button className="btn btn-mini" disabled={busy} onClick={go}>
        {busy ? <Spinner label="…" /> : T(lang, 'changePassword')}
      </button>
    </Card>
  );
}

export default function Profile({ tok, user, lang }) {
  const [me, setMe] = useState(null);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [photoUrl, setPhotoUrl] = useState('');
  const fileRef = useRef(null);
  const camRef = useRef(null);

  const loadPhoto = async () => {
    try {
      const r = await fetch('/api/v1/employees/me/photo',
        { headers: { Authorization: 'Bearer ' + getAccess() } });
      if (!r.ok) { setPhotoUrl(''); return; }
      const url = URL.createObjectURL(await r.blob());
      setPhotoUrl((old) => { if (old) URL.revokeObjectURL(old); return url; });
    } catch { setPhotoUrl(''); }
  };

  const load = () => {
    setErr('');
    request('/api/v1/employees/me').then((j) => { setMe(j); loadPhoto(); })
      .catch((e) => setErr(e.message));
  };
  useEffect(load, []);

  const save = async () => {
    setBusy(true); setErr(''); setMsg('');
    try {
      const fields = {};
      for (const k of EDITABLE) fields[k] = me[k] ?? '';
      const j = await request(`/api/v1/employees/${me.id}`, { method: 'PATCH', body: { fields } });
      setMe(j); setMsg(T(lang, 'saved'));
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const sendPhoto = async (file) => {
    if (!file) return;
    setErr(''); setMsg(''); setBusy(true);
    try {
      const fd = new FormData();
      fd.append('f', file, file.name);
      const r = await fetch('/api/v1/employees/me/photo', {
        method: 'POST', headers: { Authorization: 'Bearer ' + getAccess() }, body: fd });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
      setMsg(T(lang, 'saved'));
      loadPhoto();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  if (err && !me) return <ErrorNote error={err} onRetry={load} />;
  if (!me) return <Spinner label={T(lang, 'loading')} />;

  const ro = (k, label) => (
    <tr key={k}><td className="mut">{label || k}</td><td>{String(me[k] ?? '')}</td></tr>
  );
  const initials = ((me.first_name || '')[0] || (me.username || '')[0] || '?').toUpperCase();

  return (
    <div>
      <div className="page-head"><h2>{T(lang, 'myProfile')}</h2>
        <span className="pill pill-role">{me.role}</span></div>
      <ErrorNote error={err} onRetry={load} />
      {msg && <p className="ok" style={{ fontSize: 13 }}>{msg}</p>}
      <div className="grid-2">
        <Card title={T(lang, 'personalInfo')}>
          <div className="row" style={{ alignItems: 'center', marginBottom: 10 }}>
            {photoUrl
              ? <img src={photoUrl} alt={T(lang, 'profilePhoto')}
                  style={{ width: 72, height: 72, borderRadius: '50%', objectFit: 'cover' }} />
              : <div className="brand-mark" style={{ width: 72, height: 72, fontSize: 28 }}>{initials}</div>}
            <span>
              <div><b>{me.full_name || me.username}</b></div>
              <div className="mut">{me.employee_id || me.id}</div>
            </span>
          </div>
          <div className="row" style={{ marginBottom: 10 }}>
            <button className="btn btn-ghost btn-mini" onClick={() => fileRef.current?.click()}>{T(lang, 'uploadPhoto')}</button>
            <button className="btn btn-ghost btn-mini" onClick={() => camRef.current?.click()}>{T(lang, 'takePhoto')}</button>
            <input ref={fileRef} type="file" style={{ display: 'none' }} accept=".jpg,.jpeg,.png"
              onChange={(e) => { sendPhoto(e.target.files?.[0]); e.target.value = ''; }} />
            <input ref={camRef} type="file" style={{ display: 'none' }} accept="image/*" capture="environment"
              onChange={(e) => { sendPhoto(e.target.files?.[0]); e.target.value = ''; }} />
          </div>
          <p className="mut">{T(lang, 'photoPrivateNote')}</p>
          <table className="tbl"><tbody>
            {ro('full_name', 'Full Name')}
            {ro('employee_id', T(lang, 'employeeId'))}
            {ro('username', T(lang, 'orgEmail'))}
            {ro('identity_status', 'Verification')}
          </tbody></table>
          <h4 className="mut">{T(lang, 'employmentInfo')}</h4>
          <table className="tbl"><tbody>
            {ro('department', T(lang, 'department'))}
            {ro('team', T(lang, 'team'))}
            {ro('designation', T(lang, 'designation'))}
            {ro('joining_date', T(lang, 'joiningDate'))}
            {ro('emp_status', T(lang, 'empStatus'))}
          </tbody></table>
        </Card>
        <div>
          <Card title={T(lang, 'contactInfo')}>
            {EDITABLE.map((k) => (
              <label className="field" key={k}>{k}
                <input className="input" value={me[k] ?? ''}
                  onChange={(e) => setMe({ ...me, [k]: e.target.value })} />
              </label>
            ))}
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn" disabled={busy} onClick={save}>
                {busy ? <Spinner label="…" /> : T(lang, 'save')}
              </button>
            </div>
            {me.blood_group && <p className="mut">Blood Group: {me.blood_group} (visible only to you and authorized staff)</p>}
          </Card>
          <SecurityCard lang={lang} />
        </div>
      </div>
    </div>
  );
}
