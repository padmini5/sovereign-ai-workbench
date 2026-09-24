import React, { useEffect, useState } from 'react';
import { T } from '../i18n.js';
import { request } from '../api.js';
import { Card } from '../ui.jsx';

/* System & Capabilities (Step 4): a plain-language, honest inventory of what
   this deployment does — what is fixed, what is configurable, what is
   optional. Technical architecture stays off the Dashboard; nothing here
   claims a capability that is not actually present.
   Final PS pass adds: Sovereignty Evidence (the real protected processing
   path + live local model routing), the PS Coverage status panel, and the
   supported SIH demonstrations — all read honestly, never faked. */

const CAPS = [
  { t: 'Private AI processing', s: 'Included',
    d: 'Answers are produced by services running inside this deployment. No external AI provider, cloud API, or telemetry is ever contacted.' },
  { t: 'Role-based access control', s: 'Included',
    d: 'Every account carries a server-resolved role. The backend re-checks permissions on every request — hiding menu items is only a courtesy, never the control.' },
  { t: 'Permission-aware document search', s: 'Included',
    d: 'Questions retrieve only from documents your role is authorized to read. Unauthorized content is filtered out before any answer is drafted.' },
  { t: 'Task-based capability routing', s: 'Included',
    d: 'Each request is classified on the server as General, Document, or Coding work and handled by the matching configured capability. Users never choose model names.' },
  { t: 'General and document answers', s: 'Configurable',
    d: 'Summaries, explanations, and questions over your files. The serving model is chosen with the SOV_MODEL_GENERAL setting; the safe default is preconfigured.' },
  { t: 'Coding work', s: 'Configurable',
    d: 'Generated code and tests use the SOV_MODEL_CODING setting and execute only inside the locked-down local sandbox (no network, read-only files, dropped privileges, hard limits, automatic cleanup).' },
  { t: 'Image understanding (vision)', s: 'Optional',
    d: 'Visual question-answering runs when a local vision model is configured with SOV_MODEL_VISION. Until then the Image Analysis page says so honestly instead of faking results.' },
  { t: 'Text recognition for images', s: 'Optional',
    d: 'Extracting printed text from uploaded images runs locally. If the recognition engine is not installed on the server, the page reports that state plainly and previews keep working.' },
  { t: 'Speech dictation and read-aloud', s: 'Optional',
    d: 'Speaking a question and hearing answers aloud are available when local speech services are configured; otherwise the page says so and typed input still works.' },
  { t: 'Multilingual interface', s: 'Included',
    d: 'The interface and AI answers can be viewed in English plus the 12 supported Indian languages. Technical identifiers, citations, and filenames stay in English for accuracy.' },
  { t: 'Human approval workflow', s: 'Included',
    d: 'Inspection analysis ends in an approval note that waits for a human decision. Nothing is auto-approved, and every decision is written to the audit trail.' },
  { t: 'Audit trail', s: 'Included',
    d: 'Sign-ins, document access, AI questions, agent runs, and approvals are recorded with actor, action, and outcome for later review.' },
  { t: 'Local-only deployment', s: 'Deployment',
    d: 'The stack runs in Docker with the database internal-only and the app bound to localhost. Public exposure happens only through your own HTTPS reverse proxy.' },
];

/* Sovereignty evidence: the ACTUAL protected AI processing path implemented
   by this stack (authentication -> RBAC -> resource authorization ->
   permission-aware retrieval -> local model -> controlled sandboxed tools ->
   human approval -> audit). Model names below are read live from the
   service status, never hardcoded. */
const PATH_STEPS = ['USER', 'AUTHENTICATION', 'RBAC / RESOURCE AUTHORIZATION',
  'AUTHORIZED DATA', 'LOCAL AI', 'CONTROLLED TOOLS', 'HUMAN APPROVAL', 'AUDIT'];

/* PS coverage: one honest, friendly status per problem-statement
   capability. "Not demonstrated" rows are shown transparently — an
   unavailable capability is never presented as working. */
const PS_COVERAGE = [
  { cap: 'Local/private AI', status: 'Working' },
  { cap: 'Role-based access', status: 'Working' },
  { cap: 'Permission-aware RAG', status: 'Working' },
  { cap: 'Agentic workflow', status: 'Working' },
  { cap: 'Automatic model routing', status: 'Working' },
  { cap: 'Coding sandbox', status: 'Working' },
  { cap: 'Human approval', status: 'Working' },
  { cap: 'Audit trail', status: 'Working' },
  { cap: 'OCR / scanned documents', status: 'Working' },
  { cap: 'Word approval-note generation', status: 'Working' },
  { cap: 'Spreadsheet capability', status: 'Working (verified)' },
  { cap: 'Vision analysis', status: 'Configured only when local vision model is available' },
  { cap: 'Handwritten-note understanding', status: 'Not demonstrated' },
  { cap: 'Engineering drawing/P&ID understanding', status: 'Not demonstrated' },
  { cap: 'Zero-egress evidence', status: 'Architecture + evidence view' },
];

