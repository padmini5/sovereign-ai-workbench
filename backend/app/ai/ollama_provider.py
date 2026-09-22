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
import urllib.request
from typing import Iterator

from .base import ModelProvider, ProviderUnavailable

BASE_URL = (os.getenv("OLLAMA_BASE_URL") or os.getenv("SOV_OLLAMA_URL")
            or "http://localhost:11434").rstrip("/")
MODEL = (os.getenv("OLLAMA_MODEL") or os.getenv("SOV_OLLAMA_MODEL")
         or os.getenv("SOV_MODEL_DEFAULT") or "")


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

    def _chat_payload(self, messages: list[dict], system: str, stream: bool, **opts) -> dict:
        msgs = ([{"role": "system", "content": system}] if system else []) + [
            {"role": m["role"], "content": m["content"]} for m in messages
            if m.get("role") in ("user", "assistant")]
        p: dict = {"model": self.model, "messages": msgs, "stream": stream}
        if opts.get("max_tokens"):
            p["options"] = {"num_predict": int(opts["max_tokens"])}
        return p

    def generate(self, messages: list[dict], system: str = "", **opts) -> str:
        if not self.model:
            raise ProviderUnavailable("no model configured (set OLLAMA_MODEL)")
        try:
            out = _post("/api/chat", self._chat_payload(messages, system, False, **opts),
                        timeout=float(opts.get("timeout", 90)))
        except ProviderUnavailable:
            raise
        except Exception as e:
            # Older daemons may lack /api/chat — fall back to /api/generate.
            prompt = (system + "\n\n" if system else "") + "\n".join(
                f"{m['role']}: {m['content']}" for m in messages)
            try:
                out = _post("/api/generate",
                            {"model": self.model, "prompt": prompt, "stream": False},
                            timeout=float(opts.get("timeout", 90)))
                if out.get("response"):
                    return out["response"]
            except ProviderUnavailable:
                raise
            raise ProviderUnavailable(f"ollama error: {e}") from e
        msg = (out.get("message") or {}).get("content") or out.get("response")
        if not msg:
            if "error" in out and "model" in str(out["error"]).lower():
                raise ProviderUnavailable(f"model '{self.model}' not installed: {out['error']}")
            raise ProviderUnavailable(f"empty ollama response: {str(out)[:200]}")
        return msg

    def generate_stream(self, messages: list[dict], system: str = "", **opts) -> Iterator[str]:
        if not self.model:
            raise ProviderUnavailable("no model configured (set OLLAMA_MODEL)")
        req = urllib.request.Request(
            self.base_url + "/api/chat",
            data=json.dumps(self._chat_payload(messages, system, True, **opts)).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=float(opts.get("timeout", 120)))
        except Exception as e:
            raise ProviderUnavailable(f"ollama unreachable at {self.base_url}: {e}") from e
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
        present = any(self.model in m for m in models) if self.model else False
        return {"provider": self.name, "reachable": True, "model": self.model,
                "model_present": present, "installed": models[:20],
                "detail": "ok" if (present or not self.model) else "model not installed"}
