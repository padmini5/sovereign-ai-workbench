import React, { useEffect, useRef, useState } from 'react';
import { request } from '../api.js';
import { T } from '../i18n.js';
import { Card, Spinner, ErrorNote, Empty } from '../ui.jsx';

/* Start Work: employee self-service work log.
   Create → progress → evidence (camera / upload / document) → submit.
   Statuses map to the existing work model: assigned (not started),
   in_progress, submitted, blocked. Review outcomes stay reviewer-owned. */

export const STATUS_LABEL = (s, lang) => ({
  assigned: T(lang, 'notStarted'), in_progress: T(lang, 'inProgress'),
  submitted: T(lang, 'requiresReview'), needs_correction: T(lang, 'correctionRequested'),
  reviewed: T(lang, 'reviewed'), approved: T(lang, 'completed'),
  blocked: T(lang, 'blocked'),
}[s] || s);

export default function StartWork({ tok, user, lang, onDone }) {
  const [tasks, setTasks] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ title: '', description: '' });
  const [remarks, setRemarks] = useState({});
  const camRef = useRef(null);
  const imgRef = useRef(null);
  const docRef = useRef(null);
  const [evTask, setEvTask] = useState(null);
  const [details, setDetails] = useState({});

  const load = () => {
    setErr('');
    request('/api/v1/work/tasks').then((j) => setTasks(j.tasks))
      .catch((e) => { setErr(e.message); setTasks([]); });
  };
  useEffect(load, []);

  const create = async () => {
    if (!form.title.trim() || busy) return;
    setBusy(true); setErr('');
    try {
      await request('/api/v1/work/my-work',
        { method: 'POST', body: { title: form.title, description: form.description } });
      setForm({ title: '', description: '' });
      load();
      if (onDone) onDone();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const setStatus = async (tid, status, progress) => {
    setErr('');
    try {
      await request(`/api/v1/work/tasks/${tid}`,
        { method: 'PATCH', body: { status, ...(progress != null ? { progress } : {}) } });
      load();
    } catch (e) { setErr(e.message); }
  };

  const toggleDetails = async (tid) => {
    if (details[tid]) {
      const n = { ...details }; delete n[tid]; setDetails(n); return;
    }
    setErr('');
    try {
      const j = await request(`/api/v1/work/evidence?task_id=${tid}`);
      setDetails({ ...details, [tid]: j.evidence || [] });
    } catch (e) { setErr(e.message); }
  };

  const sendEvidence = async (file) => {
    if (!file || !evTask) return;
    setErr(''); setBusy(true);
    try {
      const fd = new FormData();
      fd.append('f', file, file.name);
      fd.append('task_id', evTask);
      fd.append('note', remarks[evTask] || '');
      const r = await fetch('/api/v1/work/evidence', {
        method: 'POST', headers: { Authorization: 'Bearer ' + tok }, body: fd });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
      setEvTask(null);
      load();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const mine = (tasks || []).filter((t) => t.assignee_id === user.id);
  const open = mine.filter((t) => !['approved', 'reviewed'].includes(t.status));
  const done = mine.filter((t) => ['approved', 'reviewed'].includes(t.status));

  return (
    <Card title={T(lang, 'todayWork')}>
      <ErrorNote error={err} onRetry={load} />
      <div className="grid-2">
        <label className="field">{T(lang, 'workTitle')}
          <input className="input" value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            placeholder={T(lang, 'workTitle')} />
        </label>
        <label className="field">{T(lang, 'workDesc')}
          <input className="input" value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            placeholder={T(lang, 'workDesc')} />
        </label>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn" disabled={busy || !form.title.trim()} onClick={create}>
          {busy ? <Spinner label="…" /> : T(lang, 'startWork')}
        </button>
      </div>

      <div style={{ marginTop: 10 }}>
        {tasks === null && <Spinner label={T(lang, 'loading')} />}
        {tasks !== null && open.length === 0 &&
          <Empty title={T(lang, 'noRecordsYet')} hint={T(lang, 'noOpenWork')} />}
        {open.map((t) => {
          const det = details[t.id];
          const btns = [];
          if (t.status === 'assigned') {
            btns.push(<button key="start" className="btn btn-ghost btn-mini"
              onClick={() => setStatus(t.id, 'in_progress', Math.max(t.progress, 10))}>{T(lang, 'startWork')}</button>);
          }
          if (['needs_correction', 'blocked'].includes(t.status)) {
            btns.push(<button key="resume" className="btn btn-ghost btn-mini"
              onClick={() => setStatus(t.id, 'in_progress')}>{T(lang, 'inProgress')}</button>);
          }
          if (['assigned', 'in_progress', 'needs_correction', 'blocked'].includes(t.status)) {
            btns.push(<button key="sub" className="btn btn-ghost btn-mini"
              onClick={() => setStatus(t.id, 'submitted', 100)}>{T(lang, 'submitReview')}</button>);
          }
          if (t.status === 'submitted') {
            btns.push(<button key="cont" className="btn btn-ghost btn-mini"
              onClick={() => setStatus(t.id, 'in_progress')}>{T(lang, 'inProgress')}</button>);
          }
          if (['assigned', 'in_progress', 'needs_correction'].includes(t.status)) {
            btns.push(<button key="blk" className="btn btn-ghost btn-mini"
              onClick={() => setStatus(t.id, 'blocked')}>{T(lang, 'blocked')}</button>);
          }
          return (
            <div key={t.id} className="card" style={{ marginBottom: 8 }}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <b>{t.title}</b>
                <span className="badge badge-slate">{STATUS_LABEL(t.status, lang)} · {t.progress}%</span>
              </div>
              <div className="row mut" style={{ fontSize: 12, flexWrap: 'wrap' }}>
                <span>{T(lang, 'priority')}: {t.priority || 'medium'}</span>
                {t.start_date && <span>· {T(lang, 'startDate')}: {t.start_date}</span>}
                {t.due_date && <span>· {T(lang, 'dueDate')}: {t.due_date}</span>}
                {t.created_by_name && <span>· {T(lang, 'assignedBy')}: {t.created_by_name}</span>}
                {String(t.title).startsWith('DEMO — ') &&
                  <span className="badge badge-amber">{T(lang, 'demoBadge')}</span>}
              </div>
              {t.instructions && <p className="mut">{t.instructions}</p>}
              {t.status === 'needs_correction' && t.review_remarks && (
                <p className="warn" style={{ fontSize: 13 }}>
                  {T(lang, 'reviewRemarks')}: {t.review_remarks}</p>)}
              <label className="field">{T(lang, 'remarks')}
                <input className="input" value={remarks[t.id] || ''}
                  onChange={(e) => setRemarks({ ...remarks, [t.id]: e.target.value })}
                  placeholder={T(lang, 'remarks')} />
              </label>
              <div className="row">
                {btns}
                <button className="btn btn-ghost btn-mini" onClick={() => toggleDetails(t.id)}>
                  {det ? T(lang, 'hideDetails') : T(lang, 'viewDetails')}</button>
                <button className="btn btn-ghost btn-mini" onClick={() => { setEvTask(t.id); camRef.current?.click(); }}>{T(lang, 'takePhoto')}</button>
                <button className="btn btn-ghost btn-mini" onClick={() => { setEvTask(t.id); imgRef.current?.click(); }}>{T(lang, 'uploadFromDevice')}</button>
                <button className="btn btn-ghost btn-mini" onClick={() => { setEvTask(t.id); docRef.current?.click(); }}>{T(lang, 'uploadDoc')}</button>
              </div>
              {det && (
                <div style={{ marginTop: 6 }}>
                  <h4 className="mut" style={{ fontSize: 12 }}>{T(lang, 'evidence')}</h4>
                  {det.length === 0 && <p className="mut" style={{ fontSize: 13 }}>{T(lang, 'noRecordsYet')}</p>}
                  {det.map((e) => (
                    <div key={e.id} className="mut" style={{ fontSize: 12 }}>
                      {e.filename || e.file_id || e.id}
                      {e.note ? ` — ${e.note}` : ''}
                    </div>))}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {done.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <h4 className="mut">{T(lang, 'completed')}</h4>
          {done.map((t) => (
            <div key={t.id} className="row" style={{ justifyContent: 'space-between', width: '100%', fontSize: 13 }}>
              <span>{t.title}</span>
              <span className="badge badge-green">{STATUS_LABEL(t.status, lang)}</span>
            </div>))}
        </div>
      )}
      <input ref={camRef} type="file" style={{ display: 'none' }} accept="image/*" capture="environment"
        onChange={(e) => { sendEvidence(e.target.files?.[0]); e.target.value = ''; }} />
      <input ref={imgRef} type="file" style={{ display: 'none' }} accept=".jpg,.jpeg,.png"
        onChange={(e) => { sendEvidence(e.target.files?.[0]); e.target.value = ''; }} />
      <input ref={docRef} type="file" style={{ display: 'none' }} accept=".pdf,.docx,.txt,.csv,.xlsx"
        onChange={(e) => { sendEvidence(e.target.files?.[0]); e.target.value = ''; }} />
      <p className="mut">{T(lang, 'cameraNote')}</p>
    </Card>
  );
}
