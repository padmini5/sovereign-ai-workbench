import React, { useEffect, useState } from 'react';
import { apiFetch, hasPerm } from './rbac.js';

/* Step 21 agents console: run allow-listed agents, confirm sensitive steps,
   browse history. Plus the SIH approval-notes panel (status, .docx download,
   human approve/reject/correct for WORK_MANAGE holders). All enforcement is
   server-side; this UI only renders states the backend returns. */

const STATE_COLOR = { COMPLETED: 'var(--ok)', FAILED: 'var(--bad)', RUNNING: 'var(--link)',
  WAITING_CONFIRMATION: 'var(--warn)', CANCELLED: 'var(--mut)', TIMEOUT: 'var(--bad)', CREATED: 'var(--mut)' };

const STEP_LABEL = {
  read_inspection_report: 'Reading report',
  find_procedures: 'Finding relevant procedures',
  analyze_findings: 'Analyzing findings',
  draft_approval_note: 'Preparing approval note',
  generate_approval_docx: 'Generating approval document',
};

const APPROVAL_COLOR = { AWAITING_HUMAN_APPROVAL: 'var(--warn)', APPROVED: 'var(--ok)',
  REJECTED: 'var(--bad)', CORRECTION_REQUESTED: 'var(--warn)', FAILED: 'var(--bad)',
  CANCELLED: 'var(--mut)', DRAFT: 'var(--link)', PREPARING: 'var(--mut)' };

const NA = 'Not available in source material.';

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
          <li key={i}><code>{STEP_LABEL[st.tool] || st.tool}</code> — {st.status || 'pending'}
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