/* Demonstrations that can actually be performed end-to-end today. */
const SIH_DEMOS = [
  { title: 'Inspection Intelligence',
    flow: 'Inspection report → OCR → authorized SOP retrieval → findings → approval note → human approval → audit' },
  { title: 'Coding Intelligence',
    flow: 'Coding request → automatic task routing → code generation → secure sandbox → tests → verified result' },
  { title: 'Role-aware AI',
    flow: 'Login as different roles → different authorized capabilities/data' },
  { title: 'Sovereign Architecture',
    flow: 'Local processing → authorization → local AI → audit' },
];

const badgeClass = (s) => (s === 'Included' ? 'badge badge-green'
  : s === 'Optional' ? 'badge badge-slate' : 'badge badge-amber');

const psBadge = (s) => {
  if (s.startsWith('Working')) return 'badge badge-green';
  if (s.startsWith('Configured')) return 'badge badge-amber';
  return 'badge badge-slate';
};

export default function SystemCapabilities({ user, lang }) {
  const [svc, setSvc] = useState(undefined); // undefined=loading, null=unavailable
  const [mon, setMon] = useState(null);
  useEffect(() => {
    request('/api/v1/ai/status').then(setSvc).catch(() => setSvc(null));
    /* Live zero-egress counter: v1 admin overview first (covers the primary
       ADMIN demo login), then the legacy monitor endpoint (covers legacy
       accounts); hidden when neither is permitted — never faked. */
    request('/api/v1/admin/overview')
      .then((j) => setMon((j && j.system) || null))
      .catch(() => request('/api/monitor', { token: 'legacy' })
        .then(setMon).catch(() => setMon(null)));
  }, []);

  const routing = (svc && svc.routing) || null;
  const cell = (val, emptyMsg) => {
    if (svc === undefined) return '…';
    if (svc === null) return 'Status unavailable';
    return val || emptyMsg;
  };

  return (
    <div>
      <div className="page-head">
        <h2>{T(lang, 'systemCaps')}</h2>
        <p className="mut">
          What this deployment includes, what is configurable, and what is optional.
        </p>
      </div>
      <div className="grid-2">
        {CAPS.map((c) => (
          <Card key={c.t} title={c.t}
            actions={<span className={badgeClass(c.s)}>{c.s}</span>}>
            <p className="mut" style={{ margin: 0, fontSize: 13 }}>{c.d}</p>
          </Card>
        ))}
      </div>

      <div style={{ marginTop: 14 }}>
        <Card title="Sovereignty Evidence">
          <p className="mut" style={{ margin: '0 0 8px', fontSize: 13 }}>
            Protected AI processing path
          </p>
          <div className="flow-graphic" aria-label="Protected AI processing path">
            {PATH_STEPS.map((p, i) => (
              <React.Fragment key={p}>
                {i > 0 ? <span className="flow-arrow">→</span> : null}
                <span className="flow-step">{p}</span>
              </React.Fragment>
            ))}
          </div>
          <p style={{ margin: '10px 0 6px', fontSize: 13 }}>
            External AI providers are not used by the protected AI workflow.
          </p>
          <p className="mut" style={{ margin: '0 0 6px', fontSize: 13 }}>
            Local model routing
          </p>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr><th>Task type</th><th>Local capability</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td>General / Document</td>
                  <td>{cell(routing && routing.general, 'Not configured')}</td>
                </tr>
                <tr>
                  <td>Coding</td>
                  <td>{cell(routing && routing.coding, 'Not configured')}</td>
                </tr>
                <tr>
                  <td>Vision</td>
                  <td>{cell(routing && routing.vision,
                    'Vision capability: Not configured in this deployment.')}</td>
                </tr>
              </tbody>
            </table>
          </div>
          {mon && typeof mon.app_egress_connections === 'number' ? (
            <p className="mut" style={{ margin: '8px 0 0', fontSize: 12 }}>
              {T(lang, 'zeroEgress')}: {mon.app_egress_connections}
            </p>
          ) : null}
        </Card>
      </div>

      <div style={{ marginTop: 14 }}>
        <Card title="PS Coverage">
          <p className="mut" style={{ margin: '0 0 8px', fontSize: 13 }}>
            A transparent look at each SIH problem-statement capability in
            this prototype — including what is not demonstrated yet.
          </p>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr><th>Capability</th><th>Prototype status</th></tr>
              </thead>
              <tbody>
                {PS_COVERAGE.map((r) => (
                  <tr key={r.cap}>
                    <td>{r.cap}</td>
                    <td><span className={psBadge(r.status)}>{r.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <div style={{ marginTop: 14 }}>
        <Card title="SIH Demonstration">
          <p className="mut" style={{ margin: '0 0 8px', fontSize: 13 }}>
            The demonstrations supported end-to-end today.
          </p>
          <div className="grid-2">
            {SIH_DEMOS.map((d) => (
              <Card key={d.title} title={d.title}>
                <p className="mut" style={{ margin: 0, fontSize: 13 }}>{d.flow}</p>
              </Card>
            ))}
          </div>
        </Card>
      </div>

      <p className="mut" style={{ marginTop: 10, fontSize: 12 }}>
        Signed in as {user.username} ({user.role}) · permissions are resolved
        by the server on every request.
      </p>
    </div>
  );
}
