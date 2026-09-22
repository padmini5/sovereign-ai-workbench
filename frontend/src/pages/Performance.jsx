import React, { useEffect, useState } from 'react';
import { request } from '../api.js';
import { T } from '../i18n.js';
import { Card, Spinner, ErrorNote, Empty, LineChart, BarChart } from '../ui.jsx';

/* Performance: own transparent score + breakdown for everyone; team table
   and completion/attendance trends for analytics roles. Scores are recorded
   data only — missing inputs are labeled, never invented. Managers review
   evidence; the system never makes employment decisions. */

const isManagerLike = (u) => ['ADMIN', 'MANAGER', 'approving_manager'].includes(u.role);

function Score({ perf, lang }) {
  if (!perf || perf.overall == null) {
    return <p className="mut">{T(lang, 'insufficientData')}{perf?.missing?.length ? ` (${perf.missing.join(', ')})` : ''}</p>;
  }
  return (
    <div>
      <div style={{ fontSize: 32, fontWeight: 700 }}>{Math.round(perf.overall * 100)} <span style={{ fontSize: 14 }}>/ 100</span></div>
      <table className="tbl"><tbody>
        {Object.entries(perf.parts || {}).map(([k, v]) => (
          <tr key={k}><td className="mut">{k.replace(/_/g, ' ')}</td>
            <td>{v == null ? T(lang, 'insufficientData') : Math.round(v * 100)}</td></tr>
        ))}
      </tbody></table>
      <p className="mut">{T(lang, 'aiAssisted')} · {T(lang, 'criteriaBased')}</p>
    </div>
  );
}

export default function Performance({ tok, user, lang, onAskWork }) {
  const [me, setMe] = useState(null);
  const [crit, setCrit] = useState(null);
  const [ov, setOv] = useState(null);
  const [trend, setTrend] = useState(null);
  const [err, setErr] = useState('');
  const mgr = isManagerLike(user) && (user.permissions || []).includes('ANALYTICS_READ');

  useEffect(() => {
    request('/api/v1/work/me').then(setMe).catch((e) => setErr(e.message));
    request('/api/v1/analytics/criteria').then(setCrit).catch(() => setCrit(null));
    if (mgr) {
      request('/api/v1/analytics/overview').then(setOv).catch((e) => setErr(e.message));
      const now = new Date();
      const from = new Date(now.getFullYear(), now.getMonth() - 2, 1).toISOString().slice(0, 10);
      request(`/api/v1/analytics/trends?date_from=${from}&bucket=week`).then(setTrend).catch(() => setTrend(null));
    }
  }, []);

  return (
    <div>
      <div className="page-head"><h2>{T(lang, 'performance')}</h2></div>
      <ErrorNote error={err} />
      {!me && !err && <Spinner label={T(lang, 'loading')} />}
      {me && (
        <div className="grid-2">
          <Card title={T(lang, 'monthlyPerf')}>
            <Score perf={me.performance} lang={lang} />
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn btn-ghost btn-mini"
                onClick={() => onAskWork && onAskWork({ text: 'Summarize my work for this period: completed work, pending work, and what affects my score.' })}>
                {T(lang, 'askAIAboutWork')}
              </button>
            </div>
          </Card>
          <Card title={T(lang, 'scoreBreakdown')}>
            {!crit && <Spinner label={T(lang, 'loading')} />}
            {crit && (
              <table className="tbl"><tbody>
                {Object.entries(crit.weights || {}).map(([k, w]) => (
                  <tr key={k}><td className="mut">{k.replace(/_/g, ' ')}</td>
                    <td>{Math.round(w * 100)}%</td></tr>
                ))}
              </tbody></table>
            )}
            <p className="mut">{T(lang, 'previousMonth')}: — ({T(lang, 'trend')} {T(lang, 'insufficientData').toLowerCase()})</p>
          </Card>
        </div>
      )}
      {mgr && (
        <React.Fragment>
          <Card title={T(lang, 'trend')}>
            {!trend && <Spinner label={T(lang, 'loading')} />}
            {trend && trend.points?.length > 0 && (
              <LineChart label="completed per week"
                points={trend.points.map((p) => ({ label: p.bucket, value: p.completed }))} />
            )}
            {trend && !(trend.points || []).length && <Empty title={T(lang, 'noResults')} />}
            {trend && <p className="mut">{trend.coverage_note}</p>}
          </Card>
          <Card title={T(lang, 'teamProgress')}>
            {!ov && <Spinner label={T(lang, 'loading')} />}
            {ov && !(ov.per_user || []).length && <Empty title={T(lang, 'noResults')} />}
            {ov && (ov.per_user || []).length > 0 && (
              <div className="tbl-wrap"><table className="tbl">
                <thead><tr><th>{T(lang, 'employees')}</th><th>{T(lang, 'completedWork')}</th>
                  <th>{T(lang, 'attendance')}</th><th>{T(lang, 'evidence')}</th></tr></thead>
                <tbody>{ov.per_user.map((r) => (
                  <tr key={r.user_id}><td>{r.username || r.user_id}</td>
                    <td>{r.completed}/{r.tasks}{r.completion_pct != null ? ` (${r.completion_pct}%)` : ''}</td>
                    <td>{r.present_days}/{r.attendance_days}</td>
                    <td>{r.evidence}</td></tr>))}</tbody>
              </table></div>
            )}
            {ov && (ov.per_user || []).length > 0 && (
              <div style={{ marginTop: 10 }}>
                <BarChart label="completion %" bars={ov.per_user.map((r) => ({
                  label: (r.username || r.user_id).slice(0, 12), value: r.completion_pct || 0 }))} />
              </div>
            )}
          </Card>
        </React.Fragment>
      )}
    </div>
  );
}
