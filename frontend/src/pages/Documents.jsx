import React from 'react';
import { DocsView } from '../docs.jsx';
import { T } from '../i18n.js';

/* Documents page: the shared docs console (drag-drop upload with progress,
   status Uploaded/Processing/Ready/Failed, search/filter, preview, analyze,
   reindex, download, delete) — all server-enforced via /api/v1/docs. */
export default function Documents({ tok, user, onAsk, lang }) {
  return (
    <div>
      <div className="page-head">
        <h2>{T(lang, 'docIntelTitle')}</h2>
        <p className="mut">{T(lang, 'docIntelHint')}</p>
      </div>
      <DocsView tok={tok} user={user} onAsk={onAsk} lang={lang} />
    </div>
  );
}
