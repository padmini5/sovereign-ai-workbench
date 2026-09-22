-- 001_rag.sql — Phase 4 RAG schema (PostgreSQL + pgvector).
-- Runtime (SQLite) applies the equivalent DDL automatically; this file is
-- the authoritative migration for Postgres deployments.
-- Apply:  psql $DATABASE_URL -f database/migrations/001_rag.sql

-- documents/doc_texts are created by earlier phases; ensure status values:
--   status: UPLOADED | PROCESSING | READY | FAILED

CREATE TABLE IF NOT EXISTS doc_chunks (
  id          TEXT PRIMARY KEY,
  doc_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  owner_id    TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  page        INTEGER,
  filename    TEXT NOT NULL,
  text        TEXT NOT NULL,
  embedding   vector,               -- requires: CREATE EXTENSION vector;
  created_at  DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc   ON doc_chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_owner ON doc_chunks(owner_id);

-- Similarity search (permission pre-filter BEFORE scoring):
--   SELECT id, doc_id, chunk_index, page, filename, text,
--          1 - (embedding <=> $1::vector) AS score
--   FROM doc_chunks WHERE owner_id = $2            -- or all rows for ADMIN
--     AND doc_id = ANY($3)                          -- when scoped to docs
--   ORDER BY embedding <=> $1::vector LIMIT $4;

-- Optional HNSW acceleration once the table is large:
--   CREATE INDEX ON doc_chunks USING hnsw (embedding vector_cosine_ops);
