import React, { useEffect, useState } from 'react';
import { login } from '../session.js';
import { request } from '../api.js';
import { LANGS } from '../ui-strings.js';
import { T } from '../i18n.js';
import { Spinner } from '../ui.jsx';

/* Enterprise sign-in. Organization email + password only; the backend owns
   authentication, JWT, RBAC, account status, and domain allowlist checks.
   Demo cards below only FILL the form — the user always presses Sign in. */

const STRIP_DEMO = ((import.meta.env.VITE_DEMO_CREDS ?? '1') === '0');

// All 11 demo accounts shown as visible cards (5 primary + 6 legacy).
// Cards only FILL the form — the user always presses Sign in.
const DEMO_ROLES = [
  { name: 'Employee', user: 'employee@company.com', pass: 'Employee@2026#R4m!',
    blurb: 'View your work' },
  { name: 'Operator', user: 'operator@company.com', pass: 'Operator@2026#T8q!',
    blurb: 'Operations work' },
  { name: 'Reviewer', user: 'reviewer@company.com', pass: 'Reviewer@2026#V6n!',
    blurb: 'Review work' },
  { name: 'Manager', user: 'manager@company.com', pass: 'Manager@2026#K7p!',
    blurb: 'Manage teams' },
  { name: 'Admin', user: 'admin@company.com', pass: 'Admin@2026#S9x!',
    blurb: 'Full administration' },
];
const DEMO_LEGACY = [
  { name: 'Field Engineer', user: 'field1', pass: 'field123',
    blurb: 'Hands-on docs + AI assistance' },
  { name: 'Process Engineer', user: 'process1', pass: 'process123',
    blurb: 'Field work + report drafting' },
  { name: 'Safety Inspector', user: 'safety1', pass: 'safety123',
    blurb: 'Inspect documents with AI help' },
  { name: 'Approving Manager', user: 'manager1', pass: 'manager123',
    blurb: 'Workflow, reports, model visibility' },
  { name: 'Security Admin', user: 'admin1', pass: 'admin123',
    blurb: 'Users, audit, models, system (content-blind)' },
  { name: 'Auditor', user: 'audit1', pass: 'audit123',
    blurb: 'Read-only audit, reports, models' },
];
const DEMO_ACCOUNTS = [...DEMO_ROLES, ...DEMO_LEGACY];

const HOME_BY_ROLE = {
  EMPLOYEE: 'dashboard', OPERATOR: 'dashboard', USER: 'dashboard',
  field_engineer: 'dashboard', process_engineer: 'dashboard',
  safety_inspector: 'dashboard',
  REVIEWER: 'dashboard', MANAGER: 'dashboard', approving_manager: 'dashboard',
  ADMIN: 'dashboard', security_admin: 'admin', auditor: 'audit',
};

