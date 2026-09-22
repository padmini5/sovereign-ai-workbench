"""AIService: role-aware orchestration over a ModelProvider.

Per chat turn the service (server-side):
  1. takes the authenticated user (id/username/role),
  2. selects the role's system prompt (prompts.py),
  3. executes explicitly requested tools via tools.run_tool() — each call
     re-verifies the user's permissions (fail-closed: any denied tool aborts
     the turn with 403; the LLM is never consulted on access),
  4. calls the provider with system + tool context,
  5. returns text + metadata (provider, model, tools_used, prompt id).

RAG attaches via extra_context (retrieved BEFORE this call by ai_api using
permission-filtered search). Vision / voice attach in later phases.
"""
from __future__ import annotations

import os
import time

from .base import AIError, ModelProvider
from .mock_provider import MockProvider
from .ollama_provider import OllamaProvider
from .prompts import system_prompt_for
from .tools import run_tool


class AIService:
    def __init__(self, provider: ModelProvider):
        self.provider = provider

    def chat(self, user: dict, messages: list[dict],
             tools: list[str] | None = None, lang: str = "en",
             extra_context: str = "", **opts) -> dict:
        t0 = time.time()
        prompt_id, system = system_prompt_for(user["role"])
        if lang and lang != "en":
            system += f" Reply in language '{lang}'. Technical IDs stay in English."
        if extra_context:
            system += (" Answer ONLY from the document context below. If the answer "
                       "is not in the context, say so plainly instead of inventing it.")
        tool_context, tools_used = "", []
        for name in (tools or []):
            out = run_tool(name, user, {})  # permission-checked, fail-closed
            tools_used.append(name)
            tool_context += f"\n[tool:{name}] {str(out)[:1500]}"
        if extra_context:
            tool_context += f"\n[documents]\n{extra_context[:6000]}"
        text = self.provider.generate(
            messages, system=system, role=user["role"],
            tools_used=tools_used, tool_context=tool_context.strip(), **opts)
        return {"text": text, "provider": self.provider.name,
                "model": getattr(self.provider, "model", self.provider.name),
                "prompt_id": prompt_id, "tools_used": tools_used,
                "elapsed_s": round(time.time() - t0, 3)}

    def chat_stream(self, user: dict, messages: list[dict],
                    tools: list[str] | None = None, lang: str = "en",
                    extra_context: str = "", **opts):
        prompt_id, system = system_prompt_for(user["role"])
        if lang and lang != "en":
            system += f" Reply in language '{lang}'. Technical IDs stay in English."
        if extra_context:
            system += (" Answer ONLY from the document context below. If the answer "
                       "is not in the context, say so plainly instead of inventing it.")
        tool_context, tools_used = "", []
        for name in (tools or []):
            out = run_tool(name, user, {})
            tools_used.append(name)
            tool_context += f"\n[tool:{name}] {str(out)[:1500]}"
        if extra_context:
            tool_context += f"\n[documents]\n{extra_context[:6000]}"
        yield from self.provider.generate_stream(
            messages, system=system, role=user["role"],
            tools_used=tools_used, tool_context=tool_context.strip(), **opts)


def _ollama_from_env() -> OllamaProvider:
    """Read OLLAMA_* per call (not import time) so tests/ops can switch live."""
    base = (os.getenv("OLLAMA_BASE_URL") or os.getenv("SOV_OLLAMA_URL")
            or "http://localhost:11434").rstrip("/")
    model = (os.getenv("OLLAMA_MODEL") or os.getenv("SOV_OLLAMA_MODEL")
             or os.getenv("SOV_MODEL_DEFAULT") or "")
    return OllamaProvider(base_url=base, model=model)


def get_service() -> AIService:
    """Provider factory — read env per call so tests can switch at runtime.

    SOV_AI_PROVIDER: mock | mock-fail | ollama | auto (default).
    auto = ollama when its daemon answers, else mock (labelled honestly).
    """
    which = (os.getenv("SOV_AI_PROVIDER") or "auto").lower()
    if which == "mock":
        return AIService(MockProvider())
    if which == "mock-fail":
        return AIService(MockProvider(fail=True))
    if which == "ollama":
        return AIService(_ollama_from_env())
    # auto
    try:
        probe = _ollama_from_env()
        if probe.health().get("reachable"):
            return AIService(probe)
    except Exception:
        pass
    return AIService(MockProvider())
