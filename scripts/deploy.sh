#!/usr/bin/env bash
# Step 25 local deploy helper: validate -> build -> start -> health-check.
# Runs wherever Docker Engine + Compose v2 exist (workstation or, after
# upload, the Linux server). No secrets inside; fails fast with guidance.
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { echo "DEPLOY ERROR: $*" >&2; exit 1; }

echo "== 1/6 prerequisites =="
command -v docker >/dev/null || fail "docker not found (install Docker Engine 24+)"
docker compose version >/dev/null 2>&1 || fail "docker compose v2 plugin not found"
[ -f .env ] || fail "missing .env (copy: cp .env.example .env, then fill secrets)"
[ -f docker-compose.yml ] || fail "docker-compose.yml missing"
[ -f backend/Dockerfile ] || fail "backend/Dockerfile missing"
[ -f frontend/Dockerfile ] || fail "frontend/Dockerfile missing"

echo "== 2/6 required secrets present (values never printed) =="
for v in SOV_JWT_SECRET POSTGRES_PASSWORD POSTGRES_USER POSTGRES_DB; do
  val="$(grep -E "^${v}=" .env | cut -d= -f2- || true)"
  [ -n "$val" ] || fail "$v is unset in .env"
  case "$val" in
    *change-me*|\${*|"" ) fail "$v still holds a placeholder in .env";;
  esac
done
echo "secrets present (not shown)"

echo "== 3/6 compose config =="
docker compose config >/dev/null || fail "docker compose config invalid"

echo "== 4/6 build =="
docker compose build backend frontend || fail "build failed"

echo "== 5/6 start =="
docker compose up -d db
docker compose up -d backend frontend || fail "startup failed"
docker compose ps

echo "== 6/6 health (120s budget) =="
for i in $(seq 1 24); do
  if curl -fsS -m 5 http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "backend healthy"
    curl -fsS -m 5 http://127.0.0.1:8000/health
    echo
    echo "frontend: $(curl -s -o /dev/null -w '%{http_code}' -m 5 http://localhost:8080/phase1.html)"
    echo "DEPLOY OK"
    exit 0
  fi
  sleep 5
done
fail "backend unhealthy after 120s (see: docker compose logs backend db)"
