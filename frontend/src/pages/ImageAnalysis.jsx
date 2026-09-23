import React, { useEffect, useRef, useState } from 'react';
import { apiFetch, hasPerm } from '../rbac.js';
import { friendlyError } from '../status.js';
import { T } from '../i18n.js';
import { Card, Spinner, ErrorNote, DocStatusBadge, Empty, Skeleton } from '../ui.jsx';

/* Image analysis: list kind=image documents, show a preview, run the
   backend's vision analysis (POST /api/v1/docs/{id}/analyze). Absent-engine
   states are surfaced with friendly wording, never engine names. */

function ImageCard({ tok, doc, user, lang, onAnalyzed, onAsk }) {
  const [img, setImg] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [priv, setPriv] = useState(null); // explicit "Analyze with Private AI" result
  const [privBusy, setPrivBusy] = useState(false);
  const canAnalyze = hasPerm(user, 'DOCUMENT_ANALYZE');

  useEffect(() => {
    let url = '';
    let dead = false;
    fetch(`/api/v1/docs/${doc.id}/preview`, { headers: { Authorization: 'Bearer ' + tok } })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('HTTP ' + r.status))))
      .then((b) => { if (!dead) { url = URL.createObjectURL(b); setImg(url); } })
      .catch((e) => { if (!dead) setErr('preview: ' + e.message); });
    return () => { dead = true; try { url && URL.revokeObjectURL(url); } catch { /* noop */ } };
  }, [doc.id, tok]);

  const analyze = async () => {
    setBusy(true); setErr('');
    try { onAnalyzed(await apiFetch(`/api/v1/docs/${doc.id}/analyze`, tok, { method: 'POST' })); }
    catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const analyzePrivate = async () => {
    setPrivBusy(true); setErr(''); setPriv(null);
    try {
      setPriv(await apiFetch(`/api/v1/images/${doc.id}/analyze`, tok, { method: 'POST', body: {} }));
    } catch (e) { setErr(e.message); }
    finally { setPrivBusy(false); }
  };

  const v = doc.vision || {};
  const ocr = v.ocr || null;
  const ocrUnavailable = ocr && /unavailable/i.test(String(ocr.status || ''));
  const objects = v.objects || [];

  return (
    <Card title={doc.filename} actions={<DocStatusBadge status={doc.status} />}>
      {img
        ? <img src={img} alt={doc.filename} style={{ maxWidth: '100%', maxHeight: 240, borderRadius: 8, display: 'block' }} />
        : <Empty title="Preview unavailable" hint={err || 'Loading…'} />}
      <div className="mut" style={{ margin: '6px 0' }}>
        {doc.ext} · {(doc.size / 1024).toFixed(1)} KB · processing: {doc.proc_status}
      </div>
      {ocrUnavailable && (
        <div className="note note-warn">
          Image analysis is temporarily unavailable. The preview above still works — only automatic text recognition is paused.
        </div>
      )}
      {ocr && !ocrUnavailable && (
        <p className="mut">text recognition · confidence {ocr.confidence} · {ocr.status}</p>
      )}
      {objects.length > 0 && (
        <p className="mut">objects: {objects.map((o) => (typeof o === 'string' ? o : o.label)).join(', ')}</p>
      )}
      {doc.text_len > 0 && <p className="mut">text extracted: {doc.text_len} chars</p>}
      <ErrorNote error={err} />
      {priv && (
        <div className="note">
          <b>Private AI result</b> ({priv.dims?.width}×{priv.dims?.height}
          {priv.ocr_status && priv.ocr_status !== 'done' ? ' · text recognition unavailable' : ''})
          {priv.summary ? <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{priv.summary}</div>
            : <div className="mut" style={{ marginTop: 4 }}>No text recognized in this image.</div>}
        </div>
      )}
      <div className="row" style={{ marginTop: 6 }}>
        {canAnalyze && (
          <button className="btn btn-mini" disabled={busy || doc.status !== 'READY' && doc.status !== 'UPLOADED'}
            onClick={analyze}>
            {busy ? <Spinner label="Analyzing…" /> : 'Analyze image'}
          </button>)}
        {canAnalyze && (
          <button className="btn btn-ghost btn-mini" disabled={privBusy} onClick={analyzePrivate}>
            {privBusy ? <Spinner label="Private AI…" /> : 'Analyze with Private AI'}
          </button>)}
        {doc.status === 'READY' && <button className="btn btn-ghost btn-mini" onClick={() => onAsk(doc)}>Ask AI</button>}
        {!canAnalyze && <span className="mut">{T(lang, 'deniedNote')} (DOCUMENT_ANALYZE)</span>}
      </div>
    </Card>
  );
}

