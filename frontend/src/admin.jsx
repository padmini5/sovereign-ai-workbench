import React, { useEffect, useState } from 'react';
import { apiFetch } from './rbac.js';
import { aiAdminStatus, docIntelStatus, langStatus, signStatus, voiceStatus } from './status.js';

/* Step 22 ADMIN system controls, product wording: AI Configuration,
   Document Intelligence, Voice Assistant, Language Services, Security.
   Raw provider state lives ONLY inside "Developer diagnostics".
   ADMIN-only server-side; secrets never exist in these payloads. */

function Section({ title, children }) {
  return <div style={s.card}><h3 style={{ marginTop: 0, color: 'var(--heading)' }}>{title}</h3>{children}</div>;
}

export function SysAdminView({ tok }) {
  const [ov, setOv] = useState(null);
  const [cfg, setCfg] = useState([]);
  const [err, setErr] = useState('');
  const [edit, setEdit] = useState({ key: 'SOV_RAG_TOP_K', value: '' });
  const [msg, setMsg] = useState('');

  const load = async () => {
    setErr('');
    try {
      setOv(await apiFetch('/api/v1/admin/overview', tok));
      setCfg((await apiFetch('/api/v1/admin/config', tok)).config);
    } catch (e) { setErr(e.status === 403 ? 'Denied: ADMIN only (server-enforced).' : e.message); }
  };
  useEffect(() => { load(); }, []);
  if (err) return <div style={s.card}><h3>System</h3><p style={s.err}>{err}</p></div>;
  if (!ov) return <div style={s.card}><p style={s.mut}>Loading system status…</p></div>;

  const apply = async () => {
    setMsg(''); setErr('');
    try {
      const j = await apiFetch('/api/v1/admin/config', tok,
        { method: 'PUT', body: JSON.stringify(edit) });
      setMsg(`Applied ${j.key} = ${j.value}`);
      await load();
    } catch (e) { setErr(e.message); }
  };
  const reset = async (key) => {
    try { await apiFetch('/api/v1/admin/config/reset', tok, { method: 'POST', body: JSON.stringify({ key }) }); await load(); }
    catch (e) { setErr(e.message); }
  };
  const toggleAgent = async (name, enabled) => {
    try {
      await apiFetch(`/api/v1/admin/agents/${name}`, tok, { method: 'PUT', body: JSON.stringify({ enabled }) });
      await load();
    } catch (e) { setErr(e.message); }
  };
  const toggleFeature = async (name, enabled) => {
    try {
      await apiFetch(`/api/v1/admin/features/${name}`, tok, { method: 'PUT', body: JSON.stringify({ enabled }) });
      await load();
    } catch (e) { setErr(e.message); }
  };

  const ai = aiAdminStatus(ov.ai?.health);
  const docI = docIntelStatus(ov.rag);
  const lg = langStatus({ i18n: { translate_available: ov.translate?.available } });
  const sg = signStatus();
  const vIn = voiceStatus({ stt: ov.stt }, 'input');
  const vOut = voiceStatus({ tts: ov.tts }, 'playback');

  const StatusRow = ({ title, desc, st }) => (
    <tr><td>{title}<br /><span style={s.mut}>{desc}</span></td>
      <td><span className={st.cls === 'ok' ? 'ok' : 'warn'}>{st.label}</span></td></tr>
  );

  return (
    <div>
      <Section title="AI Configuration">
        <table style={s.tbl}><tbody>
          <StatusRow title="AI Assistant" desc="Local answering service" st={ai} />
          <StatusRow title="Language Services" desc="Answer translation" st={lg} />
        </tbody></table>
      </Section>
      <Section title="Document Intelligence">
        <table style={s.tbl}><tbody>
          <StatusRow title="Secure document index" desc="Private retrieval over your documents" st={docI} />
        </tbody></table>
      </Section>
      <Section title="Voice Assistant">
        <table style={s.tbl}><tbody>
          <StatusRow title="Voice input" desc="Spoken questions" st={vIn} />
          <StatusRow title="Voice playback" desc="Spoken answers" st={vOut} />
        </tbody></table>
      </Section>
      <Section title="Security">
        <table style={s.tbl}><tbody>
          <StatusRow title="Secure signing" desc="Protected, auditable records" st={sg} />
        </tbody></table>
      </Section>
      <Section title="Agents">
        <table style={s.tbl}><thead><tr><th>Agent</th><th>Roles</th><th>Enabled</th><th></th></tr></thead>
          <tbody>{(ov.agents || []).map((a) => (
            <tr key={a.name}><td>{a.name}</td><td>{a.allowed ? 'visible to you' : '—'}</td>
              <td>{a.enabled ? 'on' : 'off'}</td>
              <td><button style={s.mini} onClick={() => toggleAgent(a.name, !a.enabled)}>
                turn {a.enabled ? 'off' : 'on'}</button></td></tr>))}</tbody></table>
      </Section>
      <Section title="Features">
        {Object.entries(ov.features || {}).map(([f, on]) => (
          <span key={f} style={{ marginRight: 12 }}>{f}: <b>{on ? 'on' : 'off'}</b>{' '}
            <button style={s.mini} onClick={() => toggleFeature(f, !on)}>turn {on ? 'off' : 'on'}</button></span>))}
      </Section>
      <details style={s.card}>
        <summary><b>Developer diagnostics</b> <span style={s.mut}>— raw provider state and configuration keys for troubleshooting. Never shown to regular users.</span></summary>
        <pre style={s.pre}>{JSON.stringify({ ai: ov.ai, llm: ov.llm, embeddings: ov.embeddings, rag: ov.rag, chroma: ov.chroma, translate: ov.translate, stt: ov.stt, tts: ov.tts, tools: ov.tools, system: ov.system }, null, 1)}</pre>
        <h3 style={{ margin: '12px 0 8px' }}>Configuration (non-secret only)</h3>
        {msg && <p style={{ color: 'var(--ok)', fontSize: 13 }}>{msg}</p>}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
          <select style={s.in} value={edit.key} onChange={(e) => setEdit({ ...edit, key: e.target.value })}>
            {cfg.map((c) => <option key={c.key} value={c.key}>{c.key} ({c.type})</option>)}
          </select>
          <input style={s.in} value={edit.value} placeholder="new value"
            onChange={(e) => setEdit({ ...edit, value: e.target.value })} />
          <button style={s.btn} onClick={apply}>Apply</button>
        </div>
        <table style={s.tbl}><thead><tr><th>Key</th><th>Value</th><th>Source</th><th></th></tr></thead>
          <tbody>{cfg.map((c) => (
            <tr key={c.key}><td><code>{c.key}</code><br /><span style={s.mut}>{c.desc}</span></td>
              <td>{c.value || '(empty)'}</td><td>{c.source}</td>
              <td>{c.source === 'override' && <button style={s.mini} onClick={() => reset(c.key)}>reset</button>}</td></tr>))}</tbody></table>
      </details>
    </div>
  );
}

const s = {
  card: { background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 16, marginBottom: 12 },
  in: { background: 'var(--input-bg)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 8, padding: 8 },
  btn: { background: 'var(--accent)', color: 'var(--btn-text)', border: 0, borderRadius: 8, padding: '8px 14px', cursor: 'pointer', fontWeight: 600 },
  mini: { background: 'var(--ghost-bg)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 6, padding: '3px 8px', cursor: 'pointer', fontSize: 12 },
  tbl: { width: '100%', borderCollapse: 'collapse', fontSize: 13, color: 'var(--text)' },
  pre: { whiteSpace: 'pre-wrap', background: 'var(--bg-deep)', border: '1px solid var(--border-soft)', borderRadius: 8, padding: 10, fontSize: 12, maxHeight: 220, overflowY: 'auto', color: 'var(--text)' },
  mut: { color: 'var(--mut)', fontSize: 13 },
  err: { color: 'var(--bad)', fontSize: 13 },
};
