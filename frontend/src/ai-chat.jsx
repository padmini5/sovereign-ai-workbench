import React, { useEffect, useRef, useState } from 'react';
import { apiFetch } from './rbac.js';
import { LANGS, t } from './ui-strings.js';
import { aiStatus, friendlyError } from './status.js';
import { captureVoiceWav } from './voice-capture.js';

/* Phase 2 chat console (+ Step 19 languages): history, streaming, retry,
   clear, language selector, status indicator. Talks ONLY to
   /api/v1/ai (permission-enforced). */

/* 12 Step-19 languages imported from ui-strings.js */

/* Friendly task-routing labels (Step 4): category words only — never
   model names, prompts, or internals. */
const TASK_LABEL = { GENERAL: 'General', DOCUMENT: 'Document', CODING: 'Coding', VISION: 'Image' };

export function StatusPill({ tok }) {
  const [st, setSt] = useState(null);
  useEffect(() => {
    apiFetch('/api/v1/ai/status', tok).then(setSt).catch(() => setSt({ reachable: false }));
  }, [tok]);
  const a = aiStatus(st);
  if (!st) return <span style={s.pill}>Local AI • …</span>;
  return (
    <span style={s.pill}>
      <span style={{ color: a.cls === 'ok' ? 'var(--ok)' : 'var(--warn)' }}>●</span>{' '}{a.label}
    </span>
  );
}

function parseSSE(text) {
  // Minimal SSE parse for the fallback path (non-streaming POST already used).
  const out = [];
  for (const blk of text.split('\n\n')) {
    const m = blk.match(/^data: ([\s\S]*)$/m);
    if (m) out.push(m[1]);
  }
  return out.join('');
}

/* Step 20 voice controls: mic record -> STT -> transcript (auto-sent),
   per-message play/stop via TTS. All calls carry the JWT; STT/TTS/chat
   enforce the same RBAC server-side. */
function VoiceBar({ tok, lang, disabled, onTranscript, onError }) {
  const [rec, setRec] = useState(null);
  const [recording, setRecording] = useState(false);
  const [working, setWorking] = useState(false);
  const start = async () => {
    onError('');
    let handle;
    try {
      handle = await captureVoiceWav(); // user gesture -> mic prompt
    } catch (e) {
      onError(e.message);
      return;
    }
    setRec(handle); setRecording(true);
  };
  const stop = async () => {
    if (!rec) { setRecording(false); return; }
    setRec(null);
    setRecording(false); setWorking(true);
    try {
      const blob = await rec.stop(); // real 16kHz WAV (or honest error)
      const fd = new FormData();
      fd.append('f', blob, 'voice.wav');
      fd.append('lang', lang);
      const r = await fetch('/api/v1/voice/stt', {
        method: 'POST', headers: { Authorization: 'Bearer ' + tok }, body: fd });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw Object.assign(new Error(j.detail || 'STT HTTP'), { status: r.status });
      onTranscript(j.text);
    } catch (e) { onError(friendlyError(e.status, e.message)); }
    finally { setWorking(false); }
  };
  return (
    <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
      {!recording
        ? <button style={s.mini} onClick={start} disabled={disabled || working}
            title="Record voice input">🎙 Mic</button>
        : <button style={{ ...s.mini, borderColor: 'var(--bad)' }} onClick={stop}>⏹ Stop</button>}
      {recording && <span style={{ ...s.mut, color: 'var(--bad)' }}>● recording…</span>}
      {working && <span style={s.mut}>transcribing…</span>}
    </span>
  );
}

function useSpeech(tok, lang, onError) {
  const audioRef = useRef(null);
  const [playing, setPlaying] = useState(-1);
  const stop = () => { try { audioRef.current?.pause(); } catch {} setPlaying(-1); };
  const play = async (idx, text) => {
    onError('');
    stop();
    try {
      const body = text.split('\n\nSources:\n')[0].slice(0, 2000); // speak answer, not citations
      const r = await fetch('/api/v1/voice/tts', {
        method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + tok },
        body: JSON.stringify({ text: body, lang }) });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw Object.assign(new Error(j.detail || 'TTS HTTP'), { status: r.status });
      }
      const url = URL.createObjectURL(await r.blob());
      const a = new Audio(url);
      audioRef.current = a;
      a.onended = () => setPlaying(-1);
      setPlaying(idx);
      await a.play();
    } catch (e) { onError(friendlyError(e.status, e.message)); setPlaying(-1); }
  };
  useEffect(() => () => stop(), []);
  return { playing, play, stop };
}

