import React from 'react';
import { ChatView } from '../ai-chat.jsx';

/* AI Assistant: the private file/chat workspace (upload + ask + sources).
   All retrieval is backend permission-filtered — this page never retrieves
   on its own. Wraps the shared ChatView so phase1 keeps the same component. */
export default function Assistant({ tok, user, askDoc, askCtx, lang }) {
  return (
    <div>
      <ChatView tok={tok} user={user} askDoc={askDoc} askCtx={askCtx} uiLang={lang} />
    </div>
  );
}
