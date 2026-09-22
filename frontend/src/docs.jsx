import React, { useEffect, useState } from 'react';
import { apiFetch, hasPerm } from './rbac.js';

/* Phase 3 documents console: upload (progress), search/filter, preview,
   analyze, download, delete. Talks ONLY to /api/v1/docs (RBAC-enforced). */

function xhrUpload(tok, file, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('POST', '/api/v1/docs/upload');
    x.setRequestHeader('Authorization', 'Bearer ' + tok);
    x.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100)); };
    x.onload = () => {
      try {
        const j = JSON.parse(x.responseText);
        x.status === 200 ? resolve(j) : reject(Object.assign(new Error(j.detail || 'HTTP ' + x.status), { status: x.status }));
      } catch { reject(new Error('HTTP ' + x.status)); }
    };
    x.onerror = () => reject(new Error('network error'));
    const fd = new FormData();
    fd.append('f', file, file.name);
    x.send(fd);
  });
}

export function DocsView({ tok, user, onAsk, lang }) {
  const [docs, setDocs] = useState([]);
  const [q, setQ] = useState('');
  const [kind, setKind] = useState('');
  const [status, setStatus] = useState('');
  const [err, setErr] = useState('');
  const [up, setUp] = useState(null); // {name, type, size, pct, state, msg}
  const [sel, setSel] = useState(null); // detail doc
  const [previewURL, setPreviewURL] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const [reindexing, setReindexing] = useState('');
  const canUpload = hasPerm(user, 'DOCUMENT_UPLOAD');
  const canAnalyze = hasPerm(user, 'DOCUMENT_ANALYZE');
  const canDelete = hasPerm(user, 'DOCUMENT_DELETE');

  const load = async () => {
    setErr('');
    try {
      const p = new URLSearchParams();
      if (q) p.set('q', q);
      if (kind) p.set('kind', kind);
      if (status) p.set('status', status);
      setDocs((await apiFetch(`/api/v1/docs?${p}`, tok)).documents);
    } catch (e) { setErr(e.status === 403 ? 'Denied: ' + e.message : e.message); }
  };
  useEffect(() => { load(); }, []);

  const onFile = async (file) => {
    if (!file) return;
    setUp({ name: file.name, type: file.type || '—', size: file.size, pct: 0, state: 'uploading', msg: '' });
    try {
      const j = await xhrUpload(tok, file, (pct) => setUp((u) => ({ ...u, pct })));
      setUp((u) => ({ ...u, state: 'processing', pct: 100, msg: `stored as ${j.kind} · ${j.size} B · extracting…` }));
      if (canAnalyze) {
        try {
          const a = await apiFetch(`/api/v1/docs/${j.id}/analyze`, tok, { method: 'POST' });
          setUp((u) => ({ ...u, state: 'done', msg: `ready · ${a.proc_status} · ${a.text_len} chars` }));
        } catch (e) { setUp((u) => ({ ...u, state: 'error', msg: 'analyze: ' + e.message })); }
      } else {
        setUp((u) => ({ ...u, state: 'done', msg: 'uploaded (analysis needs DOCUMENT_ANALYZE)' }));
      }
      await load();
    } catch (e) {
      setUp((u) => ({ ...u, state: 'error', msg: e.message }));
    }
  };

  const openDoc = async (id) => {
    setErr(''); setPreviewURL('');
    try {
      const d = await apiFetch(`/api/v1/docs/${id}`, tok);
      setSel(d);
      const doc = docs.find((x) => x.id === id);
      if (doc?.kind === 'image') {
        const r = await fetch(`/api/v1/docs/${id}/preview`, { headers: { Authorization: 'Bearer ' + tok } });
        if (r.ok) setPreviewURL(URL.createObjectURL(await r.blob()));
      }
    } catch (e) { setErr(e.message); }
  };

  const analyze = async (id) => {
    setErr('');
    try {
      const a = await apiFetch(`/api/v1/docs/${id}/analyze`, tok, { method: 'POST' });
      setSel(a); await load();
    } catch (e) { setErr(e.message); }
  };

  const download = async (doc) => {
    const r = await fetch(`/api/v1/docs/${doc.id}/download`, { headers: { Authorization: 'Bearer ' + tok } });
    if (!r.ok) { setErr('download failed: HTTP ' + r.status); return; }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await r.blob());
    a.download = doc.filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  };

  const reindex = async (doc) => {
    setErr(''); setReindexing(doc.id);
    try {
      const j = await apiFetch(`/api/v1/docs/${doc.id}/reindex`, tok, { method: 'POST' });
      setSel(j); await load();
    } catch (e) { setErr(e.message); }
    finally { setReindexing(''); }
  };

  const remove = async (doc) => {
    if (!confirm(`Delete ${doc.filename}?`)) return;
    setErr('');
    try {
      await apiFetch(`/api/v1/docs/${doc.id}`, tok, { method: 'DELETE' });
      if (sel?.id === doc.id) setSel(null);
      await load();
    } catch (e) { setErr(e.message); }
  };

  const stBadge = (st) => ({
    'UPLOADED': 'badge badge-blue',
    'PROCESSING': 'badge badge-amber',
    'READY': 'badge badge-green',
    'FAILED': 'badge badge-red',
  }[st] || 'badge badge-slate');

  return (
    <div style={s.card}>
      <h3 style={{ marginTop: 0 }}>Documents</h3>
      {err && <p style={s.err}>{err}</p>}
      {canUpload && (
        <div style={{ ...s.drop, borderColor: dragOver ? 'var(--accent)' : undefined, background: dragOver ? 'var(--card-2)' : undefined }}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); onFile(e.dataTransfer.files?.[0]); }}>
          <label style={s.lbl}>Upload (PDF, DOCX, TXT, CSV, XLSX, JPG/PNG ≤ limit) — drag &amp; drop or click
            <input type="file" style={{ display: 'block', marginTop: 4 }}
              accept=".pdf,.docx,.txt,.csv,.xlsx,.jpg,.jpeg,.png"
              onChange={(e) => { onFile(e.target.files[0]); e.target.value = ''; }} /></label>
          {up && (
            <div style={{ fontSize: 13, marginTop: 6 }}>
              <div><b>{up.name}</b> <span style={s.mut}>{up.type} · {(up.size / 1024).toFixed(1)} KB</span></div>
              <div style={s.bar}><div style={{ ...s.fill, width: up.pct + '%' }} /></div>
              <div style={up.state === 'error' ? s.err : s.mut}>
                {up.state}: {up.pct}% {up.msg}</div>
            </div>)}
        </div>)}
      <div style={{ display: 'flex', gap: 8, margin: '10px 0', flexWrap: 'wrap' }}>
        <input style={s.in} placeholder="Search filename…" value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && load()} />
        <select style={s.in} value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">all types</option>
          <option value="doc">documents</option><option value="image">images</option>
          <option value="pdf">pdf</option><option value="docx">docx</option>
          <option value="txt">txt</option><option value="csv">csv</option><option value="xlsx">xlsx</option>
        </select>
        <select style={s.in} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status filter">
          <option value="">all statuses</option>
          <option value="UPLOADED">Uploaded</option>
          <option value="PROCESSING">Processing</option>
          <option value="READY">Ready</option>
          <option value="FAILED">Failed</option>
        </select>
        <button style={s.btn} onClick={load}>Apply</button>
      </div>
      <table style={s.tbl}>
        <thead><tr><th>Filename</th><th>Type</th><th>Size</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>{docs.map((d) => (
          <tr key={d.id}>
            <td>{d.filename}</td><td>{d.ext}</td><td>{(d.size / 1024).toFixed(1)} KB</td>
            <td>
              <span className={stBadge(d.status)}>{d.status}</span>
              <div style={{ fontSize: 11, color: 'var(--mut)', marginTop: 2 }}>{d.proc_status}</div>
            </td>
            <td style={{ whiteSpace: 'nowrap' }}>
              <button style={s.mini} onClick={() => openDoc(d.id)}>preview</button>{' '}
              {canAnalyze && <button style={s.mini} disabled={reindexing === d.id} onClick={() => analyze(d.id)}>analyze</button>}{' '}
              {canAnalyze && <button style={s.mini} disabled={reindexing === d.id} onClick={() => reindex(d)}>Refresh index</button>}{' '}
              {d.status === 'READY' && <button style={s.mini} onClick={() => onAsk && onAsk(d)}>Ask AI</button>}{' '}
              <button style={s.mini} onClick={() => download(d)}>download</button>{' '}
              {canDelete && <button style={s.mini} onClick={() => remove(d)}>delete</button>}
            </td></tr>))}
        </tbody>
      </table>
      {docs.length === 0 && <p style={s.mut}>No documents match this filter.</p>}
      {sel && (
        <div style={{ ...s.card, marginTop: 10 }}>
          <h4 style={{ margin: '0 0 6px' }}>{sel.filename} <span style={s.mut}>({sel.proc_status}, {sel.text_len} chars)</span></h4>
          {previewURL && <img src={previewURL} alt="preview" style={{ maxWidth: '100%', maxHeight: 320, borderRadius: 8 }} />}
          {sel.vision?.ocr && <p style={s.mut}>text recognition · confidence {sel.vision.ocr.confidence} · {sel.vision.ocr.status}</p>}
          {sel.excerpt && <pre style={s.pre}>{sel.excerpt}</pre>}
        </div>)}
    </div>
  );
}

