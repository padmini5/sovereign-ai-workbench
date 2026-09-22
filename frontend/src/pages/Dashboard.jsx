import React, { useEffect, useRef, useState } from 'react';
import { api, wsUrl, getLegacyToken } from '../session.js';
import { request } from '../api.js';
import { T } from '../i18n.js';
import { visibleNav } from '../nav.js';
import { Card, Spinner, PermChips, ErrorNote } from '../ui.jsx';
import { aiStatus, docIntelStatus, signStatus, scrubDetail } from '../status.js';

/* Dashboard: welcome, role-adapted quick actions, the demo workflow graphic
   (Upload → Ask AI → Retrieve → Sources → Image → Voice → Agent → Audit),
   sovereignty indicators (zero egress, signing status, hardware/model), the
   honest AI-provider card, and the preserved legacy pipeline demo (port of
   main.jsx Monitor/PqcCard/TaskRunner/TierToggle). Legacy endpoints are only
   called with the legacy token — and only when one exists. */

const WORKFLOW_STEPS = [
  ['01', 'Upload'], ['02', 'Ask AI'], ['03', 'Retrieve'], ['04', 'Sources'],
  ['05', 'Image'], ['06', 'Voice'], ['07', 'Agent'], ['08', 'Audit'],
];
const WORKFLOW = ['PLAN', 'CHECK_PERMISSIONS', 'RETRIEVE', 'EXTRACT', 'DRAFT', 'CITE', 'REVIEW', 'SIGN', 'LOG'];
const tierColor = (tier) => ({ public: '#1a7f37', internal: '#0a66c2', confidential: '#b54708', restricted: '#b42318' }[tier] || '#555');

/* ---------------- sovereignty indicators ---------------- */

function EgressTile({ lang, legacyTok }) {
  const [m, setM] = useState(null);
  useEffect(() => {
    let ws; let closed = false;
    try {
      ws = new WebSocket(wsUrl('/ws/monitor'));
      ws.onmessage = (e) => { if (!closed) try { setM(JSON.parse(e.data)); } catch { /* bad frame */ } };
    } catch { /* WS unavailable — polling below covers it */ }
    let poll = null;
    if (legacyTok) {
      poll = setInterval(async () => {
        try {
          const r = await fetch(api('/api/monitor'), { headers: { Authorization: 'Bearer ' + legacyTok } });
          if (r.ok) setM(await r.json());
        } catch { /* offline */ }
      }, 5000);
    }
    return () => { closed = true; try { ws && ws.close(); } catch { /* noop */ } clearInterval(poll); };
  }, [legacyTok]);
  if (!m) return (
    <div className="tile">
      <div className="k">{T(lang, 'zeroEgress')}</div>
      <div className="v" style={{ fontSize: 15 }}><Spinner label={T(lang, 'loading')} /></div>
    </div>
  );
  const app = m.app_egress_connections ?? 0;
  const ok = app === 0;
  return (
    <div className="tile">
      <div className="k">{T(lang, 'zeroEgress')}</div>
      <div className={'v ' + (ok ? 'ok' : 'bad')}>{ok ? '0 — ZERO EGRESS (sovereign)' : app}</div>
      <div className="d">
        {T(lang, 'appEgress')}: <b>{app}</b> · {T(lang, 'sysTcp')}: {m.outbound_connections ?? '—'}
      </div>
    </div>
  );
}

function SigningTile({ lang, pqc }) {
  const s = signStatus();
  if (!pqc) return (
    <div className="tile">
      <div className="k">{T(lang, 'securityTitle')}</div>
      <div className="v" style={{ fontSize: 15 }}><Spinner label={T(lang, 'loading')} /></div>
    </div>
  );
  return (
    <div className="tile">
      <div className="k">{T(lang, 'securityTitle')}</div>
      <div className={'v ' + s.cls} style={{ fontSize: 15 }}>{T(lang, 'securityHead')}</div>
      <div className="d">{T(lang, 'securityDesc')}</div>
    </div>
  );
}

