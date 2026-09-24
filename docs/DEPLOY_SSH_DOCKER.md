# Step 25 — Deploying the Dockerized stack to a Linux server over SSH

Target: the **Docker Compose stack** from Step 24 (`docker-compose.yml`).
For the native (no-Docker) path, see `deploy/README_DEPLOY.md` instead.

Conventions: `SERVER_IP`, `SSH_USER`, `DOMAIN_NAME` are placeholders.
Nothing here contains real credentials. Nothing here connects anywhere —
every remote command shows the exact `ssh` invocation for you to run.

## 1. Server requirements

- Linux x86_64 (Ubuntu 22.04/24.04 recommended), kernel with cgroup v2
- Docker Engine 24+ **and** Compose v2 plugin (`docker compose version`)
- Minimum: 2 vCPU / 4 GB RAM / 20 GB disk (4 vCPU / 8 GB if running Ollama)
- Outbound internet for pulling base images (ghcr/docker.io) on first build

## 2. Firewall / ports

Public entry point is the reverse proxy (section 6), not the app:

| Port | Source | Purpose |
|---|---|---|
| 22/tcp | admin IPs only | SSH |
| 80/tcp, 443/tcp | internet | HTTP→HTTPS + app (via reverse proxy) |

Do NOT publish PostgreSQL (no `ports:` on `db` — internal network only).
The compose file publishes `127.0.0.1:8080:8080` (frontend, loopback only)
and `127.0.0.1:8000:8000` (backend, loopback only). Behind a reverse proxy
you may drop both `ports:` blocks and proxy to the container names instead
(see section 6).

`ufw` example (run as SSH_USER with sudo):
```bash
sudo ufw default deny incoming
sudo ufw allow from <ADMIN_IP> to any port 22
sudo ufw allow 80,443/tcp
sudo ufw enable
```

## 3. Connect + prepare directories

```bash
ssh SSH_USER@SERVER_IP
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git ufw
sudo systemctl enable --now docker
sudo usermod -aG docker SSH_USER   # re-login afterwards
sudo mkdir -p /opt/sovereign-ai-workbench
sudo chown -R SSH_USER:SSH_USER /opt/sovereign-ai-workbench
```
Application data lives in Docker volumes (`pgdata`, `sov-data`), owned by
container UIDs — no root-owned app files on the host beyond what Docker
itself requires.

## 4. Upload the project

```bash
# from your workstation:
scp -r ./sovereign-ai-workbench SSH_USER@SERVER_IP:/opt/sovereign-ai-workbench/
# or: git clone <YOUR_REPO_URL> /opt/sovereign-ai-workbench
```
`.env`, `*.db`, uploads, and `node_modules` must never be copied —
`scripts/remote-deploy.sh` excludes them automatically (see `.gitignore`).

## 5. Environment + secrets (on the server)

```bash
cd /opt/sovereign-ai-workbench
cp .env.example .env
chmod 600 .env
python3 -c "import secrets; print(secrets.token_hex(32))"  # -> SOV_JWT_SECRET
python3 -c "import secrets; print(secrets.token_hex(24))"  # -> POSTGRES_PASSWORD
```
Set in `.env`: `SOV_JWT_SECRET`, `POSTGRES_PASSWORD`,
`SOV_CORS_ORIGINS=https://DOMAIN_NAME`, `SOV_DEMO_LOGIN=0`.
Generate fresh values per deployment — never reuse, never commit `.env`.

## 6. Start + verify

```bash
./scripts/deploy.sh            # validates, builds, starts, health-checks
./scripts/healthcheck.sh http://localhost:8080   # full predeploy-style check
curl -s http://127.0.0.1:8000/health
```

## 7. Logs / restart / shutdown

```bash
docker compose logs -f backend      # app logs (no secrets by construction)
docker compose logs -f db
docker compose restart backend
docker compose down                 # stop, keep volumes
docker compose down -v              # DANGER: deletes DB + uploads
```

## 8. Reverse proxy + HTTPS (production)

Terminate TLS in front of the stack (nginx/Caddy/Traefik — provider-neutral):

