"""Mock provider for tests/dev — no model, no network.

Deterministic: echoes the last user message prefixed with the role's
assistant id, plus a note of any tool context injected. Failure injection
for tests: MockProvider(fail=True) raises AIError on generate.
"""
from __future__ import annotations

from typing import Iterator

from .base import AIError, ModelProvider


class MockProvider(ModelProvider):
    name = "mock"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[dict] = []  # observable in tests

    def generate(self, messages: list[dict], system: str = "", **opts) -> str:
        if self.fail:
            raise AIError("mock provider forced failure")
        role = (opts.get("role") or "USER").upper()
        tools = opts.get("tools_used") or []
        tool_ctx = opts.get("tool_context") or ""
        self.calls.append({"n_messages": len(messages), "role": role, "tools": list(tools)})
        last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        out = f"[{role} assistant via mock] {last[:500]}"
        if tools:
            out += f" (context: {', '.join(tools)})"
            if tool_ctx:
                out += "\n" + tool_ctx[:2000]
        return out

    def generate_stream(self, messages: list[dict], system: str = "", **opts) -> Iterator[str]:
        text = self.generate(messages, system=system, **opts)
        for w in text.split(" "):  # word-wise deltas exercise the SSE path
            yield w + " "

    def health(self) -> dict:
        if self.fail:
            return {"provider": self.name, "reachable": False, "model": "mock",
                    "detail": "forced failure"}
        return {"provider": self.name, "reachable": True, "model": "mock", "detail": "ok"}
