import React, { useEffect, useState } from 'react';
import { apiFetch, hasPerm } from './rbac.js';

/* Step 21 agents console: run allow-listed agents, confirm sensitive steps,
   browse history. All enforcement is server-side; this UI only renders
   states the backend returns. */

const STATE_COLOR = { COMPLETED: 'var(--ok)', FAILED: 'var(--bad)', RUNNING: 'var(--link)',
  WAITING_CONFIRMATION: 'var(--warn)', CANCELLED: 'var(--mut)', TIMEOUT: 'var(--bad)', CREATED: 'var(--mut)' };

function RunCard({ tok, run, onChange }) {
  const act = async (path, body) => {
    try { onChange(await apiFetch(`/api/v1/agents/runs/${run.id}/${path}`, tok, { method: 'POST', body: JSON.stringify(body || {}) })); }
    catch (e) { alert(e.message); }
  };
  return (
    <div style={s.card}>
      <div><b>{run.agent}</b> <span style={{ ...s.pill, color: STATE_COLOR[run.state] || 'var(--text)' }}>{run.state}</span>
        <span style={s.mut}> · {run.id} · lang {run.lang}</span></div>
      <p style={{ fontSize: 13, margin: '6px 0' }}>{run.goal || '(no goal)'}</p>
      <ol style={{ fontSize: 12, paddingLeft: 20 }}>
        {(run.steps || []).map((st, i) => (
          <li key={i}><code>{st.tool}</code> — {st.status || 'pending'}
            {st.error && <span style={s.err}> · {st.error}</span>}
            {st.result && <div style={s.mut}>{String(st.result).slice(0, 160)}</div>}</li>))}
      </ol>
      {run.result && <pre style={s.pre}>{run.result}</pre>}
      {run.state === 'WAITING_CONFIRMATION' && (
        <div style={{ display: 'flex', gap: 8 }}>
          <button style={s.btn} onClick={() => act('confirm', { approve: true })}>Confirm step</button>
          <button style={s.ghost} onClick={() => act('confirm', { approve: false })}>Deny</button>
        </div>)}
      {['RUNNING', 'WAITING_CONFIRMATION'].includes(run.state) && (
        <button style={s.mini} onClick={() => act('cancel')}>Cancel run</button>)}
    </div>
  );
}

export function AgentsView({ tok, user }) {
  const [agents, setAgents] = useState([]);
  const [agent, setAgent] = useState('document_analysis');
  const [goal, setGoal] = useState('');
  const [docs, setDocs] = useState([]);
  const [selDocs, setSelDocs] = useState([]);
  const [runs, setRuns] = useState([]);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  if (!hasPerm(user, 'AI_AGENT_USE')) return <div style={s.card}><p style={s.err}>403 — requires AI_AGENT_USE.</p></div>;

  const load = async () => {
    try {
      setAgents((await apiFetch('/api/v1/agents', tok)).agents);
      setRuns((await apiFetch('/api/v1/agents/runs', tok)).runs);
      setDocs((await apiFetch('/api/v1/docs?status=READY', tok)).documents);
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); }, []);
  const start = async () => {
    setErr(''); setBusy(true);
    try {
      const r = await apiFetch(`/api/v1/agents/${agent}/run`, tok, { method: 'POST',
        body: JSON.stringify({ goal, document_ids: selDocs.length ? selDocs : null }) });
      setRuns((rs) => [r, ...rs]);
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };
  const refresh = (updated) => setRuns((rs) => rs.map((x) => (x.id === updated.id ? updated : x)));
  return (
    <div>
      <div style={s.card}>
        <h3 style={{ marginTop: 0 }}>Agents (controlled, permission-checked)</h3>
        {err && <p style={s.err}>{err}</p>}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <select style={s.in} value={agent} onChange={(e) => setAgent(e.target.value)}>
            {agents.map((a) => <option key={a.name} value={a.name} disabled={!a.allowed}>
              {a.name}{a.allowed ? '' : ' (not for your role)'}</option>)}
          </select>
          <input style={{ ...s.in, flex: 1, minWidth: 200 }} value={goal}
            placeholder="Goal, e.g. summarize the selected documents"
            onChange={(e) => setGoal(e.target.value)} />
          <button style={s.btn} onClick={start} disabled={busy}>{busy ? '…' : 'Run agent'}</button>
        </div>
        <div style={{ marginTop: 8, fontSize: 12, color: 'var(--mut)' }}>
          Documents:{' '}
          {docs.map((d) => (
            <label key={d.id} style={{ marginRight: 10 }} title={d.filename}>
              <input type="checkbox" checked={selDocs.includes(d.id)}
                onChange={() => setSelDocs((w) => (w.includes(d.id) ? w.filter((x) => x !== d.id) : [...w, d.id]))} />
              {' '}{d.filename}</label>))}
          {docs.length === 0 && <span>(no READY documents)</span>}
        </div>
        <p style={s.mut}>Agents run allow-listed tools only. Sensitive steps pause for your confirmation. Denials are recorded, never bypassed.</p>
      </div>
      <h3>History</h3>
      {runs.map((r) => <RunCard key={r.id} tok={tok} run={r} onChange={refresh} />)}
      {runs.length === 0 && <p style={s.mut}>No runs yet.</p>}
    </div>
  );
}

const s = {
  card: { background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 16, marginBottom: 12, color: 'var(--text)' },
  in: { background: 'var(--input-bg)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 8, padding: 8 },
  btn: { background: 'var(--accent)', color: 'var(--btn-text)', border: 0, borderRadius: 8, padding: '8px 14px', cursor: 'pointer', fontWeight: 600 },
  ghost: { background: 'var(--ghost-bg)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 8, padding: '8px 14px', cursor: 'pointer' },
  mini: { background: 'var(--ghost-bg)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 6, padding: '3px 8px', cursor: 'pointer', fontSize: 12 },
  pill: { background: 'var(--chip-bg)', border: '1px solid var(--border-strong)', borderRadius: 12, padding: '2px 10px', fontSize: 12 },
  pre: { whiteSpace: 'pre-wrap', background: 'var(--bg-deep)', border: '1px solid var(--border-soft)', borderRadius: 8, padding: 10, fontSize: 12, maxHeight: 200, overflowY: 'auto', color: 'var(--text)' },
  mut: { color: 'var(--mut)', fontSize: 13 },
  err: { color: 'var(--bad)', fontSize: 13 },
};
