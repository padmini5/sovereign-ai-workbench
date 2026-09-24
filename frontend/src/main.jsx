import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './workbench.css';

import { getSession, getAccess, subscribe, logout, setUser } from './session.js';
import { request } from './api.js';
import { T } from './i18n.js';
import { NAV, canSee, visibleNav } from './nav.js';
import { ToastProvider } from './ui.jsx';
import { LANGS } from './ui-strings.js';
import { initTheme } from './theme.js';

import Login from './pages/Login.jsx';
import Dashboard from './pages/Dashboard.jsx';
import Assistant from './pages/Assistant.jsx';
import DocumentsPage from './pages/Documents.jsx';
import ImageAnalysis from './pages/ImageAnalysis.jsx';
import VoicePage from './pages/Voice.jsx';
import AgentsPage from './pages/Agents.jsx';
import Reports from './pages/Reports.jsx';
import Audit from './pages/Audit.jsx';
import Admin from './pages/Admin.jsx';
import SettingsPage from './pages/Settings.jsx';
import SystemCapabilities from './pages/SystemCapabilities.jsx';
import Attendance from './pages/Attendance.jsx';
import Worksheets from './pages/Worksheets.jsx';
import Spreadsheets from './pages/Spreadsheets.jsx';
import Performance from './pages/Performance.jsx';
import Employees from './pages/Employees.jsx';
import Profile from './pages/Profile.jsx';
import Schedule from './pages/Schedule.jsx';

/* Workbench shell: sidebar + header, hash routing, role-adapted nav
   (UI-visibility only — the backend enforces every endpoint independently),
   12-language chrome, session verify/refresh wiring. */

const UI_LANG_KEY = 'sov_ui_lang';

