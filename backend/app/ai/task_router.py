"""Server-side task router: deterministic task category -> LOCAL model.

Step 2 of the SIH roadmap: real multi-model automatic task routing.

Ordering guarantee (enforced by every caller — the router never does auth
itself, and the model is NOT a security boundary):

    USER -> AUTHENTICATION -> RBAC -> RESOURCE AUTHORIZATION -> task router
        -> authorized local model -> response

The router only classifies an already-authorized request and picks the
configured local model. Documents reach a model solely through the existing
permission-aware RAG path (verified BEFORE retrieval, retrieval BEFORE any
model call), so the coding model can never receive unauthorized documents.

Classification is deterministic server-side rules — an LLM is never asked
to decide which LLM to use. Precedence:
  1. explicit task metadata (validated task_type on the request, or the
     server-side agent registry's task_type) — when already available,
  2. deterministic keyword rules over the free-form request text
     (conservative: ties resolve to DOCUMENT = the existing general model),
  3. document context present (explicit document_ids / my_docs mode),
  4. GENERAL.

Model configuration is read from the environment per call (same convention
as get_service()/_ollama_from_env — runtime overrides via sysconfig work
with no restart), never hardcoded at call sites:

  GENERAL / DOCUMENT -> SOV_MODEL_GENERAL | OLLAMA_MODEL | SOV_OLLAMA_MODEL |
                        SOV_MODEL_DEFAULT | safe default "llama3.2:3b"
  CODING             -> SOV_MODEL_CODING | safe default "qwen2.5-coder:1.5b"
  VISION             -> SOV_MODEL_VISION | "" (unset = not configured; the UI
                        reports that honestly instead of faking results)

Only the existing local Ollama endpoint is ever contacted: this module
produces a model NAME for the existing provider — it opens no sockets and
adds no cloud/external API, preserving zero-egress.
"""
from __future__ import annotations

import os
import re

CATEGORIES = ("GENERAL", "DOCUMENT", "CODING", "VISION")

# Chat requests may only name a TEXT task category. VISION is a configured
# server-side capability (reported honestly via /ai/status), never a chat
# task type — clients still cannot choose models.
CHAT_CATEGORIES = ("GENERAL", "DOCUMENT", "CODING")

# Deterministic keyword rules: counted as distinct pattern hits (not raw
# occurrences), word-boundary anchored, linear-time — no ReDoS surface.
_CODING_PATTERNS = (
    r"\bpython\b", r"\bjavascript\b", r"\btypescript\b", r"\bnode\.?js\b",
    r"\bfunction\b", r"\bscript\b", r"\bcode\b", r"\bcoding\b",
    r"\bcompiler\b", r"\bcompile\b", r"\bsyntax\b", r"\btraceback\b",
    r"\bstack trace\b", r"\bdebug\b", r"\bbug\b", r"\brefactor\b",
    r"\bunit test\b", r"\bpytest\b", r"\bregex\b", r"\bcsv\b", r"\bjson\b",
    r"\bsql\b", r"\bexception\b", r"\bruntimeerror\b", r"\btypeerror\b",
    r"\bpandas\b", r"\bgit\b",
)
_DOCUMENT_PATTERNS = (
    r"\breport\b", r"\breports\b", r"\bdocument\b", r"\bdocuments\b",
    r"\bprocedure\b", r"\bprocedures\b", r"\bsop\b", r"\binspection\b",
    r"\bpolic(?:y|ies)\b", r"\bmanual\b", r"\bhandbook\b", r"\bguideline\b",
    r"\bchecklist\b", r"\battachment\b", r"\buploaded?\b", r"\bpdf\b",
    r"\bsummar(?:y|ize|ise)\b", r"\bapproval note\b", r"\bspecification\b",
    r"\bwork instruction\b",
)


def normalize_category(value) -> str:
    """Uppercase + validate a task category. Raises ValueError (fail-loud:
    callers only pass server-validated inputs; nothing silently coerced)."""
    c = str(value or "").strip().upper()
    if c not in CATEGORIES:
        raise ValueError(f"unknown task category '{value}'")
    return c


def classify(text: str = "", explicit: str | None = None,
             has_docs: bool = False) -> str:
    """Deterministically pick the task category for an authorized request.

    explicit: validated task metadata already on the request/context — wins.
    has_docs: permission-verified document context is attached (the caller
    guarantees verification happened before this call).
    """
    if explicit:
        return normalize_category(explicit)
    t = str(text or "").lower()
    coding = sum(1 for p in _CODING_PATTERNS if re.search(p, t))
    doc = sum(1 for p in _DOCUMENT_PATTERNS if re.search(p, t))
    if coding > doc:
        return "CODING"
    if doc > 0:  # doc-only, doc-majority, and coding/doc ties -> existing model
        return "DOCUMENT"
    if has_docs:
        return "DOCUMENT"
    return "GENERAL"


def model_for(category: str) -> str:
    """Configured LOCAL model for a task category (env per call, safe defaults)."""
    c = normalize_category(category)
    if c == "VISION":
        # Optional by design: no default, no download. Empty string is the
        # honest "not configured" state surfaced by /api/v1/ai/status.
        return os.getenv("SOV_MODEL_VISION", "").strip()
    if c == "CODING":
        return (os.getenv("SOV_MODEL_CODING", "").strip() or "qwen2.5-coder:1.5b")
    return (os.getenv("SOV_MODEL_GENERAL", "").strip()
            or os.getenv("OLLAMA_MODEL", "").strip()
            or os.getenv("SOV_OLLAMA_MODEL", "").strip()
            or os.getenv("SOV_MODEL_DEFAULT", "").strip()
            or "llama3.2:3b")