export default function ImageAnalysis({ tok, user, onAsk, lang }) {
  const [docs, setDocs] = useState(null);
  const [err, setErr] = useState('');
  const [upBusy, setUpBusy] = useState(false);
  const upRef = useRef(null);
  const canUpload = hasPerm(user, 'DOCUMENT_UPLOAD');

  const load = () => {
    setErr('');
    apiFetch('/api/v1/docs?kind=image', tok)
      .then((j) => setDocs(j.documents))
      .catch((e) => { setErr(e.message); setDocs([]); });
  };
  useEffect(load, [tok]);

  /* Upload stays on the private pipeline: authenticated multipart POST to
     the backend store (field "f") — no public URL, no external service. */
  const doUpload = async (file) => {
    if (!file || upBusy) return;
    setUpBusy(true); setErr('');
    try {
      const fd = new FormData();
      fd.append('f', file, file.name);
      const r = await fetch('/api/v1/docs/upload', {
        method: 'POST', headers: { Authorization: 'Bearer ' + tok }, body: fd });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw Object.assign(new Error(j.detail || ('HTTP ' + r.status)), { status: r.status });
      load();
    } catch (e) { setErr(friendlyError(e.status, e.message)); }
    finally { setUpBusy(false); }
  };

  const onAnalyzed = (updated) => {
    setDocs((xs) => (xs || []).map((d) => (d.id === updated.id ? { ...d, ...updated } : d)));
  };

  return (
    <div>
      <div className="page-head">
        <h2>{T(lang, 'imageAnalyzeTitle')}</h2>
        <p className="mut">{T(lang, 'imageAnalyzeHint')}</p>
      </div>
      <div className="note">
        <b>Private Image Storage</b> · Local AI Processing · External AI Sharing: Disabled ·
        Image Access: Authorization Required.
        <span className="mut"> Images stay private until you explicitly choose “Analyze with Private AI”.</span>
      </div>
      <div className="card-actions" style={{ marginBottom: 12 }}>
        {canUpload && (
          <button className="btn btn-ghost btn-mini" disabled={upBusy}
            onClick={() => upRef.current && upRef.current.click()}>
            {upBusy ? <Spinner label="Uploading…" /> : 'Upload image'}
          </button>)}
        {canUpload && (
          <input ref={upRef} type="file" accept="image/*" hidden disabled={upBusy}
            onChange={(e) => {
              const f = e.target.files && e.target.files[0];
              e.target.value = '';
              doUpload(f);
            }} />
        )}
        <button className="btn btn-ghost btn-mini" onClick={load}>{T(lang, 'refresh')}</button>
      </div>
      <ErrorNote error={err} onRetry={load} />
      {docs === null && <Skeleton rows={4} />}
      {docs !== null && docs.length === 0 && (
        <Card><Empty title={T(lang, 'noResults')} hint="Use “Upload image” above to add a JPG or PNG." /></Card>
      )}
      <div className="grid-2">
        {(docs || []).map((d) => (
          <ImageCard key={d.id} tok={tok} doc={d} user={user} lang={lang}
            onAnalyzed={onAnalyzed} onAsk={onAsk} />
        ))}
      </div>
    </div>
  );
}
