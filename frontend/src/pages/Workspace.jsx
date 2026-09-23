import React, { useEffect, useState } from 'react';
import { request } from '../api.js';
import { T } from '../i18n.js';
import { Card, Spinner, ErrorNote, Empty, LineChart } from '../ui.jsx';
import StartWork, { STATUS_LABEL } from './StartWork.jsx';
import AssignWork from './AssignWork.jsx';
import { NAV, canSee } from '../nav.js';

/* Role-adapted workspace landing: employees see their own attendance/work/
   evidence/performance; managers+admins see team aggregates. Reviewers get
   a review queue. All data comes from scoped endpoints. */

const isManagerLike = (u) => ['ADMIN', 'MANAGER', 'approving_manager'].includes(u.role);

function Tile({ k, v, d }) {
  return (
    <div className="tile">
      <div className="k">{k}</div>
      <div className="v" style={{ fontSize: 19 }}>{v}</div>
      {d ? <div className="d">{d}</div> : null}
    </div>
  );
}

function PerfBlock({ perf, lang, weights }) {
  if (!perf || perf.overall == null) {
    return <p className="mut">{T(lang, 'notEnoughData')}{perf?.missing?.length ? ` (${perf.missing.join(', ')})` : ''}</p>;
  }
  return (
    <div>
      <div style={{ fontSize: 30, fontWeight: 700 }}>{Math.round(perf.overall * 100)} <span style={{ fontSize: 14 }}>/ 100</span></div>
      <table className="tbl"><tbody>
        {Object.entries(perf.parts || {}).map(([k, v]) => (
          <tr key={k}><td className="mut">{k.replace(/_/g, ' ')}</td>
            <td>{v == null ? T(lang, 'notEnoughData') : `${Math.round(v * 100)}`}</td>
            <td className="mut">{weights && weights[k] != null ? `${Math.round(weights[k] * 100)}%` : ''}</td></tr>
        ))}
      </tbody></table>
      <p className="mut">{T(lang, 'recordedDataNote')}</p>
    </div>
  );
}

function greeting(lang) {
  const h = new Date().getHours();
  return T(lang, h < 12 ? 'goodMorning' : h < 17 ? 'goodAfternoon' : 'goodEvening');
}