export function ChatView({ tok, user, askDoc, askCtx, uiLang }) {
  const [convoId, setConvoId] = useState(null);
  const [msgs, setMsgs] = useState([]); // {role, content, tools?}
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [streaming] = useState(true);
  const [routeTask, setRouteTask] = useState('');
  const [err, setErr] = useState('');
  const [lang, setLang] = useState(uiLang || 'en'); // per-request; header pref is default
  const L = (k) => t(uiLang || 'en', k);
  const [tools, setTools] = useState([]);
  const [wantTools, setWantTools] = useState([]);
  const [mode, setMode] = useState('general');
  const [readyDocs, setReadyDocs] = useState([]);
  const [selDocs, setSelDocs] = useState([]);
  const [lastUser, setLastUser] = useState('');
  const bottom = useRef(null);
  const inputRef = useRef(null);
  const fileRef = useRef(null);
  const dataRef = useRef(null);
  const imgRef = useRef(null);
  const camRef = useRef(null);
  const micWrapRef = useRef(null);
  const [uploading, setUploading] = useState('');
  const [sheetBusy, setSheetBusy] = useState(false);
  const [sheetOut, setSheetOut] = useState(null);
  const [revCols, setRevCols] = useState('');
  const [expCols, setExpCols] = useState('');

  const refreshDocs = () => {
    apiFetch('/api/v1/docs?status=READY', tok)
      .then((j) => setReadyDocs(j.documents)).catch(() => {});
  };

  useEffect(() => {
    apiFetch('/api/v1/ai/tools', tok).then((j) => setTools(j.tools)).catch(() => {});
    refreshDocs();
  }, [tok]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth' }); }, [msgs, busy]);
  useEffect(() => { if (uiLang) setLang(uiLang); }, [uiLang]); // header preference wins
  useEffect(() => { // [Ask AI] from Documents tab preselects a doc + doc mode
    if (askDoc?.id) {
      setSelDocs([askDoc.id]);
      setMode('general');
      setReadyDocs((d) => (d.some((x) => x.id === askDoc.id) ? d : [...d, askDoc]));
    }
  }, [askDoc]);
  useEffect(() => { // work pages can preset a question (+ optional doc ids)
    if (askCtx?.text) {
      setInput(askCtx.text);
      if (askCtx.docIds?.length) {
        setSelDocs(askCtx.docIds);
        setReadyDocs((d) => {
          const have = new Set(d.map((x) => x.id));
          const add = askCtx.docIds.filter((id) => !have.has(id))
            .map((id) => ({ id, filename: id }));
          return [...d, ...add];
        });
      }
    }
  }, [askCtx]);

  const payload = (text) => ({
    messages: [...msgs.filter((m) => m.role !== 'sys').map(({ role, content }) => ({ role, content })),
               { role: 'user', content: text }].slice(-20),
    ...(convoId ? { conversation_id: convoId } : {}),
    ...(wantTools.length ? { tools: wantTools } : {}),
    ...(selDocs.length ? { document_ids: selDocs } : {}),
    mode,
    lang,
  });

  const sendStream = async (text) => {
    const r = await fetch('/api/v1/ai/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + tok },
      body: JSON.stringify(payload(text)),
    });
    if (!r.ok) {
      let detail = 'HTTP ' + r.status;
      try { detail = (await r.json()).detail || detail; } catch { /* SSE error frame instead */ }
      throw Object.assign(new Error(detail), { status: r.status });
    }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '', acc = '', cid = null, failed = null;
    setMsgs((m) => [...m, { role: 'user', content: text }, { role: 'assistant', content: '' }]);
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
        const ev = (/^event: (\w+)/m.exec(frame) || [])[1] || 'data';
        const dt = (/^data: ([\s\S]*)$/m.exec(frame) || [])[1] ?? '';
        if (ev === 'meta') cid = dt.trim();
        else if (ev === 'task') setRouteTask(dt.trim());
        else if (ev === 'error') failed = dt;
        else if (ev === 'done') { /* end */ }
        else acc += dt;
        setMsgs((m) => { const c = [...m]; c[c.length - 1] = { role: 'assistant', content: parseSSE(acc) || acc }; return c; });
      }
    }
    if (failed) throw Object.assign(new Error(failed), { status: 502 });
    if (cid) setConvoId(cid);
    return acc;
  };

  const sendOnce = async (text) => {
    const j = await apiFetch('/api/v1/ai/chat', tok, { method: 'POST', body: JSON.stringify(payload(text)) });
    setConvoId(j.conversation_id);
    if (j.task_type) setRouteTask(j.task_type);
    // Backend appends a plain-text "Sources:" section; when structured
    // sources exist we strip it and render them as cards instead.
    const raw = j.message.content;
    const sources = j.sources || [];
    const content = sources.length ? raw.split('\n\nSources:\n')[0] : raw;
    setMsgs((m) => [...m, { role: 'user', content: text },
      { role: 'assistant', content, tools: j.tools_used, sources }]);
  };

  const send = async (retryText) => {
    const text = (retryText ?? input).trim();
    if (!text || busy) return;
    setBusy(true); setErr(''); setLastUser(text); setInput(''); setRouteTask('');
    try { streaming ? await sendStream(text) : await sendOnce(text); }
    catch (e) {
      setErr(e.status === 403
        ? "You don't have permission to perform this action."
        : friendlyError(e.status, e.message));
    }
    finally { setBusy(false); }
  };

  const clear = () => { setMsgs([]); setConvoId(null); setErr(''); setLastUser(''); };
  const speech = useSpeech(tok, lang, setErr);
  const onTranscript = (text) => { setInput(text); send(text); };

  const toggleTool = (n) =>
    setWantTools((w) => (w.includes(n) ? w.filter((x) => x !== n) : [...w, n]));

  /* Workspace uploads: same authorized pipeline as Documents (upload then
     process). Images stay private: stored only, never auto-analyzed. */
  const doUpload = async (file) => {
    if (!file || busy) return;
    setUploading(file.name); setErr('');
    try {
      const fd = new FormData();
      fd.append('f', file, file.name);
      const r = await fetch('/api/v1/docs/upload', {
        method: 'POST', headers: { Authorization: 'Bearer ' + tok }, body: fd });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw Object.assign(new Error(j.detail || ('HTTP ' + r.status)), { status: r.status });
      if (j.kind !== 'image') {
        await apiFetch(`/api/v1/docs/${j.id}/analyze`, tok, { method: 'POST', body: '{}' }).catch(() => {});
      }
      refreshDocs();
    } catch (e) { setErr(friendlyError(e.status, e.message)); }
    finally { setUploading(''); }
  };

  const selFiles = readyDocs.filter((d) => selDocs.includes(d.id));
  const sheetDoc = selFiles.find((d) => d.kind === 'csv' || d.kind === 'xlsx') || null;

  const quickAsk = (text) => {
    if (!text || busy) return;
    if (!selDocs.length) { setErr('Select at least one authorized file first.'); return; }
    send(text);
  };

  const analyzeData = async () => {
    if (!sheetDoc || sheetBusy) return;
    setSheetBusy(true); setErr(''); setSheetOut(null);
    try {
      const j = await apiFetch('/api/v1/work/spreadsheets/analyze', tok, { method: 'POST',
        body: JSON.stringify({ doc_id: sheetDoc.id,
          revenue_cols: revCols.split(',').map((s) => s.trim()).filter(Boolean),
          expense_cols: expCols.split(',').map((s) => s.trim()).filter(Boolean) }) });
      setSheetOut(j);
    } catch (e) { setErr(friendlyError(e.status, e.message)); }
    finally { setSheetBusy(false); }
  };

  const FRIENDLY_TOOLS = { get_my_info: 'My info', list_users: 'People I may see',
    get_system_info: 'System info', list_reports: 'My reports',
    my_work_summary: 'My work summary', get_audit_summary: 'Audit summary',
    get_model_info: 'Service info' };
  const kindLabel = (k) => (k === 'csv' || k === 'xlsx' ? 'Data'
    : k === 'image' ? 'Image' : 'Document');
  const sizeFmt = (b) => (b > 1048576 ? (b / 1048576).toFixed(1) + ' MB'
    : Math.max(1, Math.round((b || 0) / 1024)) + ' KB');

  const SUGGESTIONS = [
    ['Summarize my file', 'Summarize this document'],
    ['Analyze this data', 'Analyze this data: totals, averages, trends, and missing values'],
    ['Find important information', 'Find the important information'],
    ['Ask about my work', 'What should I complete today?'],
    ['Pending work', 'What is my pending work?'],
  ];

  return (
    <div style={s.card}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 4 }}>
        <div>
          <h3 style={{ margin: 0 }}>AI Assistant</h3>
          <div style={s.mut}>What would you like to work with?</div>
          <div style={{ marginTop: 4, display: 'flex', gap: 6, alignItems: 'center' }}>
            <StatusPill tok={tok} />
            <span style={s.mut}>Private AI · Secure Local Processing</span>
          </div>
          {TASK_LABEL[routeTask] && (
            <div style={{ ...s.mut, marginTop: 4 }}>
              Task type: {TASK_LABEL[routeTask]} · Selected local AI capability: {TASK_LABEL[routeTask]}
            </div>
          )}
        </div>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          <label style={s.mut}>{L('language')}{' '}
            <select value={lang} onChange={(e) => setLang(e.target.value)} style={s.sel}>
              {LANGS.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
            </select></label>
          <button style={s.mini} onClick={clear}>{L('clear')}</button>
        </span>
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '10px 0' }}>
        <button style={s.btn} onClick={() => fileRef.current?.click()} disabled={busy || !!uploading}>
          Upload File</button>
        <button style={s.mini} onClick={() => dataRef.current?.click()} disabled={busy || !!uploading}>
          Add Data</button>
        <button style={s.mini} onClick={() => imgRef.current?.click()} disabled={busy || !!uploading}>
          Upload Image</button>
        <button style={s.mini} onClick={() => camRef.current?.click()} disabled={busy || !!uploading}>
          Take Photo</button>
        <button style={s.mini} onClick={() => micWrapRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>
          Voice</button>
        <button style={s.mini} onClick={() => inputRef.current?.focus()}>Ask Question</button>
        <input ref={fileRef} type="file" style={{ display: 'none' }}
          accept=".pdf,.docx,.txt,.csv,.xlsx" onChange={(e) => { doUpload(e.target.files?.[0]); e.target.value = ''; }} />
        <input ref={dataRef} type="file" style={{ display: 'none' }}
          accept=".csv,.xlsx" onChange={(e) => { doUpload(e.target.files?.[0]); e.target.value = ''; }} />
        <input ref={imgRef} type="file" style={{ display: 'none' }}
          accept=".jpg,.jpeg,.png" onChange={(e) => { doUpload(e.target.files?.[0]); e.target.value = ''; }} />
        <input ref={camRef} type="file" style={{ display: 'none' }}
          accept="image/*" capture="environment" onChange={(e) => { doUpload(e.target.files?.[0]); e.target.value = ''; }} />
      </div>
      {uploading && <div style={s.mut}>Uploading {uploading}…</div>}
      <div style={s.mut}>Files you may use: PDF, Word, Text, CSV, Spreadsheet, Image.
        Photos and images are stored privately and are never sent to AI automatically.</div>
      <div style={{ ...s.mut, marginTop: 4 }}>What can I help you with?</div>

      {tools.length > 0 && (
        <div style={{ margin: '8px 0', fontSize: 12, color: 'var(--mut)' }}>
          Private AI tools:{' '}
          {tools.map((t) => (
            <label key={t.name} style={{ marginRight: 10 }} title={t.desc}>
              <input type="checkbox" checked={wantTools.includes(t.name)}
                onChange={() => toggleTool(t.name)} /> {FRIENDLY_TOOLS[t.name] || t.name}</label>))}
        </div>
      )}
      <div style={{ marginBottom: 8, fontSize: 12, color: 'var(--mut)' }}>
        <label>Scope:{' '}
          <select value={mode} onChange={(e) => setMode(e.target.value)} style={s.sel}>
            <option value="general">General</option>
            <option value="my_docs">My files</option>
          </select></label>
      </div>

      <div style={s.files}>
        {readyDocs.length === 0 && <span style={s.mut}>No ready files yet — upload one above, or add data on the Documents page.</span>}
        {readyDocs.map((d) => {
          const on = selDocs.includes(d.id);
          return (
            <button key={d.id} onClick={() => setSelDocs((w) => (on ? w.filter((x) => x !== d.id) : [...w, d.id]))}
              style={{ ...s.file, ...(on ? s.fileOn : {}) }} title={d.filename}>
              <span style={{ fontSize: 15 }}>{on ? '☑' : '☐'}</span>
              <span style={{ minWidth: 0 }}>
                <b style={s.fname}>{d.filename}</b>
                <span style={s.mut}>{kindLabel(d.kind)} · {sizeFmt(d.size)} · {d.status === 'READY' ? 'Ready' : d.status}</span>
                <span style={s.badge}>Authorized</span>
              </span>
            </button>
          );
        })}
      </div>

      {sheetDoc && (
        <div style={s.data}>
          <b>Data analysis — {sheetDoc.filename}</b>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 6 }}>
            <label style={s.mut}>Revenue columns
              <input style={s.in} value={revCols} placeholder="e.g. revenue"
                onChange={(e) => setRevCols(e.target.value)} /></label>
            <label style={s.mut}>Expense columns
              <input style={s.in} value={expCols} placeholder="e.g. expense"
                onChange={(e) => setExpCols(e.target.value)} /></label>
            <button style={s.mini} onClick={analyzeData} disabled={sheetBusy}>
              {sheetBusy ? '…' : 'Analyze'}</button>
          </div>
          {sheetOut && (
            <div style={{ marginTop: 6, fontSize: 12 }}>
              <div>Rows: {sheetOut.structure?.rows ?? '—'} · Columns: {(sheetOut.structure?.columns || []).map((c) => c.name).join(', ')}</div>
              <div>Revenue: {sheetOut.metrics?.revenue ?? '—'} · Expenses: {sheetOut.metrics?.expenses ?? '—'} · Profit: {sheetOut.metrics?.profit ?? '—'}</div>
              <div style={{ color: String(sheetOut.verdict).startsWith('calculated') ? 'var(--ok)' : 'var(--warn)' }}>
                {sheetOut.verdict}</div>
              <div style={s.mut}>Data used: {sheetOut.filename}</div>
            </div>
          )}
        </div>
      )}

      <div style={s.thread}>
        {msgs.length === 0 && <p style={s.mut}>Ask anything in your scope. Answers use only files you are authorized to access.</p>}
        {msgs.map((m, i) => (
          <div key={i} style={m.role === 'user' ? s.u : s.a}>
            <div style={s.who}>{m.role === 'user' ? 'You' : 'Assistant'}{' '}
              {m.role === 'assistant' && (speech.playing === i
                ? <button style={s.mini} onClick={speech.stop}>⏹ Stop</button>
                : <button style={s.mini} onClick={() => speech.play(i, m.content)}>🔊 Play</button>)}
            </div>
            <div style={{ whiteSpace: 'pre-wrap' }}>{m.content}</div>
            {m.role === 'assistant' && m.sources && m.sources.length > 0 && (
              <div style={{ marginTop: 6 }}>
                <div style={{ fontSize: 11, color: 'var(--mut)', marginBottom: 3 }}>
                  Sources:
                </div>
                {m.sources.map((src, k) => (
                  <div key={k} style={s.src}>
                    <b>{src.filename}</b>
                    {src.page ? ` — Page ${src.page}` : ''}
                  </div>
                ))}
              </div>
            )}
            {m.role === 'assistant' && m.sources && m.sources.length === 0 && m.tools?.includes('rag') && (
              <div style={{ ...s.mut, marginTop: 4 }}>No matching chunks in your scope — nothing was invented.</div>
            )}
          </div>
        ))}
        {busy && msgs[msgs.length - 1]?.role === 'user' && <div style={s.a}><div style={s.who}>Assistant</div>…</div>}
        <div ref={bottom} />
      </div>
      {err && <p style={s.err}>{err}{' '}
        {lastUser && <button style={s.mini} onClick={() => send(lastUser)} disabled={busy}>{L('retry')}</button>}</p>}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
        {SUGGESTIONS.map(([label, q]) => (
          <button key={label} style={s.mini} disabled={busy} onClick={() => quickAsk(q)}>{label}</button>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
        <span ref={micWrapRef}>
          <VoiceBar tok={tok} lang={lang} disabled={busy} onTranscript={onTranscript} onError={setErr} />
        </span>
        <input ref={inputRef} style={{ ...s.in, flex: 1 }} value={input} placeholder="Type a message… (Enter to send)"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <button style={s.btn} onClick={() => send()} disabled={busy || !input.trim()}>
          {busy ? '…' : L('send')}</button>
      </div>
    </div>
  );
}

const s = {
  card: { background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 16, marginBottom: 12 },
  thread: { display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 420, overflowY: 'auto' },
  u: { background: 'var(--accent-soft)', border: '1px solid var(--accent)', borderRadius: 10, padding: '8px 10px', fontSize: 13, alignSelf: 'flex-end', maxWidth: '90%', color: 'var(--text-strong)' },
  a: { background: 'var(--bg-deep)', border: '1px solid var(--border-soft)', borderRadius: 10, padding: '8px 10px', fontSize: 13, alignSelf: 'flex-start', maxWidth: '90%' },
  who: { fontSize: 11, color: 'var(--mut)', marginBottom: 4 },
  in: { background: 'var(--input-bg)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 8, padding: 8 },
  sel: { background: 'var(--input-bg)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 6, padding: 4 },
  btn: { background: 'var(--accent)', color: 'var(--btn-text)', border: 0, borderRadius: 8, padding: '8px 14px', cursor: 'pointer', fontWeight: 600 },
  mini: { background: 'var(--ghost-bg)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 6, padding: '3px 8px', cursor: 'pointer', fontSize: 12 },
  pill: { background: 'var(--chip-bg)', border: '1px solid var(--border-strong)', borderRadius: 12, padding: '2px 10px', fontSize: 12, color: 'var(--text)' },
  src: { border: '1px solid var(--border-soft)', borderLeft: '3px solid var(--accent)', borderRadius: 7, padding: '5px 8px', margin: '4px 0', background: 'var(--bg-deep)', fontSize: 12 },
  files: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8, margin: '8px 0' },
  file: { display: 'flex', gap: 8, alignItems: 'flex-start', textAlign: 'left', background: 'var(--card-2)', color: 'var(--text)', border: '1px solid var(--border-soft)', borderRadius: 10, padding: '9px 11px', cursor: 'pointer', fontSize: 12 },
  fileOn: { borderColor: 'var(--accent)', boxShadow: '0 0 0 1px var(--accent)' },
  fname: { display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '100%' },
  badge: { display: 'inline-block', background: 'var(--accent-soft)', border: '1px solid var(--accent)', borderRadius: 999, padding: '0 8px', fontSize: 11, marginLeft: 6 },
  data: { background: 'var(--bg-deep)', border: '1px solid var(--border-soft)', borderRadius: 10, padding: '10px 12px', margin: '8px 0', fontSize: 13 },
  mut: { color: 'var(--mut)', fontSize: 13 },
  err: { color: 'var(--bad)', fontSize: 13 },
};
