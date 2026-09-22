"""Step 25 tests: SSH deployment preparation (run: python tests/test_step25.py).

Static validation (bash syntax, placeholders, secret hygiene, doc coverage,
gitignore) + a runtime check that the app is untouched. No SSH connections,
no Docker daemon required.
"""
import os
import re
import subprocess
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


SCRIPTS = ["scripts/deploy.sh", "scripts/remote-deploy.sh", "scripts/healthcheck.sh"]

print("== 1. shell syntax (bash -n) ==")
def _posix(p):
    p = os.path.join(ROOT, *p.split("/"))
    m = re.match(r"^([A-Za-z]):\\(.*)$", p)
    if m:  # WSL bash: C:\x -> /mnt/c/x
        return "/mnt/" + m.group(1).lower() + "/" + m.group(2).replace("\\", "/")
    return p.replace("\\", "/")


for s in SCRIPTS:
    try:
        r = subprocess.run(["bash", "-n", _posix(s)],
                           capture_output=True, text=True, timeout=30)
        check(f"bash -n {s}", r.returncode == 0, r.stderr[:200])
    except FileNotFoundError:
        check(f"bash -n {s}", False, "bash not available")

print("== 2. safe-script properties ==")
dep, rem, hc = (read(s) for s in SCRIPTS)
check("fail-fast everywhere",
      all("set -euo pipefail" in s for s in (dep, rem, hc)))
check("remote needs explicit args",
      'usage: $0 SSH_USER SERVER_IP' in rem and "[ $# -ge 2 ]" in rem)
check("placeholder host refused",
      "refusing placeholder host" in rem and "SERVER_IP" in rem)
check("no secret values echoed",
      "echo \"$val\"" not in dep and "echo $val" not in dep
      and "secrets present (not shown)" in dep)
check("tokens never printed",
      "echo \"$TOK" not in hc and "echo $TOK" not in hc
      and "tokens hidden" in hc)
check("rsync excludes secrets+data",
      all(x in rem for x in ("--exclude '.env'", "--exclude '*.db'",
                             "frontend/node_modules/", "backend/uploads/")))
check("deploy validates then builds",
      all(x in dep for x in ("docker compose config", "docker compose build",
                             "docker compose up -d", "/health", "compose ps")))

print("== 3. no real hosts or secrets in repo ==")
text = dep + rem + hc + read("docs/DEPLOY_SSH_DOCKER.md")
ips = set(re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text))
check("only loopback IPs", ips <= {"127.0.0.1", "0.0.0.0"}, str(ips))
for label, blob in [("deploy.sh", dep), ("remote-deploy.sh", rem),
                    ("healthcheck.sh", hc), ("deploy doc", read("docs/DEPLOY_SSH_DOCKER.md"))]:
    m = re.search(r"(?im)^[^#\n]*\b(password|passwd|secret|api[_-]?key)\b\s*=\s*['\"]?([^'\"\s$<]+)",
                  blob)
    bad = m and "change-me" not in m.group(2) and m.group(2) not in ("no",)
    check(f"no secret literals in {label}", not bad, m.group(0)[:80] if m else "")

print("== 4. deployment doc coverage ==")
doc = read("docs/DEPLOY_SSH_DOCKER.md")
for topic in ["ufw", "reverse proxy", "certbot", "pg_dump", "rollback",
              "SOV_CORS_ORIGINS", "PasswordAuthentication", "PermitRootLogin",
              "fail2ban", "/opt/sovereign-ai-workbench", "sov-data",
              "healthcheck.sh", "remote-deploy.sh", "WebSocket", "X-Forwarded-Proto"]:
    check(f"doc: {topic}", topic in doc)

print("== 5. gitignore ==")
gi = read(".gitignore")
for pat in [".env", "*.db", "chroma_db/", "frontend/dist/", "node_modules/",
            "__pycache__/", "uploads/*"]:
    check(f"ignores {pat}", pat in gi)

print("== 6. README links deployment ==")
check("README -> ssh doc", "docs/DEPLOY_SSH_DOCKER.md" in read("README.md"))

print("== 7. app untouched (runtime) ==")
import tempfile as _t
os.environ["SOV_DATA_DIR"] = os.path.join(_t.mkdtemp(prefix="sov_dep_"))
os.environ["SOV_AI_PROVIDER"] = "mock"
from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import app  # noqa: E402
cc = TestClient(app)
check("health ok", cc.get("/health").json().get("ok") is True)
_tok = cc.post("/api/v1/auth/login",
               json={"username": "admin", "password": "Admin123!"}).json()["access_token"]
check("admin overview ok",
      cc.get("/api/v1/admin/overview",
             headers={"Authorization": "Bearer " + _tok}).status_code == 200)

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