const s = {
  card: { background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 16, marginBottom: 12, color: 'var(--text)' },
  drop: { border: '1px dashed var(--border-strong)', borderRadius: 10, padding: 12 },
  lbl: { fontSize: 13 },
  in: { background: 'var(--input-bg)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 8, padding: 8 },
  btn: { background: 'var(--accent)', color: 'var(--btn-text)', border: 0, borderRadius: 8, padding: '8px 14px', cursor: 'pointer', fontWeight: 600 },
  mini: { background: 'var(--ghost-bg)', color: 'var(--text)', border: '1px solid var(--border-strong)', borderRadius: 6, padding: '3px 8px', cursor: 'pointer', fontSize: 12 },
  tbl: { width: '100%', borderCollapse: 'collapse', fontSize: 13, color: 'var(--text)' },
  bar: { height: 8, background: 'var(--bg-deep)', borderRadius: 4, marginTop: 4 },
  fill: { height: 8, background: 'var(--accent)', borderRadius: 4 },
  pre: { whiteSpace: 'pre-wrap', background: 'var(--bg-deep)', border: '1px solid var(--border-soft)', borderRadius: 8, padding: 10, fontSize: 12, maxHeight: 220, overflowY: 'auto', color: 'var(--text)' },
  mut: { color: 'var(--mut)', fontSize: 13 },
  err: { color: 'var(--bad)', fontSize: 13 },
};
