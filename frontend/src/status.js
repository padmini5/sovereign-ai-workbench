/* Product-facing status + error presentation.
   The backend keeps reporting honest technical state (provider ids, modes,
   algorithms) for logs and admin diagnostics. This module is the ONLY place
   that translates that state into human-friendly labels for normal users —
   model names, engine ids, and env-var names never reach the UI through it.

   Truthfulness rule: fallback/degraded states get neutral "active/ready"
   wording, never a false claim about a specific advanced capability. */

const TECH_TOKENS = [
  [/SOV_[A-Z_]+/g, 'service configuration'],
  [/\bOLLAMA_[A-Z]+\b/g, 'service configuration'],
  [/ollama/gi, 'AI service'],
  [/llama[\w.:-]*/gi, 'AI model'],
  [/qwen[\w.:-]*/gi, 'AI model'],
  [/moondream/gi, 'vision service'],
  [/bge-m\d*/gi, 'indexing service'],
  [/nomic[\w-]*/gi, 'indexing service'],
  [/SOV_[A-Z_]+/g, 'service configuration'],
  [/sqlite/gi, 'document index'],
  [/pgvector/gi, 'document index'],
  [/chromadb?/gi, 'document index'],
  [/\boqs[-\w]*\b/gi, 'signing service'],
  [/\bed25519\b/gi, 'signing service'],
  [/\bml-dsa[-\w]*\b/gi, 'signing service'],
  [/\bml-kem[-\w]*\b/gi, 'signing service'],
  [/\btesseract\b/gi, 'image analysis'],
  [/\bwhisper\b/gi, 'speech recognition'],
  [/\bpiper\b/gi, 'speech synthesis'],
  [/\bvosk\b/gi, 'speech recognition'],
  [/\bespeak(?:-ng)?\b/gi, 'speech synthesis'],
];

/* Scrub backend detail strings before display. Citations/filenames pass
   through untouched — only error/status text goes through here. */
export function scrubDetail(text) {
  let out = String(text ?? '');
  for (const [re, label] of TECH_TOKENS) out = out.replace(re, label);
  return out;
}

const HTTP_FRIENDLY = {
  0: 'Unable to connect to the server. Check your connection and try again.',
  401: 'Your session has expired. Please sign in again.',
  502: 'AI service is temporarily unavailable. Please try again.',
  403: "You don't have permission to perform this action.",
  404: 'The requested item could not be found.',
  422: 'Please check the information you entered.',
  429: 'Too many requests. Please wait a moment and try again.',
  500: 'Something went wrong. Please try again.',
};

export function friendlyError(status, detail) {
  const d = detail ? scrubDetail(detail) : '';
  // A scrubbed backend detail stays useful; otherwise fall back by status.
  if (d && d.length < 220) return d;
  return HTTP_FRIENDLY[status] || 'Something went wrong. Please try again.';
}

/* --- capability statuses (label + tone class) --- */

export function aiStatus(st) {
  if (!st) return { label: 'Local AI • Ready', cls: 'ok', detail: '' };
  if (st.reachable) return { label: 'Local AI • Ready', cls: 'ok', detail: '' };
  return {
    label: 'AI Assistant is temporarily unavailable. Please try again.',
    cls: 'warn', detail: '',
  };
}

export function aiAdminStatus(st) {
  if (st && st.reachable) return { label: 'Local AI • Ready', cls: 'ok' };
  return { label: 'AI service needs configuration.', cls: 'warn' };
}

export function docIntelStatus(st) {
  const ok = !st || st.reachable === undefined || !!st.reachable;
  return ok
    ? { label: 'Private document intelligence • Ready', cls: 'ok' }
    : { label: 'Document intelligence is temporarily unavailable.', cls: 'warn' };
}

export function langStatus(st) {
  const t = st?.i18n;
  const ok = !t || t.translate_available === undefined || !!t.translate_available;
  return ok
    ? { label: 'Multilingual support • Ready', cls: 'ok' }
    : { label: 'Multilingual support is temporarily unavailable.', cls: 'warn' };
}

export function signStatus() {
  // Both real PQC and the honest fallback protect + audit every action.
  return { label: 'Secure signing • Active', cls: 'ok' };
}

export function voiceStatus(st, kind) {
  // kind: 'input' | 'playback'
  const avail = kind === 'input' ? st?.stt?.available : st?.tts?.available;
  if (avail === undefined) return { label: 'Voice Assistant', cls: '', detail: '' };
  if (avail) return { label: kind === 'input' ? 'Voice input • Ready' : 'Voice playback • Ready', cls: 'ok', detail: '' };
  return {
    label: kind === 'input'
      ? 'Voice input is temporarily unavailable.'
      : 'Voice playback is temporarily unavailable.',
    cls: 'warn', detail: '',
  };
}