function ProviderTile({ lang, st }) {
  const s = aiStatus(st);
  if (!st) return (
    <div className="tile">
      <div className="k">{T(lang, 'aiTitle')}</div>
      <div className="v" style={{ fontSize: 15 }}><Spinner label={T(lang, 'loading')} /></div>
    </div>
  );
  return (
    <div className="tile">
      <div className="k">{T(lang, 'aiTitle')}</div>
      <div className={'v ' + s.cls} style={{ fontSize: 15 }}>{s.label}</div>
      <div className="d">{T(lang, 'aiDesc')}</div>
    </div>
  );
}

function HardwareTile({ lang, legacyTok }) {
  const [info, setInfo] = useState(null);
  const [err, setErr] = useState('');
  const flip = async (tier) => {
    setErr('');
    try {
      const r = await fetch(api('/api/model-tier'), {
        method: 'POST',
        headers: { Authorization: 'Bearer ' + legacyTok, 'Content-Type': 'application/json' },
        body: JSON.stringify({ tier }),
      });
      const j = await r.json().catch(() => null);
      if (r.ok) { const g = await fetch(api('/api/model-tier'), { headers: { Authorization: 'Bearer ' + legacyTok } }); setInfo(await g.json()); }
      else setErr((j && j.detail) || 'HTTP ' + r.status);
    } catch { setErr('Network unreachable.'); }
  };
  useEffect(() => {
    if (!legacyTok) return;
    fetch(api('/api/model-tier'), { headers: { Authorization: 'Bearer ' + legacyTok } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
      .then(setInfo).catch((e) => setErr(e.message));
  }, [legacyTok]);
  if (!legacyTok) return (
    <div className="tile">
      <div className="k">{T(lang, 'systemTitle')}</div>
      <div className="v" style={{ fontSize: 15 }}>{T(lang, 'systemHead')}</div>
      <div className="d">{T(lang, 'systemDesc')}</div>
    </div>
  );
  return (
    <div className="tile">
      <div className="k">{T(lang, 'systemTitle')}</div>
      <div className="v" style={{ fontSize: 15 }}>
        {info ? (info.tier === 'small' ? 'Fast mode' : 'Quality mode') : T(lang, 'systemHead')}
      </div>
      <div className="d row" style={{ marginTop: 6 }}>
        <button className="btn btn-ghost btn-mini" disabled={!info || info.tier === 'small'} onClick={() => flip('small')}>Fast (CPU)</button>
        <button className="btn btn-ghost btn-mini" disabled={!info || info.tier === 'large'} onClick={() => flip('large')}>Quality (GPU)</button>
      </div>
      {err && <div className="d bad">{scrubDetail(err)}</div>}
    </div>
  );
}

/* ---------------- preserved legacy pipeline demo ---------------- */

function TaskRunner({ legacyTok, user, lang }) {
  const [q, setQ] = useState('Corroded pipe section on line 14-CR-221 — what do OISD clauses and past NDT say?');
  const [res, setRes] = useState(null);
  const [events, setEvents] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const H = { Authorization: 'Bearer ' + legacyTok, 'Content-Type': 'application/json' };
  const run = async () => {
    setBusy(true); setErr('');
    try {
      const r = await fetch(api('/api/tasks'), { method: 'POST', headers: H, body: JSON.stringify({ query: q }) });
      const j = await r.json().catch(() => null);
      if (!r.ok) { setErr(scrubDetail((j && j.detail) || 'HTTP ' + r.status)); return; }
      setRes(j); setEvents(j.events || []);
      if (j.task_id) {
        const ws = new WebSocket(wsUrl('/ws/tasks/' + j.task_id));
        ws.onmessage = (e) => { try { const d = JSON.parse(e.data); setEvents(d.events || []); } catch { /* bad frame */ } };
        setTimeout(() => { try { ws.close(); } catch { /* noop */ } }, 15000);
      }
    } catch { setErr('Network unreachable.'); } finally { setBusy(false); }
  };
  const sign = async () => {
    setErr('');
    try {
      const r = await fetch(api('/api/tasks/' + res.task_id + '/sign'), { method: 'POST', headers: H });
      const j = await r.json().catch(() => null);
      if (r.ok && j && j.ok) { setRes({ ...res, status: 'signed' }); }
      else setErr(scrubDetail((j && j.detail) || JSON.stringify(j) || 'HTTP ' + r.status));
    } catch { setErr('Network unreachable.'); }
  };
  const cur = res ? res.state : null;
  return (
    <div>
      <Card title={T(lang, 'taskTitle') + ' — ' + user.role}>
        <textarea className="input" style={{ width: '100%' }} rows={3} value={q}
          onChange={(e) => setQ(e.target.value)} />
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn" disabled={busy} onClick={run}>
            {busy ? <Spinner label="…" /> : 'Run pipeline (PLAN → LOG)'}
          </button>
        </div>
        <ErrorNote error={err} />
      </Card>
      {res && (
        <div className="grid-2">
          <Card title="Live plan / state">
            <ul className="flow-list">
              {(res.plan || []).filter((x) => x !== 'DONE').map((s) => {
                const idx = WORKFLOW.indexOf(s); const curIdx = WORKFLOW.indexOf(cur);
                return <li key={s} className={s === cur ? 'now' : idx < curIdx || cur === 'LOG' ? 'done' : ''}>{s}</li>;
              })}
            </ul>
            <h4 className="mut" style={{ fontSize: 12.5, margin: '12px 0 6px' }}>Events</h4>
            <ul style={{ fontSize: 12, paddingLeft: 18, margin: 0 }}>
              {events.map((e, i) => <li key={i}><b>{e.state}</b> — {e.detail}</li>)}
            </ul>
          </Card>
          <div>
            <Card title="Access-control decision">
              <div className="chip-row">
                <span className="chip chip-green">{T(lang, 'allowed')}: <b>{res.filter_stats?.allowed}</b></span>
                <span className="chip chip-amber">{T(lang, 'blocked')}: <b>{res.filter_stats?.blocked}</b></span>
                <span className="chip">{T(lang, 'docIntel')}</span>
              </div>
              <p className="mut">Filter enforced before generation — role {user.role} never receives blocked chunks.</p>
            </Card>
            <Card title="Draft">
              <pre className="draft">{res.draft}</pre>
              <div className="row" style={{ marginTop: 8 }}>
                <span className={'badge ' + (res.status === 'signed' ? 'badge-green' : 'badge-amber')}>
                  {res.status === 'signed' ? T(lang, 'signed') : res.status === 'draft' ? T(lang, 'draftSt') : res.status}
                </span>
                {user.role === 'approving_manager' && res.status !== 'signed' && (
                  <button className="btn btn-mini" onClick={sign}>Sign with PQC key</button>)}
                {res.task_id && res.status === 'signed' && (
                  <a className="btn btn-ghost btn-mini" style={{ marginLeft: 0 }}
                    href={api('/api/tasks/' + res.task_id + '/download')} download>{T(lang, 'download')}</a>)}
              </div>
            </Card>
            <Card title={T(lang, 'citations') + ' (' + (res.chunks || []).length + ')'}>
              {(res.chunks || []).map((c) => (
                <div className="cite" key={c.chunk_id}>
                  <code>[{c.chunk_id} p.{c.page}]</code>{' '}
                  <span className="badge" style={{ background: tierColor(c.clearance_tier) }}>{c.clearance_tier}</span>{' '}
                  <span className="badge badge-slate">{c.score}</span>
                  <div style={{ marginTop: 4 }}>{c.text}</div>
                  <div className="meta">{c.title} · {c.doc_id} · {c.unit}</div>
                </div>
              ))}
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------------- page ---------------- */

export default function Dashboard({ user, lang, onNav }) {
  const [pqc, setPqc] = useState(null);
  const [st, setSt] = useState(null);
  const [stErr, setStErr] = useState('');
  const legacyTok = getLegacyToken();
  const blind = user.role === 'security_admin';
  const nav = visibleNav(user);

  const load = () => {
    setStErr('');
    request('/api/health', { token: 'none' }).then((j) => setPqc(j.pqc_status)).catch(() => setPqc(null));
    request('/api/v1/ai/status').then(setSt)
      .catch((e) => { setStErr(e.message); setSt({ reachable: false, provider: '?', detail: e.message }); });
  };
  useEffect(load, []);

  return (
    <div>
      <div className="page-head">
        <h2>{T(lang, 'welcome')}, {user.username}</h2>
        <span className="pill pill-role">{user.role}</span>
        <p className="mut">Signed in · {user.permissions?.length || 0} server-resolved permissions</p>
      </div>

      {blind && (
        <div className="note note-warn">
          <b>security_admin</b> — {T(lang, 'contentBlind')}
        </div>
      )}

      <div className="grid-3" style={{ marginBottom: 14 }}>
        <div className="tile">
          <div className="k">{T(lang, 'privacyTitle')}</div>
          <div className="v ok" style={{ fontSize: 15 }}>{T(lang, 'privacyHead')}</div>
          <div className="d">{T(lang, 'privacyDesc')}</div>
        </div>
        <div className="tile">
          <div className="k">{T(lang, 'securityTitle')}</div>
          <div className="v ok" style={{ fontSize: 15 }}>{T(lang, 'securityHead')}</div>
          <div className="d">{T(lang, 'securityDesc')}</div>
        </div>
        <div className="tile">
          <div className="k">{T(lang, 'aiTitle')}</div>
          <div className={'v ' + aiStatus(st).cls} style={{ fontSize: 15 }}>{aiStatus(st).label}</div>
          <div className="d">{T(lang, 'aiDesc')}</div>
        </div>
        <div className="tile">
          <div className="k">{T(lang, 'systemTitle')}</div>
          <div className="v ok" style={{ fontSize: 15 }}>{T(lang, 'systemHead')}</div>
          <div className="d">{T(lang, 'systemDesc')}</div>
        </div>
      </div>

      <div className="grid-3">
        <EgressTile lang={lang} legacyTok={legacyTok} />
        <SigningTile lang={lang} pqc={pqc} />
        <ProviderTile lang={lang} st={st} />
        <HardwareTile lang={lang} legacyTok={legacyTok} />
      </div>

      <Card title={T(lang, 'workflow')}>
        <div className="flow-graphic">
          {WORKFLOW_STEPS.map(([n, label], i) => (
            <React.Fragment key={n}>
              <span className="flow-step"><b>{n}</b>{label}</span>
              {i < WORKFLOW_STEPS.length - 1 && <span className="flow-arrow">→</span>}
            </React.Fragment>
          ))}
        </div>
        <p className="mut">Every step is server-enforced: retrieval is permission-filtered, agents are allow-listed, and each action is audited.</p>
      </Card>

      <Card title={T(lang, 'quickActions')}>
        <div className="row">
          {nav.filter((p) => p.id !== 'dashboard' && p.id !== 'settings').map((p) => (
            <button key={p.id} className="btn btn-ghost" onClick={() => onNav(p.id)}>
              <span className="ico">{p.ico}</span> {T(lang, p.labelKey)}
            </button>
          ))}
          <button className="btn btn-ghost" onClick={() => onNav('settings')}>
            <span className="ico">⚒</span> {T(lang, 'settings')}
          </button>
        </div>
      </Card>

      <Card title={T(lang, 'permissions')}>
        <PermChips permissions={user.permissions} />
        <p className="mut">Resolved server-side at login ({T(lang, 'deniedNote')})</p>
      </Card>

      {blind ? (
        <Card title="Legacy pipeline demo">
          <p className="mut">{T(lang, 'contentBlind')}</p>
          <p className="mut">The legacy query/draft pipeline is intentionally hidden for this role.</p>
        </Card>
      ) : legacyTok ? (
        <TaskRunner legacyTok={legacyTok} user={user} lang={lang} />
      ) : (
        <Card title="Legacy pipeline demo">
          <p className="mut">{T(lang, 'noLegacy')}</p>
        </Card>
      )}
    </div>
  );
}
