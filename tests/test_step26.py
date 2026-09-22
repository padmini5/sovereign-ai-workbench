"""Step 26 tests: SIH-ready product UI (run: python tests/test_step26.py).

1. No implementation names leak into user-facing frontend sources.
2. Theme system present (3 themes, dark default, localStorage persistence).
3. Friendly error mapping behaves (via node on status.js, no deps).
4. The configured local model actually answers through the chat path.
"""
import os
import re
import subprocess
import sys
import tempfile

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


SRC = os.path.join(ROOT, "frontend", "src")
FORBIDDEN = [r"ollama", r"llama", r"sqlite", r"pgvector", r"oqs", r"ed25519",
             r"ml-dsa", r"ml-kem", r"chromadb?", r"tesseract", r"whisper",
             r"piper", r"\bvosk\b", r"espeak", r"moondream", r"qwen", r"nomic",
             r"\bembed\b", r"embeddings?"]
RENDER_PATTERNS = [r"st\.provider", r"st\.model", r"result\.provider",
                   r"result\.model", r"ocr\.engine", r"stt\.provider",
                   r"tts\.provider", r"pqc\.", r"llm_mode", r"embed_dim",
                   r"vector_backend", r"translate_provider",
                   r"filter_stats\?\.backend"]
# status.js holds the scrub patterns by design; admin.jsx diagnostics line is
# the dedicated developer area (ADMIN-only). Comments are not UI.
SKIP_FILES = {"status.js"}
SKIP_LINE_IF = {"admin.jsx": ["JSON.stringify"]}


def code_lines(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s.startswith(("//", "*", "/*")):
                continue
            out.append(line)
    return out


print("== 1. no implementation names in user-facing sources ==")
hits = []
for base, _, files in os.walk(SRC):
    for fn in sorted(files):
        if not fn.endswith((".js", ".jsx")) or fn in SKIP_FILES:
            continue
        rel = os.path.relpath(os.path.join(base, fn), SRC)
        for i, line in enumerate(code_lines(os.path.join(base, fn)), 1):
            if any(tok in line for tok in SKIP_LINE_IF.get(fn, [])):
                continue
            for pat in FORBIDDEN + RENDER_PATTERNS:
                if re.search(pat, line, re.IGNORECASE):
                    hits.append(f"{rel}:{i}:{pat}:{line.strip()[:90]}")
check("zero leaks", not hits, "\n".join(hits[:10]))

print("== 2. scrub-table replacements are clean ==")
table = open(os.path.join(SRC, "status.js"), encoding="utf-8").read()
labels = re.findall(r", '([^']+)'\]", table)
check("replacement labels clean",
      not any(re.search(p, " ".join(labels), re.IGNORECASE) for p in FORBIDDEN),
      str(labels))

print("== 3. theme system ==")
theme = open(os.path.join(SRC, "theme.js"), encoding="utf-8").read()
check("three themes", all(f"'{t}'" in theme or f'"{t}"' in theme
      for t in ("dark", "light", "high-contrast")))
check("dark default + localStorage + data-theme",
      "DEFAULT_THEME = 'dark'" in theme and "sov_theme" in theme
      and "data-theme" in theme and "dataset.theme" in theme)
css = open(os.path.join(SRC, "workbench.css"), encoding="utf-8").read()
check("theme blocks", '[data-theme="light"]' in css and '[data-theme="high-contrast"]' in css
      and ":root" in css)
hc = css.split('[data-theme="high-contrast"]')[1].split("}")[0] if '[data-theme="high-contrast"]' in css else ""
check("dark identity kept", "--bg: #0b111b" in css)

print("== 4. friendly errors via node ==")
node_check = r"""
import('./frontend/src/status.js').then((m) => {
  const t = [];
  t.push(m.friendlyError(502, null) === 'AI service is temporarily unavailable. Please try again.');
  t.push(m.friendlyError(403, null) === "You don't have permission to perform this action.");
  t.push(m.friendlyError(404, null) === 'The requested item could not be found.');
  t.push(m.friendlyError(422, null) === 'Please check the information you entered.');
  t.push(m.friendlyError(429, null) === 'Too many requests. Please wait a moment and try again.');
  t.push(m.friendlyError(500, null) === 'Something went wrong. Please try again.');
  t.push(m.friendlyError(502, 'ollama model llama3.2:3b missing (set OLLAMA_MODEL)') === 'AI service model AI model missing (set service configuration)');
  t.push(!/ollama|llama|sqlite|ed25519/i.test(m.aiStatus({reachable: true}).label));
  t.push(m.aiStatus({reachable: true}).label === 'Local AI • Ready');
  t.push(m.signStatus().label === 'Secure signing • Active');
  console.log(t.every(Boolean) ? 'NODE-OK' : 'NODE-FAIL ' + JSON.stringify(t));
}).catch((e) => console.log('NODE-FAIL ' + e.message));
"""
try:
    r = subprocess.run(["node", "--input-type=module", "-e", node_check],
                       capture_output=True, text=True, timeout=30, cwd=ROOT)
    check("status.js mappings", "NODE-OK" in r.stdout, (r.stdout + r.stderr)[:300])
except FileNotFoundError:
    check("status.js mappings", False, "node not available")

print("== 5. configured local model answers through chat ==")
os.environ["SOV_AI_PROVIDER"] = "ollama"
os.environ["OLLAMA_MODEL"] = "llama3.2:3b"
try:
    import urllib.request
    urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3).read()
    daemon = True
except Exception:
    daemon = False
if not daemon:
    check("ollama daemon for live test", False, "daemon unreachable; skipping live answer check")
else:
    from fastapi.testclient import TestClient  # noqa: E402
    from backend.app.main import app  # noqa: E402
    cc = TestClient(app)
    login = cc.post("/api/v1/auth/login",
                    json={"username": "admin", "password": "Admin123!"}).json()
    st = cc.get("/api/v1/ai/status",
                headers={"Authorization": "Bearer " + login["access_token"]}).json()
    check("provider is ollama (not mock)", st.get("provider") == "ollama", str(st)[:150])
    ans = cc.post("/api/v1/ai/chat",
                  json={"messages": [{"role": "user", "content": "Reply with exactly: MODEL-OK"}]},
                  headers={"Authorization": "Bearer " + login["access_token"]},
                  timeout=120).json()
    check("model answered", "MODEL-OK" in ans.get("message", {}).get("content", ""),
          ans.get("message", {}).get("content", "")[:200])

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
