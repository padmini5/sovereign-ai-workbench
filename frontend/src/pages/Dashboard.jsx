import React, { useEffect, useState } from 'react';
import { request } from '../api.js';
import { T } from '../i18n.js';
import { hasPerm } from '../rbac.js';
import { Card, Spinner, ErrorNote, Empty, fmtTs } from '../ui.jsx';
import { todayLabel } from '../dash-date.js';

/* Dashboard = the single main workspace (Step 4): today's attendance,
   current work, progress & performance, entry points into the feature
   areas, and recent activity. Data comes only from the scoped endpoints
   GET /api/v1/work/me and GET /api/v1/work/activity. Role gating here is
   presentation only — every endpoint re-enforces permissions server-side.
   No demo/debug pipeline content, no technical monitor tiles. */

const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, ' ') : '');

function Tile({ k, v, d }) {
  return (
    <div className="tile">
      <div className="k">{k}</div>
      <div className="v" style={{ fontSize: 19 }}>{v}</div>
      {d ? <div className="d">{d}</div> : null}
    </div>
  );
}

export default function Dashboard({ user, lang, onNav }) {
  const [me, setMe] = useState(null);
  const [items, setItems] = useState(null);
  const [err, setErr] = useState('');
  const blind = user.role === 'security_admin';

  const load = () => {
    setErr('');
    setMe(null);
    request('/api/v1/work/me').then(setMe)
      .catch((e) => { setErr(e.message); setMe(null); });
    request('/api/v1/work/activity').then((j) => setItems(j.items || []))
      .catch(() => setItems([]));
  };
  useEffect(load, []);

  const att = me?.attendance_today || null;
  const openTasks = me?.tasks_open || [];
  const byStatus = me?.tasks_by_status || {};
  const perf = me?.performance || null;
  const perfPct = perf && perf.overall != null ? Math.round(perf.overall * 100) : null;

  /* Feature entry points — role-gated client-side, enforced server-side. */
  const cards = [
    hasPerm(user, 'AI_CHAT') && {
      key: 'assistant', title: T(lang, 'assistant'),
      desc: 'Ask questions about your work and your authorized files.',
      ico: '✦', to: 'assistant',
    },
    hasPerm(user, 'DOCUMENT_READ') && {
      key: 'documents', title: T(lang, 'documents'),
      desc: 'Upload reports, review extracted text, and open AI answers with source evidence.',
      ico: '▤', to: 'documents',
    },
    hasPerm(user, 'DOCUMENT_READ') && {
      key: 'images', title: T(lang, 'images'),
      desc: 'Private image previews and local analysis — images never leave this deployment.',
      ico: '▣', to: 'images',
    },
    hasPerm(user, 'AI_AGENT_USE') && {
      key: 'coding', title: T(lang, 'cardCoding'),
      desc: 'Generate code and tests, then verify them inside the secure local sandbox.',
      ico: '⌘', to: 'agents',
    },
    hasPerm(user, 'AI_AGENT_USE') && {
      key: 'inspect', title: T(lang, 'cardInspect'),
      desc: 'Run the inspection analysis, review the approval note, and make the human decision.',
      ico: '⌬', to: 'agents',
    },
  ].filter(Boolean);

  const isAdminish = ['ADMIN', 'security_admin'].includes(user.role);

  return (
    <div>
      <div className="page-head">
        <h2>{T(lang, 'welcome')}, {user.username}</h2>
        <span className="pill">{todayLabel()}</span>
        <span className="pill pill-role" title={T(lang, 'permissions')}>{user.role}</span>
        <button className="btn btn-ghost btn-mini" style={{ marginLeft: 'auto' }}
          onClick={() => onNav('profile')}>{T(lang, 'myProfile')}</button>
        <p className="mut">Signed in · {user.permissions?.length || 0} server-resolved permissions</p>
      </div>

      {blind && (
        <div className="note note-warn">
          <b>security_admin</b> — {T(lang, 'contentBlind')}
        </div>
      )}

      <ErrorNote error={err} onRetry={load} />

      {me === null && !err && <Spinner label={T(lang, 'loading')} />}

      {me && (
        <>
          <div className="grid-3" style={{ marginBottom: 14 }}>
            <Tile k={T(lang, 'dashAttendance')}
              v={att ? cap(att.status) : T(lang, 'dashNotMarked')}
              d={att ? att.date : 'Attendance for today'} />
            <Tile k={T(lang, 'dashWork')}
              v={openTasks.length}
              d={`${me.tasks_total ?? 0} total work items`} />
            <Tile k={T(lang, 'dashProgress')}
              v={me.completion_pct != null ? `${me.completion_pct}%` : T(lang, 'notEnoughData')}
              d={`${me.tasks_done_30d ?? 0} completed in the last 30 days`} />
            <Tile k={T(lang, 'performance')}
              v={perfPct != null ? `${perfPct} / 100` : T(lang, 'notEnoughData')}
              d={perfPct != null ? 'Performance score' : 'Complete reviewed work to build your score'} />
          </div>

          <Card title={T(lang, 'dashWork')}>
            <div className="chip-row" style={{ marginBottom: 8 }}>
              {Object.entries(byStatus).map(([k, v]) => (
                <span className="chip" key={k}>{cap(k)}: <b>{v}</b></span>
              ))}
              {Object.keys(byStatus).length === 0 && (
                <span className="mut">{T(lang, 'noOpenWork')}</span>
              )}
            </div>
            {openTasks.length === 0 ? (
              <p className="mut">{T(lang, 'noOpenWork')}</p>
            ) : (
              <table className="tbl"><tbody>
                {openTasks.map((t) => (
                  <tr key={t.id}>
                    <td>{t.title}</td>
                    <td><span className="badge badge-amber">{cap(t.status)}</span></td>
                    <td className="mut">{cap(t.priority)} priority</td>
                  </tr>
                ))}
              </tbody></table>
            )}
            <p className="mut" style={{ marginTop: 8, fontSize: 12 }}>
              {me.team_name ? `Team ${me.team_name}: ${me.team_completion_pct ?? 0}% complete · ` : ''}
              Evidence on file: {me.evidence_count ?? 0} · Worksheets pending: {me.worksheets_pending ?? 0}
            </p>
          </Card>

          <Card title={T(lang, 'dashFeatures')}>
            <div className="grid-3">
              {cards.map((c) => (
                <div className="tile" key={c.key}>
                  <div className="k"><span className="ico">{c.ico}</span> {c.title}</div>
                  <div className="d" style={{ minHeight: 44 }}>{c.desc}</div>
                  <button className="btn btn-ghost btn-mini" style={{ marginTop: 6 }}
                    onClick={() => onNav(c.to)}>{T(lang, 'viewDetails')}</button>
                </div>
              ))}
            </div>
          </Card>

          <Card title={T(lang, 'dashActivity')}>
            {!items && <Spinner label={T(lang, 'loading')} />}
            {items && items.length === 0 && (
              <Empty title={T(lang, 'noActivity')} hint="Work, documents, and AI activity will appear here." />
            )}
            {items && items.length > 0 && (
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                {items.slice(0, 6).map((it, i) => (
                  <li key={i} style={{ marginBottom: 4 }}>
                    {it.text} <span className="mut">· {fmtTs(it.ts)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {isAdminish && (
            <Card title="Administration">
              <div className="row">
                <button className="btn btn-ghost" onClick={() => onNav('admin')}>
                  <span className="ico">⛨</span> {T(lang, 'admin')}
                </button>
                <button className="btn btn-ghost" onClick={() => onNav('system')}>
                  <span className="ico">◈</span> {T(lang, 'systemCaps')}
                </button>
                {hasPerm(user, 'AUDIT_READ') && (
                  <button className="btn btn-ghost" onClick={() => onNav('audit')}>
                    <span className="ico">☰</span> {T(lang, 'auditLog')}
                  </button>
                )}
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
