import React, { useEffect, useState } from 'react';
import { apiFetch, hasPerm } from '../rbac.js';
import { T } from '../i18n.js';
import { AgentsView } from '../agents.jsx';
import { Card, Skeleton, ErrorNote, Empty, StateBadge } from '../ui.jsx';

/* Agents page: registry (allow-listed agents, role-gated server-side)
   + the runs console (goal, steps, status, confirm/cancel, errors)
   + the approval-notes panel (status, download, human decisions). */

function RegistryCard({ a }) {
  return (
    <Card title={a.name} actions={<StateBadge state={a.allowed ? 'COMPLETED' : 'CANCELLED'} />}>
      <p className="mut" style={{ minHeight: 34 }}>{a.purpose}</p>
      <div className="chip-row">
        {(a.tools || []).map((t) => <span className="chip" key={t}>{t}</span>)}
      </div>
      <p className="mut" style={{ marginTop: 8 }}>
        Your role may run this agent (server-verified on every run).
      </p>
    </Card>
  );
}

export default function AgentsPage({ tok, user, lang }) {
  const [agents, setAgents] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    apiFetch('/api/v1/agents', tok).then((j) => setAgents(j.agents))
      .catch((e) => { setErr(e.message); setAgents([]); });
  }, [tok]);
  const visible = (agents || []).filter((a) => a.allowed && a.enabled);

  if (!hasPerm(user, 'AI_AGENT_USE')) {
    return (
      <div>
        <div className="page-head"><h2>{T(lang, 'agents')}</h2></div>
        <Card title="Restricted">
          <p className="mut">Your role lacks <code>AI_AGENT_USE</code>. {T(lang, 'deniedNote')}</p>
        </Card>
      </div>
    );
  }

  return (
    <div>
      <div className="page-head">
        <h2>{T(lang, 'agents')}</h2>
        <p className="mut">Plan-then-execute, allow-listed tools, server-side permission checks on every call.</p>
      </div>
      <ErrorNote error={err} />
      {agents === null && <Skeleton rows={3} />}
      {agents !== null && visible.length === 0 && !err &&
        <Card><Empty title={T(lang, 'noResults')} /></Card>}
      <div className="grid-3">
        {visible.map((a) => <RegistryCard key={a.name} a={a} />)}
      </div>
      <AgentsView tok={tok} user={user} />
    </div>
  );
}