function readRoute() {
  const h = (location.hash || '').replace(/^#\/?/, '');
  /* Legacy/alternate paths land on the one main workspace (Dashboard):
     the old workspace route, the generic work path, and login/forgot
     (handled by the Login screen when signed out). */
  if (h === 'workspace' || h === 'work' || h === 'login' || h === 'forgot') {
    return 'dashboard';
  }
  return h || 'dashboard';
}

function useRoute() {
  const [route, setRoute] = useState(readRoute);
  useEffect(() => {
    const on = () => setRoute(readRoute());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  const go = (r) => { location.hash = '#/' + r; window.scrollTo(0, 0); };
  return [route, go];
}

function Shell() {
  const [, setTick] = useState(0);
  useEffect(() => subscribe(() => setTick((t) => t + 1)), []);

  const sess = getSession();
  const user = sess?.user;
  const tok = getAccess();
  const [route, go] = useRoute();
  const [lang, setLangState] = useState(
    user?.lang || localStorage.getItem(UI_LANG_KEY) || 'en',
  );
  const [askDoc, setAskDoc] = useState(null);
  const [askCtx, setAskCtx] = useState(null);
  const [meErr, setMeErr] = useState('');

  const setLang = (v) => {
    setLangState(v);
    localStorage.setItem(UI_LANG_KEY, v);
    if (getSession()) request('/api/v1/auth/language', { method: 'PUT', body: JSON.stringify({ lang: v }) }).catch(() => {});
  };

  /* Session verify: re-resolve the server-side truth (role, permissions,
     lang) via /auth/me; api.js handles a single silent refresh on 401. */
  useEffect(() => {
    if (!tok) { setMeErr(''); return; }
    let dead = false;
    request('/api/v1/auth/me').then((me) => {
      if (dead) return;
      setUser(me);
      if (me.lang && me.lang !== lang) setLangState(me.lang);
      setMeErr('');
    }).catch((e) => { if (!dead) setMeErr(e.message); });
    return () => { dead = true; };
  }, [tok]);

  const doLogout = async () => { await logout(); go('dashboard'); };

  if (!sess || !user) {
    return (
      <ToastProvider>
        <Login lang={lang} setLang={setLang} />
      </ToastProvider>
    );
  }

  const nav = visibleNav(user);
  const current = NAV.find((p) => p.id === route);
  const visible = current && canSee(current, user) ? current : null;
  const active = visible ? current : NAV[0];

  const onAsk = (doc) => { setAskDoc(doc); setAskCtx(null); go('assistant'); };
  const onAskWork = ({ text, docIds } = {}) => {
    setAskDoc(null); setAskCtx({ text: text || '', docIds: docIds || [] }); go('assistant');
  };

  const renderPage = () => {
    if (!visible) {
      return (
        <div className="card">
          <h3>Restricted</h3>
          <p className="mut">
            {current
              ? `Your role (${user.role}) does not see this page — and the API returns 403 too.`
              : 'Unknown page.'}
          </p>
        </div>
      );
    }
    switch (active.id) {
      case 'dashboard': return <Dashboard user={user} lang={lang} onNav={go} />;
      case 'employees': return <Employees tok={tok} user={user} lang={lang} />;
      case 'profile': return <Profile tok={tok} user={user} lang={lang} />;
      case 'attendance': return <Attendance tok={tok} user={user} lang={lang} />;
      case 'schedule': return <Schedule tok={tok} user={user} lang={lang} />;
      case 'worksheets': return <Worksheets tok={tok} user={user} lang={lang} onAskWork={onAskWork} />;
      case 'spreadsheets': return <Spreadsheets tok={tok} user={user} lang={lang} />;
      case 'performance': return <Performance tok={tok} user={user} lang={lang} onAskWork={onAskWork} onNav={go} />;
      case 'assistant': return <Assistant tok={tok} user={user} askDoc={askDoc} askCtx={askCtx} lang={lang} />;
      case 'documents': return <DocumentsPage tok={tok} user={user} onAsk={onAsk} lang={lang} />;
      case 'images': return <ImageAnalysis tok={tok} user={user} onAsk={onAsk} lang={lang} />;
      case 'voice': return <VoicePage user={user} lang={lang} />;
      case 'agents': return <AgentsPage tok={tok} user={user} lang={lang} />;
      case 'reports': return <Reports tok={tok} user={user} lang={lang} />;
      case 'audit': return <Audit lang={lang} />;
      case 'admin': return <Admin tok={tok} user={user} lang={lang} />;
      case 'system': return <SystemCapabilities user={user} lang={lang} />;
      case 'settings': return (
        <SettingsPage user={user} lang={lang} setLang={setLang} onLogout={doLogout} />
      );
      default: return null;
    }
  };

  return (
    <ToastProvider>
      <div className="shell">
        <header className="shell-head">
          <div className="brand">
            <div className="brand-mark"><img src="/sihlogo.jpg" alt="Sovereign AI Workbench logo" /></div>
            <div>
              <h1>Sovereign AI Workbench</h1>
              <div className="sub">{T(lang, 'subtitle') || 'Secure Local AI for Sensitive Documents'}</div>
            </div>
          </div>
          <div className="head-right">
            <span className="pill pill-role" title={T(lang, 'permissions')}>{user.role}</span>
            <span className="pill">{user.username}</span>
            <select value={lang} onChange={(e) => setLang(e.target.value)} aria-label={T(lang, 'language')}>
              {LANGS.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
            </select>
            <button className="btn btn-ghost btn-mini" style={{ marginLeft: 0 }} onClick={doLogout}>
              {T(lang, 'logout')}
            </button>
          </div>
        </header>

        <nav className="sidebar" aria-label="Main">
          <div className="nav-section">Workbench</div>
          {nav.map((p) => (
            <button key={p.id}
              className={'nav-btn' + (active.id === p.id ? ' active' : '')}
              onClick={() => go(p.id)}>
              <span className="ico">{p.ico}</span> {T(lang, p.labelKey)}
            </button>
          ))}
          <div className="sidebar-foot">
            <div className="mut">zero egress · audit-logged · RBAC server-side</div>
          </div>
        </nav>

        <main className="main">
          {meErr && <div className="err-note"><span className="err-icon">⚠</span> {meErr}</div>}
          {renderPage()}
        </main>
      </div>
    </ToastProvider>
  );
}

createRoot(document.getElementById('root')).render(<Shell />);

/* Theme is applied from localStorage before first paint where possible. */
initTheme();