function ApprovalCard({ tok, rec, canDecide, onDecide, onError }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  const [comment, setComment] = useState('');
  const note = (open && detail && detail.note) || rec.note || {};
  const toggle = async () => {
    if (open) { setOpen(false); return; }
    try { setDetail(await apiFetch(`/api/v1/approvals/${rec.id}`, tok)); setOpen(true); }
    catch (e) { onError(e.message); }
  };
  const decide = async (d) => {
    try {
      const j = await apiFetch(`/api/v1/approvals/${rec.id}/decision`, tok,
        { method: 'POST', body: JSON.stringify({ decision: d, comment }) });
      onDecide(j); setComment('');
    } catch (e) { onError(e.message); }
  };
  const download = async () => {
    try {
      const r = await fetch(`/api/v1/approvals/${rec.id}/document`,
        { headers: { Authorization: 'Bearer ' + tok } });
      if (!r.ok) {
        let body = null; try { body = await r.json(); } catch { /* non-JSON */ }
        throw new Error((body && body.detail) || `download failed (${r.status})`);
      }
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement('a');
      a.href = url; a.download = rec.filename || 'approval-note.docx';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (e) { onError(e.message); }
  };
  const findings = note.key_findings;
  const procedures = note.relevant_procedure;
  return (
    <div style={s.card}>
      <div>
        <b>{note.title || rec.doc_filename}</b>
        <span style={{ ...s.pill, color: APPROVAL_COLOR[rec.status] || 'var(--text)' }}>
          {rec.status_label || rec.status}</span>
        <span style={s.mut}> · {rec.stage_label || rec.stage} · {rec.doc_filename} · uploaded {rec.doc_uploaded}</span>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        {rec.has_document && <button style={s.mini} onClick={download}>Download {rec.filename || '.docx'}</button>}
        <button style={s.ghost} onClick={toggle}>{open ? 'Hide details' : 'View details'}</button>
        <span style={s.mut}>requested by {rec.requester}</span>
      </div>
      {open && detail && (
        <div style={{ fontSize: 13, marginTop: 10 }}>
          <p style={{ margin: '6px 0' }}><b>Summary:</b> {note.summary || NA}</p>
          <p style={{ margin: '6px 0' }}><b>Inspection reference:</b> {note.inspection_reference || NA}
            {' '}· <b>Date:</b> {note.date || NA} · <b>Prepared for:</b> {note.prepared_for || NA}</p>
          <p style={{ margin: '6px 0' }}><b>Key findings:</b></p>
          <ul style={{ margin: '4px 0 8px', paddingLeft: 20 }}>
            {Array.isArray(findings) && findings.length
              ? findings.map((f, i) => (
                <li key={i}>{f.statement}<div style={s.mut}>Evidence: {f.evidence || NA}</div></li>))
              : <li>{findings || NA}</li>}
          </ul>
          <p style={{ margin: '6px 0' }}><b>Relevant procedure:</b></p>
          <ul style={{ margin: '4px 0 8px', paddingLeft: 20 }}>
            {Array.isArray(procedures) && procedures.length
              ? procedures.map((p, i) => (
                <li key={i}>{p.ref || NA}{p.page ? ` — section/page ${p.page}` : ''}
                  <div style={s.mut}>{p.source === 'knowledge_base' ? 'company knowledge base' : 'authorized document'}</div></li>))
              : <li>{procedures || 'No relevant authorized procedure was found.'}</li>}
          </ul>
          <p style={{ margin: '6px 0' }}><b>Evidence:</b></p>
          <ul style={{ margin: '4px 0 8px', paddingLeft: 20 }}>
            {(note.evidence || [NA]).map((e, i) => <li key={i}>{e}</li>)}
          </ul>
          <p style={{ margin: '6px 0' }}><b>Recommended action:</b> {note.recommended_action || NA}</p>
          <p style={{ margin: '6px 0' }}><b>Confidence:</b> {note.confidence || NA}
            {detail.analysis_mode === 'extractive' &&
              <span style={s.mut}> (findings extracted directly from the source — automated AI analysis unavailable)</span>}</p>
          {detail.source_excerpt && (
            <details style={{ marginTop: 6 }}>
              <summary style={{ cursor: 'pointer' }}>Extracted source text ({(detail.extract || {}).method || 'text'})</summary>
              <pre style={s.pre}>{detail.source_excerpt}</pre>
            </details>)}
          {detail.decision && (
            <p style={{ margin: '8px 0' }}><b>Decision:</b> {detail.decision} by {detail.decided_by} ({detail.decided_role})
              {detail.comment ? ` — "${detail.comment}"` : ''}</p>)}
        </div>)}
      {canDecide && rec.status === 'AWAITING_HUMAN_APPROVAL' && (
        <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <input style={{ ...s.in, minWidth: 180, flex: 1 }} value={comment}
            placeholder="Comment (optional)" aria-label="Decision comment"
            onChange={(e) => setComment(e.target.value)} />
          <button style={s.btn} onClick={() => decide('approve')}>Approve</button>
          <button style={s.ghost} onClick={() => decide('reject')}>Reject</button>
          <button style={s.mini} onClick={() => decide('correct')}>Request correction</button>
        </div>)}
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
  const [approvals, setApprovals] = useState([]);
  const [canDecide, setCanDecide] = useState(false);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  if (!hasPerm(user, 'AI_AGENT_USE')) return <div style={s.card}><p style={s.err}>403 — requires AI_AGENT_USE.</p></div>;

  const loadApprovals = async () => {
    try {
      const j = await apiFetch('/api/v1/approvals', tok);
      setApprovals(j.approvals || []);
      setCanDecide(!!j.can_decide);
    } catch { /* list is supplementary — a denied/absent panel is not an error */ }
  };
  const load = async () => {
    try {
      const list = (await apiFetch('/api/v1/agents', tok)).agents;
      // Show only agents this role can actually run (server flags; the
      // backend re-checks authorization on every start_run).
      const runnable = list.filter((a) => a.allowed && a.enabled);
      setAgents(runnable);
      setAgent((cur) => (runnable.some((a) => a.name === cur)
        ? cur : (runnable[0] ? runnable[0].name : '')));
      setRuns((await apiFetch('/api/v1/agents/runs', tok)).runs);
      // All own documents (any status): the approval workflow reads/OCRs an
      // as-uploaded report itself, so a prior indexing step is not required.
      setDocs((await apiFetch('/api/v1/docs', tok)).documents);
      await loadApprovals();
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); }, []);
  const start = async () => {
    setErr(''); setBusy(true);
    try {
      if (agent === 'inspection_approval' && selDocs.length !== 1) {
        throw new Error('This workflow needs exactly one document selected.');
      }
      const r = await apiFetch(`/api/v1/agents/${agent}/run`, tok, { method: 'POST',
        body: JSON.stringify({ goal, document_ids: selDocs.length ? selDocs : null }) });
      setRuns((rs) => [r, ...rs]);
      if (agent === 'inspection_approval') await loadApprovals();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };
  const refreshRun = (updated) => setRuns((rs) => rs.map((x) => (x.id === updated.id ? updated : x)));
  const refreshApproval = (updated) => setApprovals((xs) => xs.map((x) => (x.id === updated.id ? updated : x)));
  return (
    <div>
      <div style={s.card}>
        <h3 style={{ marginTop: 0 }}>Agents (controlled, permission-checked)</h3>
        {err && <p style={s.err}>{err}</p>}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <select style={s.in} value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent">
            {agents.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
          </select>
          <input style={{ ...s.in, flex: 1, minWidth: 200 }} value={goal}
            placeholder="Goal, e.g. review this inspection report and prepare an approval note"
            onChange={(e) => setGoal(e.target.value)} />
          <button style={s.btn} onClick={start} disabled={busy}>{busy ? '…' : 'Run agent'}</button>
        </div>
        <div style={{ marginTop: 8, fontSize: 12, color: 'var(--mut)' }}>
          Documents:{' '}
          {docs.map((d) => (
            <label key={d.id} style={{ marginRight: 10 }} title={d.filename}>
              <input type="checkbox" checked={selDocs.includes(d.id)}
                onChange={() => setSelDocs((w) => (w.includes(d.id) ? w.filter((x) => x !== d.id) : [...w, d.id]))} />
              {' '}{d.filename}{d.status && d.status !== 'READY' ? ` · ${d.status}` : ''}</label>))}
          {docs.length === 0 && <span>(no documents — upload one first)</span>}
        </div>
        <p style={s.mut}>Agents run allow-listed tools only. Sensitive steps pause for your confirmation. Denials are recorded, never bypassed.</p>
      </div>
      <h3>Approval notes</h3>
      {approvals.length === 0 && <p style={s.mut}>No approval notes yet. Run the inspection approval workflow on a report.</p>}
      {approvals.map((r) => (
        <ApprovalCard key={r.id} tok={tok} rec={r} canDecide={canDecide}
          onDecide={refreshApproval} onError={setErr} />))}
      <h3>History</h3>
      {runs.map((r) => <RunCard key={r.id} tok={tok} run={r} onChange={refreshRun} />)}
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
  pill: { background: 'var(--chip-bg)', border: '1px solid var(--border-strong)', borderRadius: 12, padding: '2px 10px', fontSize: 12, marginLeft: 6 },
  pre: { whiteSpace: 'pre-wrap', background: 'var(--bg-deep)', border: '1px solid var(--border-soft)', borderRadius: 8, padding: 10, fontSize: 12, maxHeight: 200, overflowY: 'auto', color: 'var(--text)' },
  mut: { color: 'var(--mut)', fontSize: 13 },
  err: { color: 'var(--bad)', fontSize: 13 },
};