```nginx
server {
    listen 80; server_name DOMAIN_NAME;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl; server_name DOMAIN_NAME;
    ssl_certificate /etc/letsencrypt/live/DOMAIN_NAME/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/DOMAIN_NAME/privkey.pem;
    location /api/ { proxy_pass http://127.0.0.1:8000/; proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme; }
    location /ws/ { proxy_pass http://127.0.0.1:8000/;
        proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade"; }
    location / { proxy_pass http://127.0.0.1:8080/; }
}
```
DNS `DOMAIN_NAME` → `SERVER_IP`, then e.g. `certbot --nginx -d DOMAIN_NAME`.
WebSocket upgrade headers are required for live task/agent streams.
If the proxy and app share a host, `SOV_CORS_ORIGINS=https://DOMAIN_NAME`.

## 9. Backup (PostgreSQL + volumes)

```bash
BACKUP_DIR=/opt/sovereign-backups/$(date +%F); mkdir -p $BACKUP_DIR
docker compose exec -T db pg_dump -U $POSTGRES_USER $POSTGRES_DB | gzip > $BACKUP_DIR/db.sql.gz
docker run --rm -v sovereign-ai-workbench_sov-data:/data -v $BACKUP_DIR:/out \
  alpine tar czf /out/sov-data.tgz -C /data .
cp .env $BACKUP_DIR/env.bak && chmod 600 $BACKUP_DIR/env.bak
```
Back up: `pgdata` (via pg_dump), `sov-data` (uploads, ChromaDB, sqlite
fallbacks), and `.env` (required for recovery). Stop writes first for a
crash-consistent snapshot: `docker compose stop backend` (db keeps running
for pg_dump). Never auto-delete production data.

Restore:
```bash
docker compose up -d db
gunzip -c $BACKUP_DIR/db.sql.gz | docker compose exec -T db psql -U $POSTGRES_USER $POSTGRES_DB
docker run --rm -v sovereign-ai-workbench_sov-data:/data -v $BACKUP_DIR:/in \
  alpine tar xzf /in/sov-data.tgz -C /data
docker compose up -d
```

## 10. Update / rollback

```bash
./scripts/healthcheck.sh http://localhost:8080   # baseline
# backup (section 9)
cd /opt/sovereign-ai-workbench && git pull   # or re-upload
docker compose config                          # validate before touching anything
docker compose build backend frontend
docker compose up -d
./scripts/healthcheck.sh http://localhost:8080
python verify.py  # from a checkout with backend deps, against 127.0.0.1:8000
```
Rollback: `git checkout <previous-tag> && docker compose build && docker
compose up -d` (+ DB restore from section 9 if the schema moved). Never
`docker compose down -v` during an update; never prune volumes automatically.

### Local models (never pulled at build time)

The stack builds and starts with NO model download. For real local answers,
pull the two routed models once on the server (optional `local-ai` profile):

```bash
docker compose --profile local-ai up -d
docker compose exec ollama ollama pull llama3.2:3b          # GENERAL/DOCUMENT
docker compose exec ollama ollama pull qwen2.5-coder:1.5b   # CODING
```

Optional vision capability — without `SOV_MODEL_VISION` the Image Analysis
page honestly reports "Available when local vision model is configured."
(nothing fakes visual output):

```bash
echo 'SOV_MODEL_VISION=<your-local-vision-model>' >> .env
docker compose up -d
docker compose exec ollama ollama pull <your-local-vision-model>
```

PostgreSQL is never published (internal network only) and the model daemon
stays loopback/reverse-proxy only — never internet-exposed (section 2).

## 11. Production configuration checklist

- `SOV_ENV=production`, `SOV_DEMO_LOGIN=0`, restrictive `SOV_CORS_ORIGINS`
- Secrets only in server-side `.env` (0600), never in images/compose/docs
- Rate limits (`SOV_RATE_*`), provider selection (`SOV_AI_PROVIDER`, …),
  storage (`SOV_DATA_DIR=/data`, `DATABASE_URL`) all remain env-driven
- `deploy/` native path untouched; compose path uses `database/migrations/`
  DDL only for external inspection (tables self-initialize)

## 12. SSH security (recommendations, apply manually)

- SSH keys, not passwords; `PasswordAuthentication no` once keys work
- `PermitRootLogin no`; dedicated deploy user (`SSH_USER`) in `docker` group
- `sudo apt upgrade` regularly; enable `ufw` (section 2); `fail2ban` for SSH
- Never store private keys in the repo; never paste secrets into chat/logs
