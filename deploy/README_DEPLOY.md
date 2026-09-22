# SovereignAI Workbench — Production Deployment (Linux VPS over SSH)

No rebuild needed: this deploys the existing app as-is. Ollama and liboqs are
NOT required — the app starts on local fallbacks and picks them up later automatically.

## 0. What runs where

- Backend: FastAPI + uvicorn on `127.0.0.1:8000` (systemd, restarts on reboot).
- Frontend: static Vite build (`frontend/dist`) served by nginx.
- nginx: HTTPS + reverse proxy for `/api/*`, `/ws/*` (WebSocket upgrade), `/health`.
- Data (SQLite audit, ChromaDB, uploads, signed DOCX): `/var/lib/sov-workbench`.

## 1. SSH into the VPS

```bash
ssh user@YOUR_VPS_IP
sudo apt update && sudo apt upgrade -y
```

## 2. Install Python + Node + nginx

```bash
sudo apt install -y python3 python3-venv python3-pip git nginx certbot python3-certbot-nginx
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
python3 --version   # need 3.11+
node --version      # need 20+
```

## 3. Deploy the code

```bash
sudo mkdir -p /opt/sovereign-ai-workbench /var/lib/sov-workbench
sudo chown -R $USER:$USER /opt/sovereign-ai-workbench
cd /opt/sovereign-ai-workbench
git clone <YOUR_REPO_URL> .   # or: scp -r ./sovereign-ai-workbench/* user@VPS:/opt/sovereign-ai-workbench/
```

## 4. Backend: venv + dependencies

```bash
cd /opt/sovereign-ai-workbench
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

## 5. Frontend: install + production build

Same-origin (recommended: frontend and backend on one domain via nginx below) —
build with empty base so the app uses relative `/api` + same-host WebSockets:

```bash
cd /opt/sovereign-ai-workbench/frontend
npm install
npm run build   # SIH demo: demo accounts stay visible (VITE_DEMO_CREDS defaults to 1)
# Locked-down variant: VITE_DEMO_CREDS=0 npm run build   # strips demo test-passwords
# output: frontend/dist/
```

Remote-backend variant (frontend on a different host than the API):

```bash
VITE_API_BASE_URL=https://api.example.com VITE_WS_BASE_URL=wss://api.example.com npm run build
```

## 6. Environment variables

```bash
sudo cp /opt/sovereign-ai-workbench/.env.example /etc/sovereign-ai-workbench.env
sudo nano /etc/sovereign-ai-workbench.env
```

Required changes (see `.env.example` for the full list):

```bash
SOV_ENV=production
SOV_JWT_SECRET=<output of: python3 -c "import secrets; print(secrets.token_hex(32))">
SOV_CORS_ORIGINS=https://workbench.example.com
SOV_DEMO_LOGIN=0
SOV_DATA_DIR=/var/lib/sov-workbench
```

`SOV_DEMO_LOGIN=0` hides the one-click demo accounts (test passwords are then
not advertised in the UI; manual login still works). Nothing else is secret.

## 7. systemd: keep running after SSH disconnect + reboot

```bash
sudo cp /opt/sovereign-ai-workbench/deploy/sov-workbench-backend.service /etc/systemd/system/
sudo useradd -r -s /usr/sbin/nologin sov 2>/dev/null || true
sudo chown -R sov:sov /opt/sovereign-ai-workbench /var/lib/sov-workbench
sudo systemctl daemon-reload
sudo systemctl enable --now sov-workbench-backend
sudo systemctl status sov-workbench-backend
curl http://127.0.0.1:8000/health
# {"ok": true, "service": "sovereignai-workbench", ...}
```

Logs and restarts:

```bash
sudo journalctl -u sov-workbench-backend -f     # live logs
sudo journalctl -u sov-workbench-backend --since today | tail -50
sudo systemctl restart sov-workbench-backend
```

`enable` = starts automatically after reboot. `Restart=always` = revives crashes.

## 8. nginx: reverse proxy + WebSocket + HTTPS

```bash
sudo cp /opt/sovereign-ai-workbench/deploy/nginx-sovereign-ai.conf /etc/nginx/sites-available/sovereign-ai
sudo nano /etc/nginx/sites-available/sovereign-ai   # set server_name + dist path
sudo ln -s /etc/nginx/sites-available/sovereign-ai /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
curl http://127.0.0.1/health
sudo certbot --nginx -d workbench.example.com       # HTTPS
curl https://workbench.example.com/health
```

WebSocket proxying is already in the example (`Upgrade`/`Connection` headers,
long timeouts for the task/monitor streams). Verify in the browser: open a task
and confirm the live plan panel updates.

## 9. Smoke test in production

```bash
BASE=https://workbench.example.com
curl $BASE/health
TOKEN=$(curl -s -X POST $BASE/api/login -H 'Content-Type: application/json' \
  -d '{"username":"manager1","password":"mgr123"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
curl -s -X POST $BASE/api/query -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"query":"corroded pipe NDT"}' | head -c 400
```

Then in the browser: field login (internal only) → manager login (confidential +
citations) → sign → DOCX download → monitor shows app egress 0.

## 10. Later upgrades (no code changes)

- Ollama: install it, `SOV_OLLAMA_URL=http://127.0.0.1:11434` (default), pull
  `qwen2.5:7b-instruct` — `/health` flips `llm_mode` to `ollama` automatically.
- PQC: build liboqs, set `SOV_PQC=1`, restart — real ML-DSA-65/ML-KEM via the
  same adapter; UI label flips from "PQC unavailable / fallback signing".

## 11. Updating

```bash
cd /opt/sovereign-ai-workbench && git pull
./venv/bin/pip install -r requirements.txt
cd frontend && npm install && npm run build
sudo systemctl restart sov-workbench-backend
```

## 12. Backup (both halves — the demo depends on it)

The deployment keeps state in TWO places (by design for this demo; do not
migrate now). Back up BOTH or restores will be incomplete:

- **PostgreSQL volume** (`pgdata`): documents, conversations, vector-related
  data where currently used.
- **Application data** (`sov-data` mounted at `/data`, or
  `/var/lib/sov-workbench` on native installs): authentication, audit,
  agents, work/attendance records, uploads, private images.

```bash
# Docker path — stop writers first for a consistent snapshot:
docker compose stop backend
docker run --rm -v sovereign-ai-workbench_pgdata:/pg -v sov-backup:/b \
  alpine tar czf /b/pgdata-$(date +%F).tgz -C /pg .
docker run --rm -v sovereign-ai-workbench_sov-data:/d -v sov-backup:/b \
  alpine tar czf /b/sov-data-$(date +%F).tgz -C /d .
docker compose start backend

# Native path:
sudo tar czf /root/sov-backup-$(date +%F).tgz /var/lib/sov-workbench
# (+ pg_dump if DATABASE_URL points at an external PostgreSQL)
```

Verify a backup by listing the archive contents before demo day. No
complicated backup system is implemented — these snapshots are the process.
