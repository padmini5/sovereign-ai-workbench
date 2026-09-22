import React, { useEffect, useState } from 'react';
import { request } from '../api.js';
import { T } from '../i18n.js';
import { Card, Spinner, ErrorNote } from '../ui.jsx';

/* Manager "Assign Work": create a task for a team member (POST /work/tasks)
   with priority/dates, optionally attach a reference file
   (POST /work/evidence). The server enforces WORK_MANAGE and department
   scope — the UI only pre-fills and reports server decisions honestly. */

const PRIORITIES = ['low', 'medium', 'high', 'urgent'];

export default function AssignWork({ user, lang, presetAssignee = '', onDone }) {
  const [emps, setEmps] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [file, setFile] = useState(null);
  const [form, setForm] = useState({
    assignee_id: presetAssignee, title: '', instructions: '',
    priority: 'medium', start_date: '', due_date: '',
  });

  useEffect(() => {
    request('/api/v1/employees').then((j) => setEmps(j.employees || []))
      .catch((e) => { setErr(e.message); setEmps([]); });
  }, []);

  useEffect(() => {
    if (presetAssignee) setForm((f) => ({ ...f, assignee_id: presetAssignee }));
  }, [presetAssignee]);

  const submit = async () => {
    if (!form.assignee_id || !form.title.trim() || busy) return;
    setBusy(true); setErr(''); setMsg('');
    try {
      const t = await request('/api/v1/work/tasks', { method: 'POST', body: form });
      if (file) {
        const fd = new FormData();
        fd.append('f', file, file.name);
        fd.append('task_id', t.id);
        fd.append('note', 'Reference file attached at assignment.');
        await request('/api/v1/work/evidence', { method: 'POST', body: fd });
      }
      setForm({ assignee_id: '', title: '', instructions: '',
        priority: 'medium', start_date: '', due_date: '' });
      setFile(null);
      setMsg('Assigned.');
      if (onDone) onDone();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  return (
    <Card title={T(lang, 'assignWork')}>
      <ErrorNote error={err} />
      {msg && <p className="ok" style={{ fontSize: 13 }}>{msg}</p>}
      {emps === null && <Spinner label={T(lang, 'loading')} />}
      {emps !== null && (
        <div className="grid-2">
          <label className="field">{T(lang, 'assignee')}
            <select className="select" value={form.assignee_id}
              onChange={(e) => setForm({ ...form, assignee_id: e.target.value })}>
              <option value="">—</option>
              {emps.map((e) => (
                <option key={e.id} value={e.id}>{e.full_name || e.username}</option>))}
            </select>
          </label>
          <label className="field">{T(lang, 'workTitle')}
            <input className="input" value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder={T(lang, 'workTitle')} />
          </label>
          <label className="field" style={{ gridColumn: '1 / -1' }}>{T(lang, 'instructions')}
            <textarea className="input" rows={2} value={form.instructions}
              onChange={(e) => setForm({ ...form, instructions: e.target.value })}
              placeholder={T(lang, 'instructions')} />
          </label>
          <label className="field">{T(lang, 'priority')}
            <select className="select" value={form.priority}
              onChange={(e) => setForm({ ...form, priority: e.target.value })}>
              {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="field">{T(lang, 'startDate')}
            <input className="input" type="date" value={form.start_date}
              onChange={(e) => setForm({ ...form, start_date: e.target.value })} />
          </label>
          <label className="field">{T(lang, 'dueDate')}
            <input className="input" type="date" value={form.due_date}
              onChange={(e) => setForm({ ...form, due_date: e.target.value })} />
          </label>
          <label className="field">{T(lang, 'attachFile')}
            <input type="file" accept=".pdf,.docx,.txt,.csv,.xlsx,.png,.jpg,.jpeg"
              onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </label>
        </div>
      )}
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn" disabled={busy || !form.assignee_id || !form.title.trim()}
          onClick={submit}>
          {busy ? <Spinner label="…" /> : T(lang, 'assignWork')}
        </button>
      </div>
    </Card>
  );
}
