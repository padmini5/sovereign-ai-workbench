import React, { useEffect, useState } from 'react';
import { request } from '../api.js';
import { T } from '../i18n.js';
import { Card, Spinner, ErrorNote, Empty, StateBadge } from '../ui.jsx';

/* Worksheets: managers create (form or CSV/XLSX upload) + assign + review;
   employees see assigned sheets, fill permitted fields, submit with
   evidence. Team-first: one worksheet, many contributors. */

const canManage = (u) => ['ADMIN', 'MANAGER', 'approving_manager'].includes(u.role);
const canReview = (u) => ['ADMIN', 'MANAGER', 'REVIEWER', 'approving_manager'].includes(u.role);

export default function Worksheets({ tok, user, lang, onAskWork }) {
  const [sheets, setSheets] = useState(null);
  const [mine, setMine] = useState([]);
  const [users, setUsers] = useState([]);
  const [err, setErr] = useState('');
  const [form, setForm] = useState({ title: '', description: '', columns: 'task,owner,due' });
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState(null);
  const [assign, setAssign] = useState({});
  const [vals, setVals] = useState({});
  const [remarks, setRemarks] = useState({});

  const load = () => {
    setErr('');
    request('/api/v1/work/worksheets').then((j) => setSheets(j.worksheets))
      .catch((e) => { setErr(e.message); setSheets([]); });
    request('/api/v1/work/worksheets/assigned').then((j) => setMine(j.assignments || []))
      .catch(() => setMine([]));
    if (canManage(user)) {
      request('/api/v1/admin/users').then((j) => setUsers(j.users || [])).catch(() => {});
    }
  };
  useEffect(load, []);

  const create = async () => {
    if (!form.title.trim()) return;
    setBusy(true); setErr('');
    try {
      const cols = form.columns.split(',').map((s) => s.trim()).filter(Boolean)
        .map((name) => ({ name, type: 'text' }));
      await request('/api/v1/work/worksheets',
        { method: 'POST', body: { title: form.title, description: form.description, columns: cols } });
      setForm({ title: '', description: '', columns: 'task,owner,due' });
      load();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const uploadSheet = async () => {
    if (!file) return;
    setBusy(true); setErr('');
    try {
      const fd = new FormData();
      fd.append('f', file, file.name);
      fd.append('title', form.title || file.name);
      fd.append('description', form.description);
      const r = await fetch('/api/v1/work/worksheets/upload', {
        method: 'POST', headers: { Authorization: 'Bearer ' + tok }, body: fd });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
      setFile(null); load();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const doAssign = async (wid) => {
    const assignee_id = assign[wid];
    if (!assignee_id) return;
    setErr('');
    try {
      await request(`/api/v1/work/worksheets/${wid}/assign`,
        { method: 'POST', body: { assignee_id } });
      load();
    } catch (e) { setErr(e.message); }
  };

  const submit = async (a, ws) => {
    setErr('');
    try {
      const fields = {};
      (ws.columns || []).forEach((c) => {
        const v = (vals[a.id] || {})[c.name];
        if (v !== undefined && v !== '') fields[c.name] = v;
      });
      await request(`/api/v1/work/worksheets/assignments/${a.id}/submit`,
        { method: 'PATCH', body: { fields } });
      load();
    } catch (e) { setErr(e.message); }
  };

  const review = async (aid, status) => {
    setErr('');
    try {
      await request(`/api/v1/work/worksheets/assignments/${aid}/review`,
        { method: 'PATCH', body: { status, remarks: remarks[aid] || '' } });
      load();
    } catch (e) { setErr(e.message); }
  };

  const colOf = (wid) => (sheets || []).find((w) => w.id === wid)?.columns || [];
  const titleOf = (wid) => (sheets || []).find((w) => w.id === wid)?.title || wid;

  return (
    <div>
      <div className="page-head"><h2>{T(lang, 'worksheets')}</h2>
        <p className="mut">{T(lang, 'teamWork')} · {T(lang, 'contributions')}</p></div>
      <ErrorNote error={err} onRetry={load} />

      {canManage(user) && (
        <Card title={T(lang, 'worksheets') + ' — ' + T(lang, 'assign')}>
          <div className="row" style={{ marginBottom: 8 }}>
            <input className="input" placeholder="Title" value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })} style={{ minWidth: 200 }} />
            <input className="input" placeholder="Description" value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })} style={{ minWidth: 200 }} />
            <input className="input" title="columns (comma separated)" value={form.columns}
              onChange={(e) => setForm({ ...form, columns: e.target.value })} style={{ minWidth: 160 }} />
            <button className="btn" disabled={busy} onClick={create}>{T(lang, 'save')}</button>
          </div>
          <div className="row">
            <input type="file" accept=".csv,.xlsx" onChange={(e) => setFile(e.target.files?.[0] || null)} />
            <button className="btn btn-ghost" disabled={busy || !file} onClick={uploadSheet}>
              CSV/XLSX → {T(lang, 'worksheets')}
            </button>
          </div>
        </Card>
      )}

      <Card title={T(lang, 'worksheets')}>
        {sheets === null && <Spinner label={T(lang, 'loading')} />}
        {sheets !== null && sheets.length === 0 && <Empty title={T(lang, 'noResults')} />}
        {(sheets || []).map((w) => (
          <div key={w.id} className="row" style={{ justifyContent: 'space-between', width: '100%' }}>
            <span><b>{w.title}</b> <span className="mut">· {(w.columns || []).map((c) => c.name).join(', ')} · {w.department || ''}</span></span>
            <span className="row">
              {canManage(user) && (
                <React.Fragment>
                  <select className="select" value={assign[w.id] || ''}
                    onChange={(e) => setAssign({ ...assign, [w.id]: e.target.value })}>
                    <option value="">— {T(lang, 'assignee')} —</option>
                    {users.filter((u) => u.active).map((u) => (
                      <option key={u.id} value={u.id}>{u.username}</option>))}
                  </select>
                  <button className="btn btn-ghost btn-mini" onClick={() => doAssign(w.id)}>{T(lang, 'assign')}</button>
                </React.Fragment>
              )}
              <button className="btn btn-ghost btn-mini"
                onClick={() => onAskWork && onAskWork({ text: `Help me understand this worksheet: ${w.title}. Columns: ${(w.columns || []).map((c) => c.name).join(', ')}.` })}>
                {T(lang, 'askAIAboutWork')}
              </button>
            </span>
          </div>
        ))}
      </Card>

      <Card title={T(lang, 'myContributions')}>
        {mine.length === 0 && <Empty title={T(lang, 'noResults')} />}
        {mine.map((a) => (
          <div key={a.id} className="card" style={{ marginBottom: 10 }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <b>{titleOf(a.worksheet_id)}</b>
              <StateBadge state={a.status === 'submitted' ? 'COMPLETED' : a.status === 'approved' ? 'COMPLETED' : a.status === 'needs_correction' ? 'FAILED' : 'CREATED'} />
            </div>
            <div className="mut" style={{ fontSize: 12 }}>{a.status}</div>
            {colOf(a.worksheet_id).map((c) => (
              <label className="field" key={c.name}>{c.name}
                <input className="input" value={(vals[a.id] || {})[c.name] || ''}
                  disabled={['approved', 'reviewed'].includes(a.status)}
                  onChange={(e) => setVals({ ...vals, [a.id]: { ...(vals[a.id] || {}), [c.name]: e.target.value } })} />
              </label>
            ))}
            <div className="row">
              <button className="btn btn-mini" onClick={() => submit(a, { columns: colOf(a.worksheet_id) })}
                disabled={['approved', 'reviewed'].includes(a.status)}>{T(lang, 'submit')}</button>
              {canReview(user) && (
                <React.Fragment>
                  <input className="input" placeholder={T(lang, 'remarks')} value={remarks[a.id] || ''}
                    onChange={(e) => setRemarks({ ...remarks, [a.id]: e.target.value })} style={{ minWidth: 160 }} />
                  <button className="btn btn-ghost btn-mini" onClick={() => review(a.id, 'approved')}>{T(lang, 'approve')}</button>
                  <button className="btn btn-ghost btn-mini" onClick={() => review(a.id, 'needs_correction')}>{T(lang, 'needsCorrection')}</button>
                </React.Fragment>
              )}
            </div>
            {a.review_remarks && <p className="mut">{T(lang, 'managerReview')}: {a.review_remarks}</p>}
          </div>
        ))}
      </Card>
    </div>
  );
}
