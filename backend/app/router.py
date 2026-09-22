"""Local model adapter.

- Preferred: Ollama daemon on localhost (open-weight models, no network).
- Fallback: deterministic local extractive draft (grounded by construction).
- Ollama is NEVER mandatory: import/connect failures silently use fallback,
  so the app always starts. When Ollama appears later, it is used automatically.

Hardware-tier toggle: small(3b, fast/CPU) / large(7b, quality/GPU).
"""
import os, time
from . import config as cfg

MODEL_TIER = {"tier": os.getenv("SOV_MODEL_TIER", cfg.MODEL_TIER_DEFAULT)}
OLLAMA_URL = cfg.OLLAMA_URL
TIER_MODEL = {"small": "qwen2.5:3b-instruct", "large": "qwen2.5:7b-instruct"}

_last_mode = {"mode": "local-fallback", "reachable": False}

def set_tier(t: str):
    assert t in ("small", "large")
    MODEL_TIER["tier"] = t

def ollama_reachable(timeout: float = 1.0) -> bool:
    try:
        import urllib.request
        urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=timeout).read()
        return True
    except Exception:
        return False

def backend_info() -> dict:
    reachable = ollama_reachable()
    _last_mode.update({"reachable": reachable,
                       "mode": "ollama" if reachable else "local-fallback"})
    return {"tier": MODEL_TIER["tier"], "model": TIER_MODEL[MODEL_TIER["tier"]],
            "ollama_reachable": reachable,
            "mode": _last_mode["mode"]}

def _ollama_generate(prompt: str, max_tokens: int) -> str | None:
    try:
        import ollama  # pip install ollama (optional)
        model = TIER_MODEL[MODEL_TIER["tier"]]
        r = ollama.generate(model=model, prompt=prompt, options={"num_predict": max_tokens})
        resp = r.get("response")
        if resp:
            _last_mode.update({"mode": "ollama", "reachable": True})
            return resp
    except Exception:
        pass
    # HTTP fallback to Ollama daemon (default localhost — still sovereign;
    # override with SOV_OLLAMA_URL if Ollama runs on another host)
    try:
        import urllib.request, json
        model = TIER_MODEL[MODEL_TIER["tier"]]
        req = urllib.request.Request(OLLAMA_URL + "/api/generate",
            data=json.dumps({"model": model, "prompt": prompt, "stream": False,
                             "options": {"num_predict": max_tokens}}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = json.loads(resp.read().decode()).get("response")
            if out:
                _last_mode.update({"mode": "ollama", "reachable": True})
            return out
    except Exception:
        return None

def draft_text(query: str, chunks: list, role: str) -> tuple[str, float]:
    t0 = time.time()
    ctx = "\n".join(f"[{c['chunk_id']} p.{c['page']}] {c['text']}" for c in chunks)
    max_tok = 256 if MODEL_TIER["tier"] == "small" else 512
    prompt = (f"Role: {role}. Draft a preliminary technical note answering: {query}\n"
              f"Ground every claim in the sources below with [chunk_id] citations.\nSOURCES:\n{ctx}\nDRAFT:")
    llm = _ollama_generate(prompt, max_tok)
    elapsed = time.time() - t0
    if llm:
        return llm, elapsed
    _last_mode.update({"mode": "local-fallback", "reachable": ollama_reachable()})
    # Deterministic offline fallback (extractive, grounded by construction)
    if not chunks:
        return ("No sources visible at your clearance tier. Request elevated access or refine the query.", elapsed)
    lines = [f"Preliminary note (local {MODEL_TIER['tier']} fallback, grounded extract):"]
    for c in chunks:
        snippet = c["text"][:220].strip()
        lines.append(f"- {snippet} [{c['chunk_id']} p.{c['page']}]")
    lines.append("Status: DRAFT — pending review and PQC signature.")
    return ("\n".join(lines), elapsed)