function EmployeeView({ tok, user, lang, onNav, onAskWork }) {
  const [me, setMe] = useState(null);
  const [prof, setProf] = useState(null);
  const [sum, setSum] = useState(null);
  const [sched, setSched] = useState(null);
  const [crit, setCrit] = useState(null);
  const [acts, setActs] = useState(null);
  const [dev, setDev] = useState(null);
  const [err, setErr] = useState('');
  const [ident, setIdent] = useState(null);
  useEffect(() => {
    request('/api/v1/work/me').then(setMe).catch((e) => setErr(e.message));
    request('/api/v1/employees/me').then(setProf).catch(() => setProf(null));
    request('/api/v1/work/attendance/summary').then(setSum).catch(() => setSum(null));
    request('/api/v1/work/schedule').then(setSched).catch(() => setSched(null));
    request('/api/v1/analytics/criteria').then(setCrit).catch(() => setCrit(null));
    request('/api/v1/work/activity').then(setActs).catch(() => setActs(null));
    request('/api/v1/devices/status').then(setDev).catch(() => setDev(null));
    request('/api/v1/identity/status').then(setIdent).catch(() => setIdent(null));
  }, []);
  if (err) return <ErrorNote error={err} />;
  if (!me) return <Spinner label={T(lang, 'loading')} />;
  const cur = sum?.current;
  const tasks = [...(me.tasks_open || []), ...(me.tasks_recent || [])].slice(0, 5);
  const first = (prof?.first_name || user.username || '').split(' ')[0];
  const weights = crit?.weights || {};
  return (
    <div>
      <div className="page-head">
        <h2>{greeting(lang)}, {first}</h2>
        <span className="pill pill-role">{user.role}</span>
        {ident && (
          <span className={'badge ' + (ident.verified ? 'badge-green' : 'badge-amber')}>
            {ident.verified ? T(lang, 'verified') : T(lang, 'verificationRequired')}
          </span>
        )}
      </div>

      <div className="grid-2">
        <Card title={`${T(lang, 'empId')}: ${prof?.employee_id || user.id}`}>
          <table className="tbl"><tbody>
            <tr><td className="mut">{T(lang, 'department')}</td><td>{prof?.department || '—'}</td></tr>
            <tr><td className="mut">{T(lang, 'team')}</td><td>{prof?.team || me.team_name || '—'}</td></tr>
            <tr><td className="mut">{T(lang, 'designation')}</td><td>{prof?.designation || '—'}</td></tr>
            <tr><td className="mut">{T(lang, 'status')}</td><td>
              <span className="badge badge-green">{prof?.emp_status || T(lang, 'active')}</span></td></tr>
          </tbody></table>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn btn-ghost btn-mini" onClick={() => onNav('profile')}>{T(lang, 'myProfile')}</button>
          </div>
        </Card>
        <Card title={T(lang, 'todaySchedule')}>
          {!sched && <Spinner label={T(lang, 'loading')} />}
          {sched && (
            <div>
              <div style={{ fontSize: 22, fontWeight: 700 }}>{sched.start} – {sched.end}</div>
              <p className="mut">{T(lang, 'shift')}: {sched.shift} · {T(lang, 'nextWorkingDay')}: {sched.next_working_day}</p>
              <button className="btn btn-ghost btn-mini" onClick={() => onNav('schedule')}>{T(lang, 'viewSchedule')}</button>
            </div>
          )}
        </Card>
      </div>

      <div className="grid-3">
        <Tile k={T(lang, 'presentDays')} v={cur ? `${cur.present}` : '—'}
          d={cur ? (cur.has_records ? `${T(lang, 'workingDays')}: ${cur.working_days}` : T(lang, 'noRecordsYet')) : ''} />
        <Tile k={T(lang, 'attendancePct')} v={cur && cur.attendance_pct != null && cur.has_records ? `${cur.attendance_pct}%` : '—'}
          d={cur ? `${T(lang, 'presentToday')}: ${cur.today_status ? T(lang, 'yes') : T(lang, 'no')}` : ''} />
        <Tile k={T(lang, 'monthlyPoints')}
          v={me.performance?.overall != null ? `${Math.round(me.performance.overall * 100)} / 100` : T(lang, 'notEnoughData')} />
        <Tile k={T(lang, 'workCompleted')} v={`${me.tasks_done_30d || 0}`} d={`${T(lang, 'pending')}: ${me.tasks_open?.length || 0}`} />
        <Tile k={T(lang, 'requiresReview')}
          v={`${(me.tasks_open || []).filter((t) => ['submitted', 'needs_correction'].includes(t.status)).length}`}
          d={`${T(lang, 'teamProgress')}: ${me.team_completion_pct != null ? me.team_completion_pct + '%' : '—'}`} />
        <Tile k={T(lang, 'deviceSync')} v={dev ? (dev.connected ? (dev.last_sync_date || '—') : T(lang, 'no')) : '—'}
          d={dev ? (dev.connected ? `${T(lang, 'lastSync')}: ${dev.last_sync_date || '—'}` : T(lang, 'deviceNotConnected')) : ''} />
      </div>

      <div className="grid-2">
        <Card title={T(lang, 'monthlyPoints')}>
          <PerfBlock perf={me.performance} lang={lang} weights={weights} />
        </Card>
        <Card title={T(lang, 'recentActivity')}>
          {!acts && <Spinner label={T(lang, 'loading')} />}
          {acts && acts.items.length === 0 && <Empty title={T(lang, 'noRecordsYet')} />}
          {(acts?.items || []).slice(0, 6).map((a, i) => (
            <p key={i} className="mut">• {a.text}</p>
          ))}
        </Card>
      </div>

      <div className="grid-2">
        <Card title={T(lang, 'recentWork')}>
          {!tasks.length && <Empty title={T(lang, 'noResults')} />}
          {tasks.map((t) => (
            <div key={t.id} className="row" style={{ justifyContent: 'space-between', width: '100%' }}>
              <span>{t.title} <span className="mut">· {STATUS_LABEL(t.status, lang)} · {t.progress}%</span></span>
              <button className="btn btn-ghost btn-mini"
                onClick={() => onAskWork({ text: `Explain this task and what remains: ${t.title}. Status ${t.status}, progress ${t.progress}%.` })}>
                {T(lang, 'askAIAboutWork')}
              </button>
            </div>
          ))}
        </Card>
        <Card title={T(lang, 'quickActions')}>
          <div className="row">
            <button className="btn" onClick={() => onNav('assistant')}>{T(lang, 'askAI')}</button>
            <button className="btn btn-ghost" onClick={() => onNav('documents')}>{T(lang, 'uploadEvidence')}</button>
            <button className="btn btn-ghost" onClick={() => onNav('attendance')}>{T(lang, 'viewAttendance')}</button>
            <button className="btn btn-ghost" onClick={() => onNav('worksheets')}>{T(lang, 'viewMyWork')}</button>
            <button className="btn btn-ghost" onClick={() => onNav('reports')}>{T(lang, 'viewMyReport')}</button>
            <button className="btn btn-ghost" onClick={() => onNav('profile')}>{T(lang, 'myProfile')}</button>
          </div>
        </Card>
      </div>
      {me.feedback_recent?.length > 0 && (
        <Card title={T(lang, 'recentFeedback')}>
          {me.feedback_recent.map((f) => (
            <p key={f.id} className="mut">“{f.text}”{f.rating ? ` — ${f.rating}/5` : ''}</p>
          ))}
        </Card>
      )}
      <StartWork tok={tok} user={user} lang={lang} />
    </div>
  );
}

