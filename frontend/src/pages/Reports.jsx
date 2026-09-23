import React, { useEffect, useState } from 'react';
import { request } from '../api.js';
import { T } from '../i18n.js';
import { Card, Spinner, ErrorNote, Empty } from '../ui.jsx';

/* Reports backed by the real report store (POST /reports/generate,
   GET /reports[/{id}]). Visibility is server-enforced: own reports,
   manager-scope team/department reports, ADMIN all. Every report carries
   its Sources section — no internal store names exposed. */

const TYPES = ['employee_work', 'team_work', 'department', 'monthly_ops',
  'performance', 'attendance', 'financial'];

export default function Reports({ tok, user, lang }) {
  const [rows, setRows] = useState(null);
  const [sel, setSel] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    report_type: 'monthly_ops', scope: 'own',
    period_from: '', period_to: '', official_remarks: '',
  });
  const canCreate = (user.permissions || []).includes('REPORT_CREATE');
  const canTeam = ['ADMIN', 'MANAGER', 'approving_manager'].includes(user.role);

  const load = () => {
    setErr('');
    request('/api/v1/reports').then((j) => setRows(j.reports))
      .catch((e) => { setErr(e.message); setRows([]); });
  };
  useEffect(load, []);

  const open = async (id) => {
    setErr('');
    try { setSel(await request(`/api/v1/reports/${id}`)); }
    catch (e) { setErr(e.message); }
  };

  const generate = async () => {
    setBusy(true); setErr('');
    try {
      const j = await request('/api/v1/reports/generate', { method: 'POST', body: form });
      setSel(j); load();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  return (
    <div>
      <div className="page-head"><h2>{T(lang, 'reportsTitle')}</h2>
        <p className="mut">{T(lang, 'dataPeriod')} · {T(lang, 'sources')}</p></div>
      <ErrorNote error={err} onRetry={load} />

      {canCreate && (
        <Card title={T(lang, 'generateReport')}>
          <div className="row">
            <select className="select" value={form.report_type}
              onChange={(e) => setForm({ ...form, report_type: e.target.value })}>
              {TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
            </select>
            <select className="select" value={form.scope}
              onChange={(e) => setForm({ ...form, scope: e.target.value })}>
              <option value="own">own</option>
              {canTeam && <option value="team">team</option>}
              {canTeam && <option value="department">department</option>}
            </select>
            <input className="input" type="date" value={form.period_from}
              onChange={(e) => setForm({ ...form, period_from: e.target.value })} />
            <input className="input" type="date" value={form.period_to}
              onChange={(e) => setForm({ ...form, period_to: e.target.value })} />
            <button className="btn" disabled={busy} onClick={generate}>
              {busy ? <Spinner label="…" /> : T(lang, 'generateReport')}
            </button>
          </div>
          <label className="field" style={{ marginTop: 8 }}>{T(lang, 'officialRemarks')}
            <input className="input" value={form.official_remarks}
              onChange={(e) => setForm({ ...form, official_remarks: e.target.value })} />
          </label>
        </Card>
      )}

      <div className="grid-2">
        <Card title={T(lang, 'reportsTitle')}>
          {rows === null && <Spinner label={T(lang, 'loading')} />}
          {rows !== null && rows.length === 0 && <Empty title={T(lang, 'noResults')} />}
          {(rows || []).map((r) => (
            <div key={r.id} className="row" style={{ justifyContent: 'space-between', width: '100%' }}>
              <span><b>{r.title}</b> <span className="mut">· {r.report_type} · {r.scope_type}</span></span>
              <button className="btn btn-ghost btn-mini" onClick={() => open(r.id)}>{T(lang, 'viewReport')}</button>
            </div>
          ))}
        </Card>
        <Card title={sel ? sel.title : T(lang, 'viewReport')}>
          {!sel && <Empty title={T(lang, 'noResults')} />}
          {sel && (
            <div>
              <p className="mut">{T(lang, 'dataPeriod')}: {sel.period_from || '?'} – {sel.period_to || '?'}</p>
              <table className="tbl"><tbody>
                {Object.entries(sel.metrics || {}).filter(([, v]) => v == null || typeof v !== 'object').map(([k, v]) => (
                  <tr key={k}><td className="mut">{k.replace(/_/g, ' ')}</td>
                    <td>{v == null ? T(lang, 'insufficientData') : String(v)}</td></tr>))}
              </tbody></table>
              {sel.analysis && <p style={{ whiteSpace: 'pre-wrap', fontSize: 13 }}>{sel.analysis}</p>}
              {sel.official_remarks && <p className="mut">{T(lang, 'managerReview')}: {sel.official_remarks}</p>}
              <h4 className="mut" style={{ fontSize: 12 }}>{T(lang, 'sources')}</h4>
              <ul className="mut" style={{ fontSize: 12, paddingLeft: 18 }}>
                {(sel.sources || []).map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
