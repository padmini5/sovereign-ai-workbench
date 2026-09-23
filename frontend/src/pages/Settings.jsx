import React, { useEffect, useState } from 'react';
import { request } from '../api.js';
import { getAccess, getRefresh, getLegacyToken, logout } from '../session.js';
import { LANGS } from '../ui-strings.js';
import { T } from '../i18n.js';
import { Card, Spinner, ErrorNote, PermChips, useToast } from '../ui.jsx';

/* Settings: language preference persisted server-side, account + role,
   server-resolved permissions summary, honest session info (client view of
   the JWT exp — display only), and feature availability. */

function tokenExpiry() {
  try {
    const payload = JSON.parse(atob(getAccess().split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return payload.exp ? new Date(payload.exp * 1000) : null;
  } catch { return null; }
}

function SecurityCard({ lang }) {
  const [f, setF] = useState({ cur: '', np: '', cp: '' });
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true); setErr(''); setMsg('');
    try {
      const j = await request('/api/v1/auth/password/change',
        { method: 'POST', body: { current_password: f.cur, new_password: f.np, confirm_password: f.cp } });
      setMsg(j.message || T(lang, 'passwordChanged'));
      setF({ cur: '', np: '', cp: '' });
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Card title={`${T(lang, 'settings')} → ${T(lang, 'securityInfo')} → ${T(lang, 'changePassword')}`}>
      <label className="field">{T(lang, 'currentPassword')}
        <input className="input" type="password" value={f.cur}
          onChange={(e) => setF({ ...f, cur: e.target.value })} />
      </label>
      <label className="field">{T(lang, 'newPassword')}
        <input className="input" type="password" value={f.np}
          onChange={(e) => setF({ ...f, np: e.target.value })} />
      </label>
      <label className="field">{T(lang, 'confirmPassword')}
        <input className="input" type="password" value={f.cp}
          onChange={(e) => setF({ ...f, cp: e.target.value })} />
      </label>
      <ErrorNote error={err} />
      {msg && <p className="ok" style={{ fontSize: 13 }}>{msg}</p>}
      <button className="btn btn-mini" disabled={busy} onClick={go}>
        {busy ? <Spinner label="…" /> : T(lang, 'changePassword')}
      </button>
    </Card>
  );
}

export default function Settings({ user, lang, setLang, onLogout }) {
  const [pref, setPref] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [ai, setAi] = useState(null);
  const [voice, setVoice] = useState(null);
  const [expiry, setExpiry] = useState(null);
  const toast = useToast();
  const canAI = (user.permissions || []).includes('AI_CHAT');

  useEffect(() => {
    setExpiry(tokenExpiry());
    request('/api/v1/auth/language').then((j) => setPref(j.lang)).catch((e) => setErr(e.message));
    if (canAI) {
      request('/api/v1/ai/status').then(setAi).catch(() => setAi(null));
      request('/api/v1/voice/status').then(setVoice).catch(() => setVoice(null));
    }
  }, []);

  const saveLang = async (v) => {
    setLang(v); setBusy(true); setErr('');
    try {
      const j = await request('/api/v1/auth/language', { method: 'PUT', body: JSON.stringify({ lang: v }) });
      setPref(j.lang); toast(T(lang, 'saved'), 'success');
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div>
      <div className="page-head">
        <h2>{T(lang, 'settings')}</h2>
      </div>

      <div className="grid-2">
        <Card title={T(lang, 'languagePref')}>
          <label className="field">
            {T(lang, 'languagePref')}
            <select className="select" value={lang} onChange={(e) => saveLang(e.target.value)} disabled={busy}>
              {LANGS.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
            </select>
          </label>
          <p className="mut">{T(lang, 'languageNote')}</p>
          <p className="mut">Server-stored preference: <code>{pref ?? '…'}</code> {busy && <Spinner />}</p>
          <ErrorNote error={err} />
        </Card>
      </div>

        <Card title={T(lang, 'account')}>
          <table className="tbl"><tbody>
            <tr><td className="mut">{T(lang, 'username')}</td><td>{user.username}</td></tr>
            <tr><td className="mut">{T(lang, 'role')}</td><td><span className="pill pill-role">{user.role}</span></td></tr>
            <tr><td className="mut">ID</td><td><code>{user.id}</code></td></tr>
          </tbody></table>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn btn-danger" onClick={onLogout || logout}>{T(lang, 'signOut')}</button>
          </div>
        </Card>

        <SecurityCard lang={lang} />

      <Card title={T(lang, 'permissions')}>
        <PermChips permissions={user.permissions} />
        <p className="mut">
          Resolved server-side from your role on every request ({T(lang, 'deniedNote')})
        </p>
      </Card>

      <div className="grid-2">
        <Card title={T(lang, 'session')}>
          <table className="tbl"><tbody>
            <tr>
              <td className="mut">{T(lang, 'tokenExpires')}</td>
              <td>{expiry ? expiry.toLocaleString() : '— (unparseable client view)'}
                {expiry && <span className="mut"> · {Math.round((expiry - Date.now()) / 60000)} min</span>}
              </td>
            </tr>
            <tr><td className="mut">v1 access token</td><td>{getAccess() ? 'present' : 'missing'}</td></tr>
            <tr><td className="mut">v1 refresh token</td><td>{getRefresh() ? 'present' : 'missing'}</td></tr>
            <tr><td className="mut">legacy demo token</td><td>{getLegacyToken() ? 'present (legacy pipeline demo unlocked)' : 'absent (pipeline card hidden)'}</td></tr>
          </tbody></table>
          <p className="mut">Client-side view for display only — expiry is enforced by the server on every call.</p>
        </Card>

        <Card title="Feature availability">
          {!canAI && <p className="mut">Your role lacks <code>AI_CHAT</code> — assistant and voice features are hidden (and return 403 server-side).</p>}
          {canAI && !ai && !voice && <Spinner label={T(lang, 'loading')} />}
          {ai && (
            <p style={{ fontSize: 13 }}>
              AI Assistant: <span className={ai.reachable ? 'ok' : 'warn'}>
                {ai.reachable ? T(lang, 'aiReady') : T(lang, 'aiDown')}</span>
            </p>
          )}
          {voice && (
            <div className="chip-row">
              <span className={'chip ' + (voice.stt?.available ? 'chip-green' : 'chip-red')}>
                Voice input • {voice.stt?.available ? 'Ready' : 'Unavailable'}</span>
              <span className={'chip ' + (voice.tts?.available ? 'chip-green' : 'chip-red')}>
                Voice playback • {voice.tts?.available ? 'Ready' : 'Unavailable'}</span>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
