"""Embedding provider abstraction (Phase 4).

SOV_EMBED_PROVIDER: mock (default, deterministic, no deps) | hash (local
on-prem, token-hash buckets) | ollama (local model via daemon) | fail
(forced errors for tests). A cloud provider can be added later as a new
subclass — nothing here is coupled to one vendor.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.request
from abc import ABC, abstractmethod


class EmbeddingError(Exception):
    pass


class EmbeddingProvider(ABC):
    name: str = "base"
    dim: int = 0

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """L2-normalized vectors, one per text."""


def _norm(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / n for v in vec]


class MockEmbeddingProvider(EmbeddingProvider):
    """Deterministic tiny vectors for tests (no semantics, stable)."""
    name, dim = "mock", 16

    def __init__(self, fail: bool = False):
        self.fail = fail

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise EmbeddingError("mock embeddings forced failure")
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()
            out.append(_norm([b / 255.0 for b in h[:self.dim]]))
        return out


class HashEmbeddingProvider(EmbeddingProvider):
    """Local on-prem embeddings: hashed token buckets + length feature.

    No downloads, no network. Lexical-overlap semantics — adequate for
    on-prem CPU retrieval; swap SOV_EMBED_PROVIDER=ollama for neural
    embeddings when a GPU box is available.
    """
    name, dim = "hash", 256

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in t.lower().split():
                b = int(hashlib.sha256(tok.encode()).hexdigest(), 16) % (self.dim - 1)
                vec[b] += 1.0
            vec[-1] = len(t) / 1000.0
            out.append(_norm(vec))
        return out


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Local neural embeddings via Ollama daemon (SOV_EMBED_MODEL)."""
    name = "ollama"

    def __init__(self, base_url: str = "", model: str = ""):
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL")
                         or os.getenv("SOV_OLLAMA_URL")
                         or "http://localhost:11434").rstrip("/")
        self.model = model or os.getenv("SOV_EMBED_MODEL") or os.getenv("OLLAMA_MODEL") or ""
        self.dim = int(os.getenv("SOV_EMBED_DIM") or 0) or 768  # corrected on first call

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(self.base_url + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            raise EmbeddingError(f"ollama embeddings unreachable: {e}") from e

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.model:
            raise EmbeddingError("no embedding model configured (set SOV_EMBED_MODEL)")
        try:
            out = self._post("/api/embed", {"model": self.model, "input": texts})
            vecs = out.get("embeddings") or []
        except EmbeddingError:
            raise
        except Exception:
            vecs = []
            for t in texts:  # legacy /api/embeddings, one prompt per call
                r = self._post("/api/embeddings", {"model": self.model, "prompt": t})
                if "embedding" not in r:
                    raise EmbeddingError(f"bad ollama response: {str(r)[:150]}")
                vecs.append(r["embedding"])
        if len(vecs) != len(texts):
            raise EmbeddingError("ollama returned wrong vector count")
        self.dim = len(vecs[0])
        return [_norm([float(v) for v in vec]) for vec in vecs]


def get_embedding_provider() -> EmbeddingProvider:
    which = (os.getenv("SOV_EMBED_PROVIDER") or "mock").lower()
    if which == "hash":
        return HashEmbeddingProvider()
    if which == "ollama":
        return OllamaEmbeddingProvider()
    if which == "fail":
        return MockEmbeddingProvider(fail=True)
    return MockEmbeddingProvider()
