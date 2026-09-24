"""Step 4 (final freeze) tests: honest optional VISION capability (run:
python tests/test_step4_vision.py). Isolated temp DBs, mock AI, no model
downloads. Proves: VISION is a configured server-side capability with an
empty default, chat can never select it, status reports it honestly, and
the UI shows the exact honest capability sentence.
"""
import os
import re
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="sov_vision_")
for k in ["SOV_AUTH_DB", "SOV_AUTH_AUDIT_DB", "SOV_CHAT_DB", "SOV_DOCS_DB",
          "SOV_VECTORS_DB", "SOV_UPLOADS_DIR", "SOV_AGENTS_DB",
          "SOV_APPROVALS_DB", "SOV_APPROVALS_DIR", "SOV_SYSCONFIG_PATH",
          "SOV_WORK_DB"]:
    os.environ[k] = os.path.join(_tmp, k.replace("SOV_", "").lower())
os.environ["SOV_AI_PROVIDER"] = "mock"
os.environ["SOV_EMBED_PROVIDER"] = "hash"
os.environ["SOV_TRANSLATE_PROVIDER"] = "mock"
# Deterministic model-config defaults (vision must default to unset/empty).
for k in ("SOV_MODEL_VISION", "OLLAMA_MODEL", "SOV_MODEL_GENERAL",
          "SOV_MODEL_CODING", "SOV_OLLAMA_MODEL", "SOV_MODEL_DEFAULT",
          "OLLAMA_BASE_URL"):
    os.environ.pop(k, None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import app  # noqa: E402

c = TestClient(app)
H = lambda t: {"Authorization": "Bearer " + t}  # noqa: E731

passed = failed = 0
def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def login(u, p):
    r = c.post("/api/v1/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


# ---------------- 1. routing categories ----------------
print("== 1. routing categories ==")
from backend.app.ai.task_router import (  # noqa: E402
    CATEGORIES, CHAT_CATEGORIES, classify, model_for, normalize_category)

check("CATEGORIES includes VISION", "VISION" in CATEGORIES, str(CATEGORIES))
check("chat categories exclude VISION",
      "VISION" not in CHAT_CATEGORIES
      and CHAT_CATEGORIES == ("GENERAL", "DOCUMENT", "CODING"),
      str(CHAT_CATEGORIES))
check("normalize_category accepts vision (case-insensitive)",
      normalize_category("vision") == "VISION")
check("classify never auto-selects VISION",
      all(classify(t) != "VISION" for t in (
          "what does this photo show",
          "look at this image of a valve",
          "analyze this picture")))
try:
    normalize_category("gpt-4o")
    check("invalid category still raises (fail-loud)", False)
except ValueError:
    check("invalid category still raises (fail-loud)", True)

# ---------------- 2. vision model configuration ----------------
print("== 2. vision model configuration (empty default, env override) ==")
check("VISION default is empty (not configured)",
      model_for("VISION") == "", repr(model_for("VISION")))
try:
    os.environ["SOV_MODEL_VISION"] = "unit-vision-override"
    check("SOV_MODEL_VISION env override",
          model_for("VISION") == "unit-vision-override")
finally:
    os.environ.pop("SOV_MODEL_VISION", None)
check("VISION still empty after override removed",
      model_for("VISION") == "")
from backend.app.sysconfig import CONFIG_SCHEMA  # noqa: E402
check("sysconfig schema exposes SOV_MODEL_VISION",
      CONFIG_SCHEMA.get("SOV_MODEL_VISION", {}).get("type") == "model",
      str(CONFIG_SCHEMA.get("SOV_MODEL_VISION")))

# ---------------- 3. chat cannot reach VISION ----------------
print("== 3. chat cannot select VISION (clients never choose models) ==")
from pydantic import ValidationError  # noqa: E402
from backend.app.ai_api import ChatIn  # noqa: E402

try:
    ChatIn(messages=[{"role": "user", "content": "hi"}], task_type="VISION")
    check("ChatIn rejects task_type=VISION", False)
except ValidationError:
    check("ChatIn rejects task_type=VISION", True)
try:
    ok = ChatIn(messages=[{"role": "user", "content": "hi"}],
                task_type="CODING")
    check("ChatIn still accepts CODING", ok.task_type == "CODING")
except ValidationError as e:
    check("ChatIn still accepts CODING", False, str(e)[:200])

T_ADM = login("admin", "Admin123!")
r = c.post("/api/v1/ai/chat", headers=H(T_ADM),
           json={"messages": [{"role": "user", "content": "hello"}],
                 "task_type": "VISION"})
check("endpoint task_type=VISION -> 422", r.status_code == 422,
      str(r.status_code))

# ---------------- 4. honest status reporting ----------------
print("== 4. /ai/status reports vision honestly ==")
st = c.get("/api/v1/ai/status", headers=H(T_ADM))
j = st.json() if st.status_code == 200 else {}
check("status 200", st.status_code == 200, str(st.status_code))
check("routing.vision empty when unset",
      j.get("routing", {}).get("vision") == "", str(j.get("routing")))
check("existing routing entries intact",
      j.get("routing", {}).get("general") == "llama3.2:3b"
      and j.get("routing", {}).get("document") == "llama3.2:3b"
      and j.get("routing", {}).get("coding") == "qwen2.5-coder:1.5b",
      str(j.get("routing")))
try:
    os.environ["SOV_MODEL_VISION"] = "unit-vision-status"
    j2 = c.get("/api/v1/ai/status", headers=H(T_ADM)).json()
    check("routing.vision reflects configured value",
          j2.get("routing", {}).get("vision") == "unit-vision-status",
          str(j2.get("routing")))
finally:
    os.environ.pop("SOV_MODEL_VISION", None)

# ---------------- 5. honest UI wording + stream frame ----------------
print("== 5. honest UI wording + SSE routing frame ==")
img = read(os.path.join("frontend", "src", "pages", "ImageAnalysis.jsx"))
check("exact honest vision sentence in ImageAnalysis",
      "Image analysis capability: Available when local vision model is configured." in img)
ai_src = read(os.path.join("backend", "app", "ai_api.py"))
check("chat stream emits friendly task frame",
      'yield f"event: task\\ndata: {cat}\\n\\n"' in ai_src)
chat_src = read(os.path.join("frontend", "src", "ai-chat.jsx"))
check("frontend captures the task frame",
      "ev === 'task'" in chat_src)
check("frontend shows friendly routing indicator",
      "Task type: " in chat_src
      and "Selected local AI capability: " in chat_src)

# ---------------- 6. deployment configuration ----------------
print("== 6. deployment configuration (no secrets, no downloads) ==")
compose = read("docker-compose.yml")
check("compose passes SOV_MODEL_VISION to backend",
      "SOV_MODEL_VISION: ${SOV_MODEL_VISION:-}" in compose)
pull_lines = [ln for ln in compose.splitlines() if "ollama pull" in ln]
check("compose never pulls models (pull hints are comments only)",
      all(ln.strip().startswith("#") for ln in pull_lines), str(pull_lines))
env_ex = read(".env.example")
check(".env.example documents SOV_MODEL_VISION",
      "SOV_MODEL_VISION=" in env_ex)
check(".env.example keeps placeholders only",
      "hunter2" not in env_ex and "supersecret" not in env_ex)
readme = read("README.md")
readme_flat = re.sub(r"\s+", " ", readme)
check("README documents SOV_MODEL_VISION honest state",
      "SOV_MODEL_VISION" in readme
      and "Available when local vision model is configured." in readme_flat)
check("README pull line names the routed models",
      "ollama pull llama3.2:3b qwen2.5-coder:1.5b" in readme)
deploy = read(os.path.join("docs", "DEPLOY_SSH_DOCKER.md"))
check("deploy doc has model pull section",
      "ollama pull llama3.2:3b" in deploy and "SOV_MODEL_VISION" in deploy)

print(f"\n RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
