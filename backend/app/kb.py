"""Legacy seed-KB with tier+unit pre-filter and TF-IDF ranking (preserved).

This is the ORIGINAL pre-Phase-4 knowledge base backing /api/query and the
demo task pipeline: seed JSON docs, tier filter BEFORE ranking, TF-IDF
re-rank of the allowed subset, optional ChromaDB pre-filter. Untouched
semantics — only moved from rag.py to kb.py so rag.py can host the Phase 4
document-intelligence pipeline.
"""
from __future__ import annotations

import hashlib
import json
import math
import os

from . import config as cfg

SEED_DIR = cfg.SEED_DIR
KB_STORE = cfg.KB_STORE
CHROMA_DIR = cfg.CHROMA_DIR

EMB_DIM = 64


def _hash_vec(text: str) -> list[float]:
    vec = [0.0] * EMB_DIM
    for tok in text.lower().split():
        vec[int(hashlib.sha256(tok.encode()).hexdigest(), 16) % EMB_DIM] += 1.0
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / n for v in vec]


def _roles_cfg():
    import yaml
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for cand in [os.path.join(here, "roles.yaml"), "roles.yaml"]:
        if os.path.exists(cand):
            with open(cand) as f:
                return yaml.safe_load(f)
    return {"roles": {}, "tier_rank": {"public": 0, "internal": 1, "confidential": 2,
                                       "restricted": 3}}


ROLES_CFG = _roles_cfg()
TIER_RANK = ROLES_CFG.get("tier_rank", {})


class KB:
    def __init__(self):
        self.chunks: list[dict] = []
        self.chroma_backend = "tfidf"
        self._chroma = None
        self._load_seeds()
        self._load_store()
        self._init_chroma()

    def _load_seeds(self):
        try:
            files = sorted(os.listdir(SEED_DIR))
        except OSError:
            return
        for fn in files:
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(SEED_DIR, fn)) as f:
                    doc = json.load(f)
            except Exception:
                continue
            for i, ch in enumerate(doc.get("chunks", [])):
                self.chunks.append({
                    "chunk_id": f"{doc.get('doc_id', fn)}#c{i}",
                    "doc_id": doc.get("doc_id", fn),
                    "title": doc.get("title", fn),
                    "text": ch.get("text", ""),
                    "page": ch.get("page", 1),
                    "clearance_tier": doc.get("clearance_tier", "public"),
                    "unit": doc.get("unit", "ALL"),
                    "source_type": doc.get("source_type", "seed"),
                    "score": 0.0,
                })

    def _load_store(self):
        if not os.path.exists(KB_STORE):
            return
        try:
            with open(KB_STORE) as f:
                data = json.load(f)
        except Exception:
            return
        for doc in data if isinstance(data, list) else []:
            for ch in doc.get("chunks", []):
                self.chunks.append({**ch, "score": 0.0})

    def _save_store(self, doc: dict):
        try:
            data = []
            if os.path.exists(KB_STORE):
                with open(KB_STORE) as f:
                    data = json.load(f)
            data.append(doc)
            with open(KB_STORE, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _init_chroma(self):
        try:
            import chromadb
            client = chromadb.PersistentClient(path=CHROMA_DIR)
            self._chroma = client.get_or_create_collection("sov_kb")  # >=3 chars
            if self._chroma.count() == 0 and self.chunks:
                self._chroma.add(
                    ids=[c["chunk_id"] for c in self.chunks],
                    documents=[c["text"] for c in self.chunks],
                    metadatas=[{"tier": c["clearance_tier"], "unit": c["unit"]}
                               for c in self.chunks],
                    embeddings=[_hash_vec(c["text"]) for c in self.chunks])
            self.chroma_backend = "chroma(filter)+tfidf-rank"
        except Exception:
            self._chroma = None
            self.chroma_backend = "tfidf"

    def _allowed(self, role: str, unit: str) -> tuple[list[dict], int]:
        rcfg = ROLES_CFG.get("roles", {}).get(role, {})
        max_rank = TIER_RANK.get(rcfg.get("max_clearance_tier", "public"), -1)
        cross = bool(rcfg.get("cross_unit", False))
        ok, blocked = [], 0
        for c in self.chunks:
            tier_ok = TIER_RANK.get(c.get("clearance_tier", "public"), 99) <= max_rank
            unit_ok = cross or c.get("unit") in (unit, "ALL")
            if tier_ok and unit_ok:
                ok.append(c)
            else:
                blocked += 1
        return ok, blocked

    def query(self, query: str, role: str, unit: str, top_k: int = 4):
        allowed, blocked = self._allowed(role, unit)
        stats = {"allowed": len(allowed), "blocked": blocked, "backend": self.chroma_backend}
        if not allowed:
            return [], stats
        cand_ids = None
        if self._chroma is not None:
            try:
                tiers = [t for t, r in TIER_RANK.items()
                         if r <= TIER_RANK.get(
                             ROLES_CFG["roles"].get(role, {}).get("max_clearance_tier",
                                                                  "public"), -1)]
                res = self._chroma.query(query_embeddings=[_hash_vec(query)],
                                         n_results=min(len(allowed), max(top_k * 3, top_k)),
                                         where={"tier": {"$in": tiers}})
                cand_ids = set(res.get("ids", [[]])[0])
            except Exception:
                cand_ids = None
        pool = [c for c in allowed if cand_ids is None or c["chunk_id"] in cand_ids]
        if not pool:
            pool = allowed
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
            vec = TfidfVectorizer().fit_transform([c["text"] for c in pool] + [query])
            sims = cosine_similarity(vec[-1], vec[:-1])[0]
            ranked = sorted(zip(pool, sims), key=lambda x: x[1], reverse=True)[:top_k]
            out = [{**c, "score": round(float(s), 4)} for c, s in ranked]
        except Exception:
            out = [{**c, "score": 0.0} for c in pool[:top_k]]
        return out, stats

    def save_upload(self, doc: dict):
        for ch in doc.get("chunks", []):
            self.chunks.append({**ch, "score": 0.0})
        self._save_store(doc)
        if self._chroma is not None:
            try:
                self._chroma.add(
                    ids=[c["chunk_id"] for c in doc.get("chunks", [])],
                    documents=[c.get("text", "") for c in doc.get("chunks", [])],
                    metadatas=[{"tier": c.get("clearance_tier", "internal"),
                                "unit": c.get("unit", "MRPL-U2")}
                               for c in doc.get("chunks", [])],
                    embeddings=[_hash_vec(c.get("text", "")) for c in doc.get("chunks", [])])
            except Exception:
                pass


kb = KB()
