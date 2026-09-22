#!/usr/bin/env bash
# Step 25 remote deploy: upload the project over SSH, then run scripts/deploy.sh
# ON the server. Never connects without explicit arguments; placeholders only.
# Usage: ./scripts/remote-deploy.sh SSH_USER SERVER_IP [/opt/sovereign-ai-workbench]
set -euo pipefail
cd "$(dirname "$0")/.."

[ $# -ge 2 ] || { echo "usage: $0 SSH_USER SERVER_IP [REMOTE_DIR]" >&2; exit 2; }
SSH_USER="$1"; SERVER_IP="$2"; REMOTE_DIR="${3:-/opt/sovereign-ai-workbench}"
case "$SERVER_IP" in
  SERVER_IP|"" ) echo "refusing placeholder host (pass a real SERVER_IP)" >&2; exit 2;;
esac

echo "== upload (secrets, data, and build junk excluded) =="
rsync -avz --delete \
  --exclude '.env' --exclude '*.db' --exclude '*.db-journal' \
  --exclude 'backend/chroma_db/' --exclude 'backend/outputs/' \
  --exclude 'backend/uploads/' --exclude 'frontend/dist/' \
  --exclude 'frontend/node_modules/' --exclude '__pycache__/' \
  --exclude '.git/' \
  ./ "${SSH_USER}@${SERVER_IP}:${REMOTE_DIR}/" \
  || { echo "upload failed" >&2; exit 1; }

echo "== remote deploy =="
ssh "${SSH_USER}@${SERVER_IP}" "cd '${REMOTE_DIR}' && ./scripts/deploy.sh"

echo "== remote health =="
ssh "${SSH_USER}@${SERVER_IP}" "cd '${REMOTE_DIR}' && ./scripts/healthcheck.sh http://localhost:8080"
echo "REMOTE DEPLOY DONE (${SSH_USER}@${SERVER_IP}:${REMOTE_DIR})"