export default function Login({ lang, setLang }) {
  const [u, setU] = useState('');
  const [p, setP] = useState('');
  const [showP, setShowP] = useState(false);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [demoOk, setDemoOk] = useState(false);
  const [domains, setDomains] = useState([]);

  useEffect(() => {
    request('/api/config', { token: 'none' })
      .then((j) => {
        setDemoOk(!!(j && j.demo_login_enabled));
        if (Array.isArray(j.email_domains)) setDomains(j.email_domains);
      })
      .catch(() => setDemoOk(false));
  }, []);

  const go = async (uu, pp) => {
    const user = (uu ?? u).trim();
    const pass = pp ?? p;
    if (!user || !pass) { setErr(T(lang, 'invalidCreds')); return; }
    if (user.includes('@') && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(user)) {
      setErr(T(lang, 'invalidEmail')); return;
    }
    setBusy(true); setErr('');
    try {
      const me = await login(user, pass); // session.subscribe drives the shell
      const home = HOME_BY_ROLE[me?.role] || 'dashboard';
      location.hash = '#/' + home;
    } catch (e) {
      const msg = String(e.message || '').toLowerCase();
      if (e.status === 401) setErr(T(lang, 'invalidCreds'));
      else if (e.status === 403 && (msg.includes('suspend') || msg.includes('disabled'))) setErr(T(lang, 'accountSuspended'));
      else if (e.status === 403) setErr(T(lang, 'domainBlocked'));
      else if (e.status === 429) setErr(T(lang, 'throttled'));
      else setErr(T(lang, 'serverUnreachable'));
    } finally { setBusy(false); }
  };

  const fill = (email, pass) => { setU(email); setP(pass); setErr(''); };

  // Forgot-password (phone-verified demo OTP) dialog state.
  const [fpOpen, setFpOpen] = useState(false);
  const [fpStep, setFpStep] = useState(1);
  const [fp, setFp] = useState({ email: '', phone: '', otp: '', np: '', cp: '' });
  const [fpMsg, setFpMsg] = useState('');
  const [fpBusy, setFpBusy] = useState(false);

  const fpRequest = async () => {
    setFpBusy(true); setFpMsg('');
    try {
      const j = await request('/api/v1/auth/password/forgot/request',
        { token: 'none', method: 'POST', body: { email: fp.email.trim(), phone: fp.phone.trim() } });
      setFpMsg(j.message + (j.demo_otp ? ` (Demo code: ${j.demo_otp}. ${j.demo_notice})` : ''));
      setFpStep(2);
    } catch (e) { setFpMsg(e.message); }
    finally { setFpBusy(false); }
  };
  const fpConfirm = async () => {
    setFpBusy(true); setFpMsg('');
    try {
      const j = await request('/api/v1/auth/password/forgot/confirm',
        { token: 'none', method: 'POST',
          body: { email: fp.email.trim(), otp: fp.otp.trim(), new_password: fp.np, confirm_password: fp.cp } });
      setFpMsg(j.message);
      setFpStep(3);
    } catch (e) { setFpMsg(e.message); }
    finally { setFpBusy(false); }
  };

  return (
    <div className="login-wrap">
      <div className="login-grid">
        <div className="login-box">
          <div className="brand" style={{ marginBottom: 10 }}>
            <div className="brand-mark"><img src="/sihlogo.jpg" alt="Sovereign AI Workbench logo" /></div>
            <div>
              <h2 style={{ fontSize: 21 }}>Sovereign AI Workbench</h2>
              <div className="sub" style={{ margin: 0 }}>{T(lang, 'productTag')}</div>
            </div>
          </div>
          <p className="mut">{T(lang, 'trustLine')}</p>

          <label className="field">
            {T(lang, 'orgEmail')}
            <input className="input" autoComplete="username" placeholder="name@company.com"
              value={u} onChange={(e) => setU(e.target.value)} />
          </label>
          {domains.length > 0 && (
            <p className="mut" style={{ marginTop: -6 }}>
              {T(lang, 'allowedDomains')}: {domains.join(', ')}
            </p>
          )}
          <label className="field">
            {T(lang, 'password')}
            <span className="pw-wrap">
              <input className="input" type={showP ? 'text' : 'password'}
                autoComplete="current-password" value={p}
                onChange={(e) => setP(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && go()} />
              <button type="button" className="btn btn-ghost btn-mini pw-toggle"
                onClick={() => setShowP((s) => !s)}>
                {showP ? T(lang, 'hidePassword') : T(lang, 'showPassword')}
              </button>
            </span>
          </label>
          <label className="field">
            {T(lang, 'languagePref')}
            <select className="select" value={lang} onChange={(e) => setLang(e.target.value)}>
              {LANGS.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
            </select>
          </label>

          {err && <p className="err-note" role="alert"><span className="err-icon">⚠</span> {err}</p>}

          <button className="btn" style={{ width: '100%' }} disabled={busy} onClick={() => go()}>
            {busy ? <Spinner label={T(lang, 'signingIn')} /> : T(lang, 'signInSecurely')}
          </button>
          <div className="row" style={{ justifyContent: 'space-between', marginTop: 8 }}>
            <button type="button" className="linklike" onClick={() => { setFpOpen(true); setFpStep(1); setFpMsg(''); }}>
              {T(lang, 'forgotPassword')}
            </button>
          </div>

          {fpOpen && (
            <div className="card" style={{ marginTop: 12 }}>
              <h3 style={{ fontSize: 14 }}>{T(lang, 'forgotPassword')}</h3>
              {fpStep === 1 && (
                <div>
                  <label className="field">{T(lang, 'orgEmail')}
                    <input className="input" value={fp.email}
                      onChange={(e) => setFp({ ...fp, email: e.target.value })} placeholder="name@company.com" />
                  </label>
                  <label className="field">{T(lang, 'phone')}
                    <input className="input" value={fp.phone}
                      onChange={(e) => setFp({ ...fp, phone: e.target.value })} placeholder="+91-98XXXXXXXX" />
                  </label>
                  <button className="btn btn-mini" disabled={fpBusy} onClick={fpRequest}>
                    {fpBusy ? <Spinner label="…" /> : T(lang, 'sendCode')}
                  </button>
                </div>
              )}
              {fpStep === 2 && (
                <div>
                  <label className="field">{T(lang, 'otp')}
                    <input className="input" value={fp.otp}
                      onChange={(e) => setFp({ ...fp, otp: e.target.value })} placeholder="6-digit code" />
                  </label>
                  <label className="field">{T(lang, 'newPassword')}
                    <input className="input" type="password" value={fp.np}
                      onChange={(e) => setFp({ ...fp, np: e.target.value })} />
                  </label>
                  <label className="field">{T(lang, 'confirmPassword')}
                    <input className="input" type="password" value={fp.cp}
                      onChange={(e) => setFp({ ...fp, cp: e.target.value })} />
                  </label>
                  <div className="row">
                    <button className="btn btn-mini" disabled={fpBusy} onClick={fpConfirm}>
                      {fpBusy ? <Spinner label="…" /> : T(lang, 'resetPassword')}
                    </button>
                    <button className="btn btn-ghost btn-mini" onClick={() => setFpStep(1)}>{T(lang, 'cancel')}</button>
                  </div>
                </div>
              )}
              {fpStep === 3 && (
                <div>
                  <p className="ok" style={{ fontSize: 13 }}>{T(lang, 'passwordChanged')}</p>
                  <button className="btn btn-ghost btn-mini" onClick={() => setFpOpen(false)}>{T(lang, 'close')}</button>
                </div>
              )}
              {fpMsg && fpStep !== 3 && <p className="mut" style={{ fontSize: 12 }}>{fpMsg}</p>}
              {fpStep !== 3 && (
                <div style={{ marginTop: 6 }}>
                  <button type="button" className="linklike" onClick={() => setFpOpen(false)}>{T(lang, 'close')}</button>
                </div>
              )}
            </div>
          )}

        </div>

        {!STRIP_DEMO && demoOk && (
          <div className="demo-panel">
            <h3 style={{ fontSize: 15 }}>{T(lang, 'demoAccounts')}</h3>
            <div className="demo-cards">
              {DEMO_ACCOUNTS.map((d) => (
                <div className="demo-card" key={d.user}>
                  <b>{d.name}</b>
                  <span className="mut">{d.blurb}</span>
                  <code>{d.user}</code>
                  <code title="Demo password">{d.pass}</code>
                  <button type="button" className="btn btn-ghost btn-mini" disabled={busy}
                    onClick={() => fill(d.user, d.pass)}>
                    {T(lang, 'useAccount')}
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
