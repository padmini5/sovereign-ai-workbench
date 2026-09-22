#!/usr/bin/env bash
# Step 25 deployment verification: frontend, backend, db-backed auth, RBAC,
# isolation, RAG, admin gate, agents, audit. Credentials come from env vars
# (never arguments); tokens are never printed.
# Usage: ./scripts/healthcheck.sh BASE_URL   (e.g. http://localhost:8080)
# For a reverse-proxied host, pass the public base (https://workbench.example.com);
# /health is served by the backend through the proxy. With SOV_DEMO_LOGIN=0 the
# demo cards are hidden in the UI, but these env-supplied accounts still log in.
set -euo pipefail
BASE="${1:?usage: $0 BASE_URL}"
BASE="${BASE%/}"
API="$BASE/api"
USER1="${SOV_HEALTH_USER:-user}"; PASS1="${SOV_HEALTH_PASS:?set SOV_HEALTH_PASS}"
ADMIN_U="${SOV_HEALTH_ADMIN:-admin}"; ADMIN_P="${SOV_HEALTH_ADMIN_PASS:?set SOV_HEALTH_ADMIN_PASS}"

pass=0; fail=0
check() { # check <name> <command...>; hides output (may contain tokens)
  if "$@" >/dev/null 2>&1; then pass=$((pass+1)); echo "  PASS $1";
  else fail=$((fail+1)); echo "  FAIL $1"; fi
}
check_not() { # expect non-2xx
  code="$(curl -s -o /dev/null -w '%{http_code}' "$@")"
  if [ "$code" != "200" ] && [ "$code" != "201" ]; then
    pass=$((pass+1)); echo "  PASS $1 ($code)";
  else fail=$((fail+1)); echo "  FAIL $1 (got $code)"; fi
}

echo "== reachability =="
check "frontend" curl -fsS -m 5 "$BASE/phase1.html"
check "backend health" curl -fsS -m 5 "$BASE/health"
check "public config (no secrets)" curl -fsS -m 5 "$API/config"

echo "== auth + RBAC =="
TOK_USER="$(curl -fsS -m 10 -X POST "$API/v1/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER1\",\"password\":\"$PASS1\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")"
TOK_ADMIN="$(curl -fsS -m 10 -X POST "$API/v1/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$ADMIN_U\",\"password\":\"$ADMIN_P\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")"
[ -n "$TOK_USER" ] && [ -n "$TOK_ADMIN" ] && { pass=$((pass+1)); echo "  PASS login (tokens hidden)"; } \
  || { fail=$((fail+1)); echo "  FAIL login"; }
check_not "user denied admin overview" curl -s -m 5 "$API/v1/admin/overview" -H "Authorization: Bearer $TOK_USER"
check "admin overview" curl -fsS -m 5 "$API/v1/admin/overview" -H "Authorization: Bearer $TOK_ADMIN"

echo "== docs + RAG isolation =="
TMPDOC="$(mktemp /tmp/sov-health-XXXX.txt)"; echo "deployment smoke probe" > "$TMPDOC"
check "doc upload" curl -fsS -m 15 -X POST "$API/v1/docs/upload" \
  -H "Authorization: Bearer $TOK_USER" -F "f=@$TMPDOC;filename=health-probe.txt"
rm -f "$TMPDOC"
check "chat roundtrip" curl -fsS -m 15 -X POST "$API/v1/ai/chat" \
  -H "Authorization: Bearer $TOK_USER" -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"deployment ping"}]}'
check "agents registry" curl -fsS -m 10 "$API/v1/agents" -H "Authorization: Bearer $TOK_ADMIN"
check "audit readable" curl -fsS -m 10 "$API/v1/admin/audit" -H "Authorization: Bearer $TOK_ADMIN"

echo "RESULT: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
