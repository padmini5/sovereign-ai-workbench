import React, { useEffect, useState } from 'react';
import { request } from '../api.js';
import { T } from '../i18n.js';
import { Card, Spinner, ErrorNote, Empty, BarChart } from '../ui.jsx';

/* Spreadsheet intelligence: pick an owned CSV/XLSX document, inspect
   structure safely server-side (no macro/formula execution), then compute
   revenue/expense/profit from columns the manager names explicitly.
   Unknown columns and empty inputs yield "insufficient data" — never guesses. */

export default function Spreadsheets({ tok, user, lang }) {
  const [docs, setDocs] = useState(null);
  const [sel, setSel] = useState('');
  const [rev, setRev] = useState('');
  const [exp, setExp] = useState('');
  const [group, setGroup] = useState('');
  const [out, setOut] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState(null);
  const [upBusy, setUpBusy] = useState(false);
  const [upMsg, setUpMsg] = useState('');
  /* UI-only gate — the server enforces DOCUMENT_UPLOAD independently. */
  const canUpload = (user.permissions || []).includes('DOCUMENT_UPLOAD');

  const loadDocs = () => Promise.all([
    request('/api/v1/docs?kind=csv').catch(() => ({ documents: [] })),
    request('/api/v1/docs?kind=xlsx').catch(() => ({ documents: [] })),
  ]).then(([a, b]) => {
    const list = [...(a.documents || []), ...(b.documents || [])];
    setDocs(list);
    return list;
  });

  useEffect(() => {
    loadDocs().catch((e) => { setErr(e.message); setDocs([]); });
  }, []);

  const upload = async () => {
    if (!file || upBusy) return;
    setUpBusy(true); setErr(''); setUpMsg('');
    try {
      const fd = new FormData();
      fd.append('f', file, file.name);
      const j = await request('/api/v1/docs/upload', { method: 'POST', body: fd });
      setFile(null);
      const list = await loadDocs();
      if (j?.id) setSel(j.id);
      setUpMsg(`${j?.filename || ''} — ${list.length} spreadsheet(s) available`);
    } catch (e) { setErr(e.message); } finally { setUpBusy(false); }
  };

  const run = async () => {
    if (!sel) return;
    setBusy(true); setErr(''); setOut(null);
    try {
      const j = await request('/api/v1/work/spreadsheets/analyze', { method: 'POST',
        body: { doc_id: sel,
          revenue_cols: rev.split(',').map((s) => s.trim()).filter(Boolean),
          expense_cols: exp.split(',').map((s) => s.trim()).filter(Boolean),
          group_by: group.trim() } });
      setOut(j);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const t = out?.metrics || out?.totals;
  const groups = out?.groups ? Object.entries(out.groups)
    .map(([label, g]) => ({ label, value: g.profit ?? g.revenue ?? 0 })) : [];

  return (
    <div>
      <div className="page-head"><h2>{T(lang, 'spreadsheets')}</h2>
        <p className="mut">{T(lang, 'totals')} · {T(lang, 'statistics')}</p></div>
      <ErrorNote error={err} />
      <Card title={T(lang, 'spreadsheets')}>
        {canUpload && (
          <div className="row" style={{ marginBottom: 10 }}>
            <input type="file" accept=".csv,.xlsx" style={{ flex: 1 }}
              onChange={(e) => setFile(e.target.files?.[0] || null)} />
            <button className="btn btn-ghost" disabled={upBusy || !file} onClick={upload}>
              {upBusy ? <Spinner label="…" /> : T(lang, 'uploadDoc')}
            </button>
          </div>
        )}
        {upMsg && <p className="ok" style={{ fontSize: 13 }}>{upMsg}</p>}
        {docs === null && <Spinner label={T(lang, 'loading')} />}
        {docs !== null && docs.length === 0 && (
          <Empty title={T(lang, 'noResults')} hint="Upload a CSV or XLSX file to begin." />)}
        {docs && docs.length > 0 && (
          <div className="grid-2">
            <label className="field">Document
              <select className="select" value={sel} onChange={(e) => setSel(e.target.value)}>
                <option value="">—</option>
                {docs.map((d) => <option key={d.id} value={d.id}>{d.filename}</option>)}
              </select>
            </label>
            <label className="field">{T(lang, 'revenue')} columns (comma separated)
              <input className="input" value={rev} onChange={(e) => setRev(e.target.value)} placeholder="revenue, amount" />
            </label>
            <label className="field">{T(lang, 'expenses')} columns (comma separated)
              <input className="input" value={exp} onChange={(e) => setExp(e.target.value)} placeholder="expense, cost" />
            </label>
            <label className="field">Group by column (optional)
              <input className="input" value={group} onChange={(e) => setGroup(e.target.value)} placeholder="month" />
            </label>
          </div>
        )}
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn" disabled={busy || !sel} onClick={run}>
            {busy ? <Spinner label="…" /> : T(lang, 'statistics')}
          </button>
        </div>
      </Card>

      {out && (
        <React.Fragment>
          <div className="grid-3">
            <div className="tile"><div className="k">{T(lang, 'rows')}</div>
              <div className="v">{out.structure?.rows ?? '—'}</div></div>
            <div className="tile"><div className="k">{T(lang, 'columns')}</div>
              <div className="v">{out.structure?.columns?.length ?? '—'}</div>
              <div className="d">{(out.structure?.columns || []).map((c) => c.name).join(', ')}</div></div>
            <div className="tile"><div className="k">{T(lang, 'missingValues')}</div>
              <div className="v">{(out.missing_columns || []).join(', ') || '—'}</div></div>
            <div className="tile"><div className="k">{T(lang, 'revenue')}</div>
              <div className="v">{t?.revenue ?? '—'}</div></div>
            <div className="tile"><div className="k">{T(lang, 'expenses')}</div>
              <div className="v">{t?.expenses ?? '—'}</div></div>
            <div className="tile"><div className="k">{T(lang, 'profitLoss')}</div>
              <div className="v">{t?.profit ?? '—'}</div></div>
          </div>
          <Card title={T(lang, 'statistics')}>
            <p className={out.verdict?.startsWith('calculated') ? 'ok' : 'warn'} style={{ fontSize: 13 }}>
              {out.verdict === 'calculated from data' ? out.verdict : T(lang, 'insufficientData')}
            </p>
            {out.skipped_cells > 0 && (
              <p className="mut">Skipped non-numeric cells: {out.skipped_cells} (text is never coerced).</p>)}
            {groups.length > 0 && <BarChart bars={groups} label="profit by group" />}
          </Card>
        </React.Fragment>
      )}
    </div>
  );
}
