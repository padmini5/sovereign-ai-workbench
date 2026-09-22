"""Phase 2 AI provider interface.

Frontend -> FastAPI -> AIService -> ModelProvider -> Response.

Providers implement this ABC; the service never imports a concrete
provider module. New providers (cloud, if explicitly configured) just
subclass ModelProvider and register in service.PROVIDERS.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator


class AIError(Exception):
    """Base AI failure (mapped to HTTP 502 by the API layer)."""


class ProviderUnavailable(AIError):
    """Provider cannot be reached (e.g. Ollama daemon down)."""


class ModelProvider(ABC):
    #: stable id used in status responses, audit metadata, stored messages
    name: str = "base"

    @abstractmethod
    def generate(self, messages: list[dict], system: str = "", **opts) -> str:
        """Non-streaming completion. messages: [{role, content}]."""

    def generate_stream(self, messages: list[dict], system: str = "", **opts) -> Iterator[str]:
        """Streaming completion yielding text deltas. Default: one chunk."""
        yield self.generate(messages, system=system, **opts)

    def health(self) -> dict:
        """Cheap liveness probe. Must never raise; never assumes models exist."""
        return {"provider": self.name, "reachable": True, "model": "", "detail": "ok"}
