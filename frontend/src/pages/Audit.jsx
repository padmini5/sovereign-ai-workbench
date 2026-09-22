import React, { useEffect, useState } from 'react';
import { request } from '../api.js';
import { getLegacyToken } from '../session.js';
import { T } from '../i18n.js';
import { Card, Skeleton, ErrorNote, Empty, fmtTs } from '../ui.jsx';

/* Audit: GET /api/v1/admin/audit (requires AUDIT_READ) with client-side
   filters over {ts, actor_id, actor_role, action, resource, decision}.
   If the v1 endpoint 403s and a legacy session exists, fall back to the
   legacy GET /api/audit rows (time/user/role/action/doc) — labelled as
   legacy so nobody mistakes one for the other. */

export default function Audit({ lang }) {
  const [rows, setRows] = useState(null);
  const [source, setSource] = useState('v1');
  const [err, setErr] = useState('');
  const [q, setQ] = useState('');
  const [result, setResult] = useState('');

  const load = async () => {
    setErr(''); setRows(null);
    try {
      const j = await request('/api/v1/admin/audit');
      setRows((j.rows || j.events || []).map((r) => ({
        ts: r.ts, actor: r.actor_id, role: r.actor_role, action: r.action,
        resource: r.resource, decision: r.decision || '—', detail: r.detail || '',
      })));
      setSource('v1');
    } catch (e) {
      if (e.status === 403) {
        const leg = getLegacyToken();
        if (leg) {
          try {
            const j = await request('/api/audit', { token: 'legacy' });
            setRows((j.rows || []).map((r) => ({
              ts: r.ts, actor: r.user_id, role: r.role, action: r.action,
              resource: r.doc_id || '—', decision: '—', detail: 'legacy trail',
            })));
            setSource('legacy');
            return;
          } catch { /* fall through to honest error */ }
        }
      }
      setErr(e.message);
      setRows([]);
    }
  };
  useEffect(() => { load(); }, []);

  const filtered = (rows || []).filter((r) => {
    if (result === 'allow' && !/allow|ok|permit/i.test(r.decision)) return false;
    if (result === 'deny' && !/deny|denied|blocked|403|401/i.test(r.decision)) return false;
    if (!q) return true;
    const hay = `${r.actor} ${r.role} ${r.action} ${r.resource} ${r.decision} ${r.detail}`.toLowerCase();
    return hay.includes(q.toLowerCase());
  });

  return (
    <div>
      <div className="page-head">
        <h2>{T(lang, 'audit')}</h2>
        <p className="mut">
          {source === 'v1' ? 'GET /api/v1/admin/audit (AUDIT_READ)' : 'legacy GET /api/audit'}
          {' · '}{filtered.length}/{rows?.length ?? 0} rows · read-only
        </p>
      </div>

      <Card>
        <div className="row">
          <input className="input" style={{ flex: 1, minWidth: 200 }} placeholder={T(lang, 'search') + ' (user, action, resource)…'}
            value={q} onChange={(e) => setQ(e.target.value)} />
          <select className="select" value={result} onChange={(e) => setResult(e.target.value)}>
            <option value="">{T(lang, 'auditAll')}</option>
            <option value="allow">{T(lang, 'auditAllow')}</option>
            <option value="deny">{T(lang, 'auditDeny')}</option>
          </select>
          <button className="btn btn-ghost" onClick={load}>{T(lang, 'refresh')}</button>
        </div>
      </Card>

      <ErrorNote error={err} onRetry={load} />
      {rows === null && !err && <Skeleton rows={5} />}
      {rows?.length === 0 && !err && <Card><Empty title="No audit events yet." hint="Actions you take will appear here." /></Card>}
      {rows?.length > 0 && (
        <div className="card tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>{T(lang, 'timestamp')}</th>
                <th>{T(lang, 'user')}</th>
                <th>{T(lang, 'role')}</th>
                <th>Action</th>
                <th>{T(lang, 'resource')}</th>
                <th>{T(lang, 'decision')}</th>
                <th>{T(lang, 'detail')}</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r, i) => (
                <tr key={i}>
                  <td style={{ whiteSpace: 'nowrap' }}>{fmtTs(r.ts)}</td>
                  <td>{r.actor}</td>
                  <td><span className="chip">{r.role}</span></td>
                  <td><code>{r.action}</code></td>
                  <td>{String(r.resource)}</td>
                  <td>
                    <span className={'badge ' + (/deny|blocked|denied/i.test(r.decision) ? 'badge-red' : /allow|ok/i.test(r.decision) ? 'badge-green' : 'badge-slate')}>
                      {r.decision}
                    </span>
                  </td>
                  <td className="mut" style={{ maxWidth: 340, overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length === 0 && <Empty title={T(lang, 'noResults')} />}
        </div>
      )}
    </div>
  );
}