function ManagerView({ user, lang, onNav, onAskWork }) {
  const [ov, setOv] = useState(null);
  const [trend, setTrend] = useState(null);
  const [err, setErr] = useState('');
  const [reviewRemarks, setReviewRemarks] = useState({});
  const load = () => {
    setErr('');
    request('/api/v1/analytics/overview').then(setOv).catch((e) => setErr(e.message));
    const now = new Date();
    const from = new Date(now.getFullYear(), now.getMonth() - 2, 1).toISOString().slice(0, 10);
    request(`/api/v1/analytics/trends?date_from=${from}&bucket=week`).then(setTrend).catch(() => setTrend(null));
  };
  useEffect(load, []);
  const review = async (tid, status) => {
    setErr('');
    try {
      await request(`/api/v1/work/tasks/${tid}`, { method: 'PATCH',
        body: { status, review_remarks: reviewRemarks[tid] || '' } });
      load();
    } catch (e) { setErr(e.message); }
  };
  if (err) return <ErrorNote error={err} />;
  if (!ov) return <Spinner label={T(lang, 'loading')} />;
  const pts = (trend?.points || []).map((p) => ({ label: p.bucket, value: p.completed }));
  return (
    <div>
      <div className="page-head">
        <h2>{T(lang, 'welcome')}, {user.username}</h2>
        <span className="pill pill-role">{user.role}</span>
      </div>
      <div className="grid-3">
        <Tile k={T(lang, 'totalEmployees')} v={ov.employees ?? '—'} />
        <Tile k={T(lang, 'presentToday')} v={ov.present_today ?? '—'} />
        <Tile k={T(lang, 'lateToday')} v={ov.late_today ?? '—'} />
        <Tile k={T(lang, 'absentToday')} v={ov.absent_today ?? '—'} />
        <Tile k={T(lang, 'activeTeams')} v={ov.active_teams ?? '—'} />
        <Tile k={T(lang, 'workInProgress')} v={ov.work_in_progress ?? 0} />
        <Tile k={T(lang, 'completedWork')} v={ov.completed_work ?? 0} />
        <Tile k={T(lang, 'pendingReview')} v={ov.pending_review ?? 0} />
        <Tile k={T(lang, 'avgPerformance')} v={ov.avg_performance != null ? `${Math.round(ov.avg_performance * 100)} / 100` : '—'} />
        <Tile k={T(lang, 'issuesBlockers')} v={ov.blocked_count ?? 0} />
        {ov.financial ? (
          <React.Fragment>
            <Tile k={T(lang, 'revenue')} v={ov.financial.revenue}
              d={ov.financial.demo ? T(lang, 'demoBadge') : ''} />
            <Tile k={T(lang, 'expenses')} v={ov.financial.expenses}
              d={ov.financial.demo ? T(lang, 'demoBadge') : ''} />
            <Tile k={T(lang, 'profitLoss')} v={ov.financial.profit}
              d={ov.financial.note || ''} />
          </React.Fragment>
        ) : (
          <Tile k={T(lang, 'profitLoss')} v="—" d={T(lang, 'noFinancialData')} />
        )}
      </div>
      <div className="grid-2">
        <Card title={T(lang, 'trend')}>
          {pts.length ? <LineChart points={pts} label="completed per week" /> : <Empty title={T(lang, 'noResults')} />}
        </Card>
        <Card title={T(lang, 'pendingReview')}>
          {!(ov.pending_items || []).length && <Empty title={T(lang, 'noResults')} />}
          {(ov.pending_items || []).slice(0, 6).map((t) => (
            <div key={t.id} style={{ marginBottom: 8 }}>
              <div className="row" style={{ justifyContent: 'space-between', width: '100%' }}>
                <span>{t.title} <span className="mut">· {t.assignee} · {STATUS_LABEL(t.status, lang)}</span></span>
                <button className="btn btn-ghost btn-mini"
                  onClick={() => onAskWork({ text: `Summarize review status for work: ${t.title} (${t.status}).` })}>
                  {T(lang, 'askAIAboutWork')}
                </button>
              </div>
              <div className="row">
                <input className="input" style={{ flex: 1, minWidth: 140 }}
                  placeholder={T(lang, 'reviewRemarks')}
                  value={reviewRemarks[t.id] || ''}
                  onChange={(e) => setReviewRemarks({ ...reviewRemarks, [t.id]: e.target.value })} />
                <button className="btn btn-ghost btn-mini"
                  onClick={() => review(t.id, 'approved')}>{T(lang, 'approve')}</button>
                <button className="btn btn-ghost btn-mini"
                  onClick={() => review(t.id, 'needs_correction')}>{T(lang, 'requestCorrection')}</button>
                <button className="btn btn-ghost btn-mini"
                  onClick={() => review(t.id, 'reviewed')}>{T(lang, 'reviewed')}</button>
              </div>
            </div>
          ))}
        </Card>
      </div>
      <AssignWork user={user} lang={lang} onDone={load} />
      <Card title={T(lang, 'quickActions')}>
        <div className="row">
          <button className="btn" onClick={() => onNav('attendance')}>{T(lang, 'attendance')}</button>
          <button className="btn btn-ghost" onClick={() => onNav('worksheets')}>{T(lang, 'teamWork')}</button>
          <button className="btn btn-ghost" onClick={() => onNav('performance')}>{T(lang, 'performance')}</button>
          <button className="btn btn-ghost" onClick={() => onNav('reports')}>{T(lang, 'reportsTitle')}</button>
        </div>
      </Card>
    </div>
  );
}

