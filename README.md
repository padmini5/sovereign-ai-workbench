# SovereignAI Workbench — SIH26117 (Autonex)

> **Start here:** the current UI is the hash-routed workbench shell —
> run it via Docker (`docker compose up -d`, app at http://localhost:8080,
> dashboard at http://localhost:8080/#/dashboard) or natively
> (`frontend`: `npm install && npm run dev` → http://localhost:3000/#/dashboard
> with the backend on `:8000`). Public VPS deployment: `docs/DEPLOY_SSH_DOCKER.md`.
> Demo accounts are seeded by `scripts/seed_demo.py` (admin/employee/operator/
> reviewer/manager `@company.com`); legacy `field1`-style logins below still work.
> Never commit `.env` (see `.env.example`); deployment secrets live only on the server.

Local-first, role-based agentic AI prototype. Runs fully offline; no external API calls.

## Quick start (Windows, no Docker/Ollama required)
```bat
pip install -r requirements.txt
uvicorn backend.app.main:app --port 8000
:: second terminal:
cd frontend && npm install && npm run dev
:: open http://localhost:3000
```
Demo logins: `field1/field123`, `process1/proc123`, `safety1/safe123`, `manager1/mgr123`, `admin1/adm123`, `audit1/aud123`.

## 90-second demo
1. Login as `field1`, run the default corrosion query → only public+internal chunks, confidential blocked (see filter stats).
2. Login as `manager1`, same query → confidential U2 chunks appear with `[doc#cN p.X]` citations.
3. As manager: Sign with PQC key → signed DOCX download. Network monitor stays at 0.
4. Toggle hardware tier 7b↔3b, re-run to show speed/quality tradeoff.
5. Auditor (`audit1`) verifies audit trail.

## Offline / sovereign notes
- Retrieval enforces `roles.yaml` tier+unit filter BEFORE the LLM sees text (`backend/app/rag.py:query`). ChromaDB (persistent, `backend/chroma_db`) applies the tier `where` pre-filter; TF-IDF re-ranks the allowed subset. Custom offline hash embeddings — no embedding-model downloads, works fully offline. `filter_stats.backend` reports `chroma(filter)+tfidf-rank` (or `tfidf` if Chroma is missing).
- Models: Ollama `qwen2.5:7b/3b-instruct`, `bge-m3`, `moondream` when reachable; deterministic local fallback otherwise — zero network required. `/api/health` reports `llm_mode: ollama|local-fallback`. Ollama is never mandatory.
- PQC adapter (`backend/app/pqc.py`): real ML-DSA-65 + ML-KEM when liboqs is ready AND `SOV_PQC=1` is set; otherwise an honestly-labelled Ed25519 fallback (`PQC unavailable / fallback signing (NOT ML-DSA)` in UI + `/api/health`). `SOV_PQC` defaults to `0` because importing `oqs-python` without built liboqs triggers a git-clone/build attempt (network egress) and raises `SystemExit`. `SOV_HYBRID=1` enables hybrid-note mode. Rationale: NIST PQC standard + harvest-now-decrypt-later.
- UI: 21 interface languages (English default + 20 Indian-scheduled languages) via the header selector; technical IDs, citations `[doc#cN p.X]`, units, filenames, algorithm names stay in English. `GET /api/languages` lists them.
- Sandbox: production runs generated code in `docker --network none`; prototype restricts to local exec stub.
- Seed KB: `backend/seed_docs/*.json` across public/internal/confidential/restricted.
- Tests: `python verify.py` (original suite) + `python verify_phase2.py` (chroma backend, i18n, PQC honesty, 403, zero app egress).

## One-command deploy (when Docker present)
```
docker compose up
ollama pull qwen2.5:7b-instruct qwen2.5:3b-instruct bge-m3 moondream
```

## Docker deployment (Step 24)

Prerequisites: Docker Engine 24+ with the Compose v2 plugin. No model
downloads happen during image build; Ollama stays optional.

Architecture: `frontend` (nginx, static build) → `backend` (FastAPI) →
`db` (PostgreSQL + pgvector, internal network only). Optional `ollama`
service behind the `local-ai` profile. The app auto-creates its tables on
startup (see `database/migrations/` for the authoritative Postgres DDL).

Environment setup (placeholders only — never commit real secrets):
```
cp .env.example .env
:: edit .env: SOV_JWT_SECRET, POSTGRES_PASSWORD, SOV_CORS_ORIGINS
```

Build / start / stop / logs / health:
```
docker compose config        :: validate (needs .env present)
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f backend
curl http://127.0.0.1:8000/health
:: app: http://localhost:8080   (API docs: http://127.0.0.1:8000/api/docs)
docker compose down          :: stop (volumes kept)
```

Persistent volumes: `pgdata` (PostgreSQL), `sov-data` (uploads, sqlite
fallbacks, ChromaDB at `/data`), `ollama-models` (profile only). Uploaded
paths stay confined to `/data` (Step 23 guards apply in-container too).

Ollama / local AI:
```
docker compose --profile local-ai up -d
docker compose exec ollama ollama pull qwen2.5:7b-instruct
:: or point at an external daemon: SOV_OLLAMA_URL=http://host:11434
:: without Ollama the app runs on the mock provider (see /api/v1/ai/status)
```

Tests (native, unchanged workflow):
```
python verify.py && python verify_phase2.py
python tests/test_phase1.py  # ... test_step24.py
```
SSH deployment to a Linux server (Step 25): see
`docs/DEPLOY_SSH_DOCKER.md` (`scripts/deploy.sh`,
`scripts/remote-deploy.sh`, `scripts/healthcheck.sh`).

Rebuild / reset dev data safely:
```
docker compose build --no-cache backend   :: rebuild one service
docker compose down -v                    :: DANGER: deletes pgdata + uploads
:: safe reset of app data only: docker volume rm <project>_sov-data
```
