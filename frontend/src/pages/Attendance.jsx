import React, { useEffect, useState } from 'react';
import { request } from '../api.js';
import { T } from '../i18n.js';
import { Card, Spinner, ErrorNote, Empty } from '../ui.jsx';

/* Attendance: summary cards (recorded data only), device sync status,
   own history for everyone; team table + date filters for managers;
   device simulator (ADMIN/ATTENDANCE_MANAGE) driving the same ingest path
   as a physical device. */

const canSimulate = (u) => ['ADMIN', 'MANAGER', 'approving_manager'].includes(u.role)
  && (u.permissions || []).includes('ATTENDANCE_MANAGE');

function SumCard({ s, lang }) {
  if (!s) return <Spinner label={T(lang, 'loading')} />;
  return (
    <div>
      <div className="grid-3">
        <div className="tile"><div className="k">{T(lang, 'presentDays')}</div>
          <div className="v" style={{ fontSize: 22 }}>{s.present}</div></div>
        <div className="tile"><div className="k">{T(lang, 'absentDays')}</div>
          <div className="v" style={{ fontSize: 22 }}>{s.absent}</div></div>
        <div className="tile"><div className="k">{T(lang, 'workingDays')}</div>
          <div className="v" style={{ fontSize: 22 }}>{s.working_days}</div></div>
        <div className="tile"><div className="k">{T(lang, 'attendancePct')}</div>
          <div className="v" style={{ fontSize: 22 }}>
            {s.has_records && s.attendance_pct != null ? `${s.attendance_pct}%` : '—'}</div></div>
        <div className="tile"><div className="k">{T(lang, 'presentToday')}</div>
          <div className="v" style={{ fontSize: 22 }}>
            {s.today_status ? T(lang, 'yes') : T(lang, 'no')}</div></div>
      </div>
      {!s.has_records && <p className="mut">{T(lang, 'noRecordsYet')}</p>}
    </div>
  );
}

export default function Attendance({ tok, user, lang }) {
  const [rows, setRows] = useState(null);
  const [sum, setSum] = useState(null);
  const [dev, setDev] = useState(null);
  const [users, setUsers] = useState([]);
  const [f, setF] = useState({ user_id: '', date_from: '', date_to: '' });
  const [err, setErr] = useState('');
  const [sim, setSim] = useState({ username: '', event: 'CHECK_IN' });
  const [simMsg, setSimMsg] = useState('');

  const load = () => {
    setErr('');
    const q = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
    request('/api/v1/work/attendance' + (q ? '?' + q : '')).then((j) => setRows(j.rows))
      .catch((e) => { setErr(e.message); setRows([]); });
    request('/api/v1/work/attendance/summary').then(setSum).catch(() => setSum(null));
    request('/api/v1/devices/status').then(setDev).catch(() => setDev(null));
    if (canSimulate(user)) {
      request('/api/v1/admin/users').then((j) => setUsers(j.users || [])).catch(() => {});
    }
  };
  useEffect(load, []);

  const simulate = async () => {
    setSimMsg(''); setErr('');
    if (!sim.username) { setSimMsg('Pick an employee first.'); return; }
    try {
      const j = await request('/api/v1/admin/devices/simulate',
        { method: 'POST', body: { username: sim.username, event: sim.event } });
      setSimMsg(`${sim.event === 'CHECK_IN' ? T(lang, 'checkIn') : T(lang, 'checkOut')}: ${j.employee} · ${j.date} (device event recorded)`);
      load();
    } catch (e) { setErr(e.message); }
  };

  const nameOf = (id) => (users.find((u) => u.id === id)?.username) || id;

  return (
    <div>
      <div className="page-head"><h2>{T(lang, 'attendance')}</h2></div>
      <div className="grid-2">
        <Card title={`${T(lang, 'attendance')} — ${sum?.current?.month || ''}`}>
          <SumCard s={sum?.current} lang={lang} />
        </Card>
        <Card title={`${T(lang, 'attendance')} — ${sum?.previous?.month || ''}`}>
          <SumCard s={sum?.previous} lang={lang} />
        </Card>
      </div>
      <Card title={T(lang, 'deviceSync')}>
        {!dev && <Spinner label={T(lang, 'loading')} />}
        {dev && (
          <p className="mut">
            {dev.connected
              ? `${T(lang, 'lastSync')}: ${dev.last_sync_date || T(lang, 'noRecordsYet')}`
              : T(lang, 'deviceNotConnected')}
          </p>
        )}
      </Card>
      {canSimulate(user) && (
        <Card title={T(lang, 'deviceSimulator')}>
          <div className="row">
            <select className="select" value={sim.username}
              onChange={(e) => setSim({ ...sim, username: e.target.value })}>
              <option value="">— employee —</option>
              {users.filter((u) => u.active).map((u) => (
                <option key={u.id} value={u.username}>{u.username} ({u.role})</option>))}
            </select>
            <select className="select" value={sim.event}
              onChange={(e) => setSim({ ...sim, event: e.target.value })}>
              <option value="CHECK_IN">{T(lang, 'checkIn')}</option>
              <option value="CHECK_OUT">{T(lang, 'checkOut')}</option>
            </select>
            <button className="btn" onClick={simulate}>{T(lang, 'simulate')}</button>
          </div>
          {simMsg && <p className="ok" style={{ fontSize: 13 }}>{simMsg}</p>}
        </Card>
      )}
      <Card title={T(lang, 'attendance')}>
        <div className="row" style={{ marginBottom: 10 }}>
          {canSimulate(user) && (
            <input className="input" placeholder="user id (blank = team)" value={f.user_id}
              onChange={(e) => setF({ ...f, user_id: e.target.value })} style={{ maxWidth: 220 }} />
          )}
          <input className="input" type="date" value={f.date_from}
            onChange={(e) => setF({ ...f, date_from: e.target.value })} />
          <input className="input" type="date" value={f.date_to}
            onChange={(e) => setF({ ...f, date_to: e.target.value })} />
          <button className="btn btn-ghost" onClick={load}>{T(lang, 'search')}</button>
        </div>
        <ErrorNote error={err} onRetry={load} />
        {rows === null && <Spinner label={T(lang, 'loading')} />}
        {rows !== null && rows.length === 0 && <Empty title={T(lang, 'noResults')} />}
        {rows && rows.length > 0 && (
          <div className="tbl-wrap"><table className="tbl">
            <thead><tr><th>{T(lang, 'employees')}</th><th>{T(lang, 'date')}</th>
              <th>{T(lang, 'status')}</th><th>{T(lang, 'detail')}</th></tr></thead>
            <tbody>{rows.slice(0, 100).map((r) => (
              <tr key={r.id}>
                <td>{nameOf(r.user_id)}</td><td>{r.date}</td>
                <td><span className={'badge ' + (r.status === 'present' ? 'badge-green' : r.status === 'late' ? 'badge-amber' : 'badge-slate')}>{r.status}</span></td>
                <td className="mut">{r.marked_by || ''} {r.note || ''}</td>
              </tr>))}</tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}