function ReviewerView({ tok, user, lang, onNav, onAskWork }) {
  /* Reviewer cannot read org-wide analytics (ANALYTICS_READ is intentionally
     not granted to this role — see tests). This view uses only endpoints the
     reviewer is entitled to: /work/me, /work/tasks, attendance summary,
     schedule, criteria — plus quick links to Reports and Audit. */
  const [me, setMe] = useState(null);
  const [tasks, setTasks] = useState(null);
  const [sum, setSum] = useState(null);
  const [sched, setSched] = useState(null);
  const [err, setErr] = useState('');
  const load = () => {
    setErr('');
    request('/api/v1/work/me').then(setMe).catch((e) => setErr(e.message));
    request('/api/v1/work/tasks').then((j) => setTasks(j.tasks || []))
      .catch((e) => { setErr(e.message); setTasks([]); });
    request('/api/v1/work/attendance/summary').then(setSum).catch(() => setSum(null));
    request('/api/v1/work/schedule').then(setSched).catch(() => setSched(null));
  };
  useEffect(load, []);
  if (err) return <ErrorNote error={err} onRetry={load} />;
  if (!me || !tasks) return <Spinner label={T(lang, 'loading')} />;
  const open = tasks.filter((t) => !['approved', 'reviewed'].includes(t.status));
  const cur = sum?.current;
  const first = (user.username || '').split(' ')[0];
  const links = [
    { id: 'reports', label: T(lang, 'reportsTitle') },
    { id: 'audit', label: T(lang, 'auditLog') },
    { id: 'attendance', label: T(lang, 'attendance') },
    { id: 'schedule', label: T(lang, 'schedule') },
    { id: 'worksheets', label: T(lang, 'teamWork') },
    { id: 'performance', label: T(lang, 'performance') },
    { id: 'documents', label: T(lang, 'documents') },
  ].filter((l) => canSee(NAV.find((p) => p.id === l.id) || {}, user));
  return (
    <div>
      <div className="page-head">
        <h2>{greeting(lang)}, {first}</h2>
        <span className="pill pill-role">{user.role}</span>
      </div>
      <div className="grid-3">
        <Tile k={T(lang, 'requiresReview')}
          v={tasks.filter((t) => t.status === 'submitted').length} />
        <Tile k={T(lang, 'workInProgress')}
          v={open.filter((t) => t.status === 'in_progress').length} />
        <Tile k={T(lang, 'completedWork')}
          v={tasks.filter((t) => ['approved', 'reviewed'].includes(t.status)).length} />
        <Tile k={T(lang, 'attendancePct')}
          v={cur && cur.attendance_pct != null && cur.has_records ? `${cur.attendance_pct}%` : '—'} />
        <Tile k={T(lang, 'todaySchedule')} v={sched ? `${sched.start} – ${sched.end}` : '—'}
          d={sched ? sched.shift : ''} />
        <Tile k={T(lang, 'monthlyPoints')}
          v={me.performance?.overall != null ? `${Math.round(me.performance.overall * 100)} / 100` : T(lang, 'notEnoughData')} />
      </div>
      <StartWork tok={tok} user={user} lang={lang} />
      <Card title={T(lang, 'quickActions')}>
        <div className="row">
          {links.map((l) => (
            <button key={l.id} className="btn btn-ghost" onClick={() => onNav(l.id)}>{l.label}</button>
          ))}
          <button className="btn btn-ghost"
            onClick={() => onAskWork({ text: 'Help me review my assigned work.' })}>
            {T(lang, 'askAI')}
          </button>
        </div>
      </Card>
    </div>
  );
}

export default function Workspace(props) {
  if (isManagerLike(props.user)) return <ManagerView {...props} />;
  if (props.user.role === 'REVIEWER') return <ReviewerView {...props} />;
  return <EmployeeView {...props} />;
}
