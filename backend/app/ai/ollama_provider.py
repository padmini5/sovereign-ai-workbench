"""Ollama / local-model provider (stdlib HTTP only — no `ollama` package needed).

Config (never hard-coded):
  OLLAMA_BASE_URL (or SOV_OLLAMA_URL), default http://localhost:11434
  OLLAMA_MODEL    (or SOV_OLLAMA_MODEL / SOV_MODEL_DEFAULT), default ""

No model is assumed installed: health() reports reachable + whether the
configured model is present; generate() raises ProviderUnavailable with a
clear message when the daemon is down or the model is missing.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Iterator

from .base import ModelProvider, ProviderUnavailable

BASE_URL = (os.getenv("OLLAMA_BASE_URL") or os.getenv("SOV_OLLAMA_URL")
            or "http://localhost:11434").rstrip("/")
MODEL = (os.getenv("OLLAMA_MODEL") or os.getenv("SOV_OLLAMA_MODEL")
         or os.getenv("SOV_MODEL_DEFAULT") or "")


def _pick_model(models: list) -> str:
    """Choose an installed model when none is configured: prefer chat models
    (skip embedding-only tags), else the first installed one."""
    names = [m for m in models if m]
    for n in names:
        if "embed" not in n.lower():
            return n
    return names[0] if names else ""


def _post(path: str, payload: dict, timeout: float):
    req = urllib.request.Request(
        BASE_URL + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:  # type: ignore[attr-defined]
        raise ProviderUnavailable(f"ollama unreachable at {BASE_URL}: {e}") from e


class OllamaProvider(ModelProvider):
    name = "ollama"

    def __init__(self, base_url: str = "", model: str = ""):
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self.model = model or MODEL
        # Model discovery (only when nothing is configured): cache the pick;
        # an empty result is cached briefly so a later model pull is picked up.
        self._discovered: str | None = None
        self._discovered_at = 0.0
        # Set only after a successful completion: which model ACTUALLY answered
        # (never a routing target). Read by AIService to report the real model.
        self.last_invoked_model: str = ""

    def _resolve_model(self) -> str:
        """Configured model always wins (env precedence). Otherwise discover an
        installed model via GET /api/tags; cache empty results for 60s."""
        if self.model:
            return self.model
        now = time.monotonic()
        if self._discovered is not None and now - self._discovered_at < 60:
            return self._discovered
        pick = ""
        try:
            with urllib.request.urlopen(self.base_url + "/api/tags", timeout=3) as resp:
                tags = json.loads(resp.read().decode())
            pick = _pick_model([m.get("name", "") for m in tags.get("models", [])])
        except Exception:
            pick = ""
        self._discovered, self._discovered_at = pick, now
        if pick:
            self.model = pick  # sticky for this instance (env was empty)
        return pick

    def _chat_payload(self, messages: list[dict], system: str, stream: bool,
                      model: str = "", **opts) -> dict:
        msgs = ([{"role": "system", "content": system}] if system else []) + [
            {"role": m["role"], "content": m["content"]} for m in messages
            if m.get("role") in ("user", "assistant")]
        # Per-call model override (server-side task routing) wins over the
        # configured/discovered default — this is how the selected local model
        # is ACTUALLY invoked; the routing decision alone changes nothing.
        p: dict = {"model": model or self.model, "messages": msgs, "stream": stream}
        if opts.get("max_tokens"):
            p["options"] = {"num_predict": int(opts["max_tokens"])}
        return p

    def generate(self, messages: list[dict], system: str = "", **opts) -> str:
        override = str(opts.pop("model", None) or "").strip()
        model = override
        if not model:
            if not self._resolve_model():
                raise ProviderUnavailable("no model configured (set OLLAMA_MODEL)")
            model = self.model
        try:
            out = _post("/api/chat",
                        self._chat_payload(messages, system, False, model=model, **opts),
                        timeout=float(opts.get("timeout", 90)))
        except ProviderUnavailable:
            raise
        except Exception as e:
            # Older daemons may lack /api/chat — fall back to /api/generate.
            prompt = (system + "\n\n" if system else "") + "\n".join(
                f"{m['role']}: {m['content']}" for m in messages)
            try:
                out = _post("/api/generate",
                            {"model": model, "prompt": prompt, "stream": False},
                            timeout=float(opts.get("timeout", 90)))
                if out.get("response"):
                    self.last_invoked_model = model
                    return out["response"]
            except ProviderUnavailable:
                raise
            raise ProviderUnavailable(f"ollama error: {e}") from e
        msg = (out.get("message") or {}).get("content") or out.get("response")
        if not msg:
            if "error" in out and "model" in str(out["error"]).lower():
                raise ProviderUnavailable(f"model '{model}' not installed: {out['error']}")
            raise ProviderUnavailable(f"empty ollama response: {str(out)[:200]}")
        self.last_invoked_model = model
        return msg

    def generate_stream(self, messages: list[dict], system: str = "", **opts) -> Iterator[str]:
        override = str(opts.pop("model", None) or "").strip()
        model = override
        if not model:
            if not self._resolve_model():
                raise ProviderUnavailable("no model configured (set OLLAMA_MODEL)")
            model = self.model
        req = urllib.request.Request(
            self.base_url + "/api/chat",
            data=json.dumps(self._chat_payload(messages, system, True,
                                               model=model, **opts)).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=float(opts.get("timeout", 120)))
        except Exception as e:
            raise ProviderUnavailable(f"ollama unreachable at {self.base_url}: {e}") from e
        self.last_invoked_model = model  # daemon accepted the model for this stream
        with resp:
            for raw in resp:
                try:
                    chunk = json.loads(raw.decode())
                except Exception:
                    continue
                delta = (chunk.get("message") or {}).get("content", "")
                if delta:
                    yield delta
                if chunk.get("done"):
                    break

    def health(self) -> dict:
        try:
            with urllib.request.urlopen(self.base_url + "/api/tags", timeout=3) as resp:
                tags = json.loads(resp.read().decode())
        except Exception as e:
            return {"provider": self.name, "reachable": False, "model": self.model,
                    "detail": f"unreachable: {e}"}
        models = [m.get("name", "") for m in tags.get("models", [])]
        if not self.model:
            # Nothing configured: discover an installed model from this tags
            # response (no extra request) and reflect it in health().
            pick = _pick_model(models)
            self._discovered, self._discovered_at = pick, time.monotonic()
            if pick:
                self.model = pick
        present = any(self.model in m for m in models) if self.model else False
        return {"provider": self.name, "reachable": True, "model": self.model,
                "model_present": present, "installed": models[:20],
                "detail": "ok" if (present or not self.model) else "model not installed"}
