"""Step 24 tests: Docker containerization (run: python tests/test_step24.py).

No container runtime exists in this environment, so this suite validates
everything validatable without a daemon: compose structure, Dockerfiles,
ignore files, nginx config, env placeholders, secret hygiene, README docs —
plus a runtime check that the container entrypoint path actually imports.
`docker compose build/up` commands are documented in README for a host
with Docker Engine.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

passed = failed = 0
def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")


def read(p):
    with open(os.path.join(ROOT, p)) as f:
        return f.read()


import yaml  # noqa: E402

print("== 1. compose structure ==")
compose = yaml.safe_load(read("docker-compose.yml"))
svcs = compose.get("services", {})
check("services = db/backend/frontend/ollama",
      set(svcs) == {"db", "backend", "frontend", "ollama"}, str(sorted(svcs)))
check("ollama is opt-in profile", svcs["ollama"].get("profiles") == ["local-ai"])
check("db not published", "ports" not in svcs["db"])
check("backend localhost-only", svcs["backend"].get("ports") == ["127.0.0.1:8000:8000"])
check("frontend localhost-only (never public directly)",
      svcs["frontend"].get("ports") == ["127.0.0.1:8080:8080"])
vols = compose.get("volumes", {})
check("volumes pgdata/sov-data", {"pgdata", "sov-data"} <= set(vols), str(sorted(vols)))
bvols = " ".join(svcs["backend"].get("volumes", []))
check("backend persists /data", "/data" in bvols and "sov-data" in bvols)
check("healthchecks everywhere",
      all("healthcheck" in svcs[s] for s in ("db", "backend", "frontend")))
check("backend waits for healthy db",
      svcs["backend"]["depends_on"]["db"]["condition"] == "service_healthy")
check("restart policies",
      all(svcs[s].get("restart") == "unless-stopped" for s in ("db", "backend", "frontend")))
check("single internal network",
      all(svcs[s].get("networks") == ["internal"] for s in ("db", "backend", "frontend", "ollama"))
      and "internal" in compose.get("networks", {}))
check("db credentials are references, not values",
      "${POSTGRES_PASSWORD:?" in yaml.safe_dump(svcs["db"])
      and "hunter2" not in yaml.safe_dump(compose))
check("DATABASE_URL built from refs, no literal password",
      "postgresql://${POSTGRES_USER" in yaml.safe_dump(svcs["backend"]))

print("== 2. backend Dockerfile ==")
bd = read("backend/Dockerfile")
check("slim base", "python:3.12-slim" in bd)
check("layout incl. roles.yaml", "COPY requirements.txt roles.yaml" in bd and "COPY backend ./backend" in bd)
check("psycopg for PG path", "psycopg[binary]" in bd)
check("non-root", re.search(r"^USER appuser", bd, re.M) is not None)
check("healthcheck /health", "HEALTHCHECK" in bd and "/health" in bd)
check("entrypoint module path", 'backend.app.main:app' in bd)
check("no model download", "ollama pull" not in bd and "weights" not in bd.lower())
check("no secrets baked", "SECRET" not in bd and "PASSWORD" not in bd and "API_KEY" not in bd)
check("no dev bind in CMD", "--host" in bd and "0.0.0.0" in bd)

print("== 3. frontend Dockerfile + nginx ==")
fd = read("frontend/Dockerfile")
check("multi-stage", len(re.findall(r"^FROM ", fd, re.M)) == 2)
check("npm ci + build", "npm ci" in fd and "npm run build" in fd)
check("nginx non-root", "USER nginx" in fd and "8080" in fd)
check("healthcheck page", "HEALTHCHECK" in fd and "phase1.html" in fd)
nx = read("frontend/nginx.conf")
check("proxies api+ws to backend", "proxy_pass http://backend:8000" in nx
      and "location /api/" in nx and "location /ws/" in nx)
check("no secrets in nginx", "SECRET" not in nx and "PASSWORD" not in nx)

print("== 4. ignore files ==")
bi, fi = read("backend/.dockerignore"), read("frontend/.dockerignore")
check("backend ignores runtime data",
      all(x in bi for x in ("__pycache__", "*.db", "chroma_db/", "uploads/")))
check("frontend ignores build inputs", "node_modules/" in fi and "dist/" in fi and ".env" in fi)

print("== 5. env placeholders only ==")
ex = read(".env.example")
check("pg placeholders", all(k in ex for k in ("POSTGRES_USER=", "POSTGRES_PASSWORD=", "POSTGRES_DB=")))
check("jwt placeholder, not real",
      "SOV_JWT_SECRET=change-me-to-a-long-random-secret-min-32-chars" in ex)
check("no real passwords",
      not re.search(r"(?im)^\s*[^#\s]*password\s*=\s*(?!change-me|\$\{)\S+", ex))
check("no real tokens", "hunter2" not in ex and "supersecret" not in ex)

print("== 6. README documents operations ==")
rm = read("README.md")
for topic in ["Prerequisites", "docker compose build", "docker compose up",
              "docker compose down", "logs", "health", "volumes", "Ollama",
              "rebuild", "cp .env.example .env"]:
    check(f"README: {topic}", topic in rm)

print("== 7. container entrypoint path works ==")
import tempfile as _t
os.environ["SOV_DATA_DIR"] = os.path.join(_t.mkdtemp(prefix="sov_docker_"))
os.environ["SOV_AI_PROVIDER"] = "mock"
from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import app  # noqa: E402  (same module path as image CMD)
cc = TestClient(app)
check("health ok", cc.get("/health").json().get("ok") is True)
_tok = cc.post("/api/v1/auth/login",
               json={"username": "admin", "password": "Admin123!"}).json()["access_token"]
check("auth in image layout", cc.get("/api/v1/auth/me",
      headers={"Authorization": "Bearer " + _tok}).json()["role"] == "ADMIN")

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
