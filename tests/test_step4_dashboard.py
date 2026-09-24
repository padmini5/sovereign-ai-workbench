"""Step 4 (final freeze) tests: Dashboard as the single main workspace +
System & Capabilities page + friendly routing indicator (run:
python tests/test_step4_dashboard.py). Static source checks + a node check
for the dynamic date helper — no browser required.
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


def read(rel):
    with open(os.path.join(ROOT, "frontend", "src", rel), encoding="utf-8") as f:
        return f.read()


nav_src = read("nav.js")
main_src = read("main.jsx")
dash_src = read(os.path.join("pages", "Dashboard.jsx"))
login_src = read(os.path.join("pages", "Login.jsx"))
sys_src = read(os.path.join("pages", "SystemCapabilities.jsx"))
img_src = read(os.path.join("pages", "ImageAnalysis.jsx"))
chat_src = read("ai-chat.jsx")
i18n_src = read("i18n.js")
ws_src = read(os.path.join("pages", "Workspace.jsx"))
sw_src = read(os.path.join("pages", "StartWork.jsx"))

# ---------------- 1. routing integrity ----------------
print("== 1. route/nav integrity ==")
nav_ids = set(re.findall(r"id:\s*['\"]([^'\"]+)['\"]", nav_src))
case_ids = set(re.findall(r"case\s+['\"]([^'\"]+)['\"]\s*:", main_src))
check("every NAV id has a route (no dead links)",
      not sorted(nav_ids - case_ids), str(sorted(nav_ids - case_ids)))
check("every route has a NAV entry (no orphan routes)",
      not sorted(case_ids - nav_ids), str(sorted(case_ids - nav_ids)))
check("workspace route retired from nav + switch",
      "workspace" not in nav_ids and "case 'workspace'" not in main_src,
      str(sorted(nav_ids)))
check("system page present in nav + switch",
      "system" in nav_ids and "case 'system'" in main_src)
check("system page gated to ADMIN + security_admin",
      re.search(r"\{ id: 'system'.*roles: \['ADMIN', 'security_admin'\] \}",
                nav_src) is not None)
check("readRoute redirects workspace -> dashboard",
      "h === 'workspace'" in main_src and "return 'dashboard'" in main_src)
check("readRoute redirects generic work path too",
      "h === 'work'" in main_src)
check("workspace import removed from shell",
      "pages/Workspace.jsx" not in main_src)
check("Login homes land on dashboard (no workspace target)",
      "'workspace'" not in login_src and "HOME_BY_ROLE[me?.role] || 'dashboard'" in login_src)

# ---------------- 2. Dashboard = single main workspace ----------------
print("== 2. dashboard workspace ==")
check("Dashboard reads /api/v1/work/me",
      "'/api/v1/work/me'" in dash_src)
check("Dashboard reads /api/v1/work/activity",
      "'/api/v1/work/activity'" in dash_src)
check("no /api/v1/ai/status call on Dashboard",
      "'/api/v1/ai/status'" not in dash_src)
check("no legacy demo/debug pipeline on Dashboard",
      "'/api/tasks'" not in dash_src and "ws/monitor" not in dash_src
      and "getLegacyToken" not in dash_src and "TaskRunner" not in dash_src
      and "WebSocket" not in dash_src)
check("no sovereignty/monitor tiles on Dashboard",
      "EgressTile" not in dash_src and "SigningTile" not in dash_src
      and "HardwareTile" not in dash_src and "WORKFLOW_STEPS" not in dash_src)
hexes = set(h.lower() for h in re.findall(r"#[0-9a-fA-F]{3,8}\b", dash_src))
allow = set(x.lower() for x in
            ["#1a7f37", "#0a66c2", "#b54708", "#b42318", "#555"])
check("Dashboard raw hex within theme allowlist",
      not (hexes - allow), str(sorted(hexes - allow)))
check("5 role-gated feature cards (assistant/docs/images/coding/inspect)",
      all(x in dash_src for x in (
          "to: 'assistant'", "to: 'documents'", "to: 'images'",
          "cardCoding", "cardInspect"))
      and dash_src.count("to: 'agents'") == 2)
check("cards role-gated with hasPerm",
      dash_src.count("hasPerm(user") >= 5)
check("dynamic date via todayLabel",
      "todayLabel" in dash_src and "from '../dash-date.js'" in dash_src)
check("dashboard sections cover required areas",
      all(k in dash_src for k in (
          "dashAttendance", "dashWork", "dashProgress", "dashActivity")))

# ---------------- 3. dynamic date helper (node) ----------------
print("== 3. dynamic date helper ==")
dd_src = read("dash-date.js")
check("dash-date exports todayLabel", "export function todayLabel" in dd_src)
try:
    node_check = (
        "import { todayLabel } from './frontend/src/dash-date.js';"
        "const y = String(new Date().getFullYear());"
        "const out = todayLabel();"
        "console.log(typeof out === 'string' && out.includes(y)"
        " && out.length > 6 ? 'DATE-OK' : 'DATE-FAIL ' + out);"
    )
    r = subprocess.run(["node", "--input-type=module", "-e", node_check],
                       capture_output=True, text=True, timeout=30, cwd=ROOT)
    check("todayLabel renders current date dynamically",
          "DATE-OK" in r.stdout, (r.stdout + r.stderr)[:300])
except FileNotFoundError:
    check("todayLabel renders current date dynamically", False,
          "node not available")

# ---------------- 4. System & Capabilities page ----------------
print("== 4. system & capabilities page ==")
items = re.findall(r"\{ t: '", sys_src)
check("13 capability items", len(items) == 13, str(len(items)))
check("page title via systemCaps key",
      "T(lang, 'systemCaps')" in sys_src)
check("configurable/optional states marked",
      "Configurable" in sys_src and "Optional" in sys_src
      and "Included" in sys_src)
check("vision honesty described (no fake claim)",
      "SOV_MODEL_VISION" in sys_src and "instead of faking results" in sys_src)

# ---------------- 5. i18n keys ----------------
print("== 5. i18n keys defined in en fallback ==")
en_block = i18n_src.split("const EXTRA")[0]
new_keys = ["systemCaps", "dashAttendance", "dashWork", "dashProgress",
            "dashActivity", "dashFeatures", "cardCoding", "cardInspect",
            "noActivity", "dashNotMarked"]
missing = [k for k in new_keys if not re.search(rf"\b{k}:", en_block)]
check("all Step-4 keys defined in `en`", not missing, str(missing))

# ---------------- 6. preserved workspace pins ----------------
print("== 6. preserved source pins (workspace file untouched) ==")
seg = ws_src.split("function ReviewerView", 1)[-1].split("export default", 1)[0]
check("ReviewerView intact", "function ReviewerView" in ws_src
      and "return <ManagerView" not in seg)
check("manager workspace review actions intact",
      "<AssignWork" in ws_src and "requestCorrection" in ws_src
      and "'approved'" in ws_src)
check("StartWork status map intact",
      "needs_correction: T(lang, 'correctionRequested')" in sw_src
      and "approved: T(lang, 'completed')" in sw_src)
check("workspace key still defined in i18n (deep links)",
      re.search(r"\bworkspace:", en_block) is not None)

# ---------------- 7. friendly assistant ----------------
print("== 7. assistant: clean UI + routing indicator ==")
m = re.search(r"const SUGGESTIONS = \[(.*?)\];", chat_src, re.S)
n_sugg = len(re.findall(r"\['", m.group(1))) if m else -1
check("exactly 5 suggestion chips", n_sugg == 5, str(n_sugg))
check("no leftover quick-chip arrays",
      "QUICK_WORK" not in chat_src and "const QUICK" not in chat_src)
check("no debug stream checkbox",
      "setStreaming" not in chat_src)
check("no raw tools line under messages",
      "tools: {m.tools.join" not in chat_src)
check("captures SSE task frame", "ev === 'task'" in chat_src)
check("friendly indicator text present",
      "Task type: " in chat_src
      and "Selected local AI capability: " in chat_src)
check("no model names in indicator labels",
      re.search(r"TASK_LABEL = \{[^}]*llama|TASK_LABEL = \{[^}]*qwen",
                chat_src, re.I) is None)

# ---------------- 8. honest vision note ----------------
print("== 8. image analysis honest capability state ==")
check("exact honest sentence present",
      "Image analysis capability: Available when local vision model is configured." in img_src)
check("vision state sourced from /ai/status routing",
      "routing.vision" in img_src)

print(f"\n RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
