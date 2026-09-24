"""Step 3 tests: coding agent + secure sandbox + bounded test/fix loop.
(run: python tests/test_coding_agent.py)

Matrix: auth/RBAC/no-bypass, server-side CODING routing (qwen2.5-coder),
workspace + path isolation (.env/.git/ssh/traversal rejected), real sandbox
execution (network/DB/docker-socket/env denied, timeout, output caps),
failure detection -> failure fed back -> bounded correction -> re-test,
honest VERIFIED/failed states, and audit evidence without source/prompts.

Generated code executes ONLY in ephemeral Docker containers - never on the
host. Requires: docker CLI + the sov-sandbox:latest image
(build: docker build -t sov-sandbox:latest ./sandbox).
"""
import json
import os
import subprocess
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="sov_step3_")
os.environ["SOV_AUTH_DB"] = os.path.join(_tmp, "auth.db")
os.environ["SOV_AUTH_AUDIT_DB"] = os.path.join(_tmp, "auth_audit.db")
os.environ["SOV_CHAT_DB"] = os.path.join(_tmp, "chat.db")
os.environ["SOV_DOCS_DB"] = os.path.join(_tmp, "docs.db")
os.environ["SOV_VECTORS_DB"] = os.path.join(_tmp, "vectors.db")
os.environ["SOV_UPLOADS_DIR"] = os.path.join(_tmp, "uploads")
os.environ["SOV_AGENTS_DB"] = os.path.join(_tmp, "agents.db")
os.environ["SOV_DATA_DIR"] = os.path.join(_tmp, "data")
os.environ["SOV_AI_PROVIDER"] = "mock"
os.environ["SOV_EMBED_PROVIDER"] = "hash"
os.environ["SOV_TRANSLATE_PROVIDER"] = "mock"
for _k in ("SOV_SANDBOX", "SOV_SANDBOX_TIMEOUT", "SOV_CODING_MAX_ITERATIONS",
           "SOV_MODEL_CODING"):
    os.environ.pop(_k, None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app import agent_store, audit_log  # noqa: E402
from backend.app import coding_sandbox as sbx  # noqa: E402
from backend.app.agents import AGENT_REGISTRY, TOOL_REGISTRY, exec_tool  # noqa: E402
import backend.app.ai as ai_mod  # noqa: E402

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


def login(u, p):
    r = c.post("/api/v1/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


# ---------------------------------------------------------------- fixtures

GOAL = ("Create a Python function average_inspection_scores(csv_path) that reads "
        "a comma-separated CSV file with a header row containing a 'score' column "
        "and returns the arithmetic average of all rows whose score parses as a "
        "float. Rows with blank or non-numeric scores must be ignored. Return 0.0 "
        "when no valid scores exist. Standard library only.")

BUGGY_SOLUTION = (
    '"""Inspection score average."""\n'
    "import csv\n"
    "\n"
    "def average_inspection_scores(csv_path):\n"
    "    total = 0.0\n"
    "    count = 0\n"
    '    with open(csv_path, newline="") as f:\n'
    "        reader = csv.DictReader(f)\n"
    "        for row in reader:\n"
    '            total += float(row["score"])   # BUG: does not skip invalid\n'
    "            count += 1\n"
    "    return total / count if count else 0.0\n"
)

FIXED_SOLUTION = (
    '"""Inspection score average."""\n'
    "import csv\n"
    "\n"
    "def average_inspection_scores(csv_path):\n"
    "    total = 0.0\n"
    "    count = 0\n"
    '    with open(csv_path, newline="") as f:\n'
    "        reader = csv.DictReader(f)\n"
    "        for row in reader:\n"
    '            raw = (row.get("score") or "").strip()\n'
    "            try:\n"
    "                score = float(raw)\n"
    "            except ValueError:\n"
    "                continue\n"
    "            total += score\n"
    "            count += 1\n"
    "    return total / count if count else 0.0\n"
)

GOOD_TESTS = (
    "import csv\n"
    "from solution import average_inspection_scores\n"
    "\n"
    "def _write(path, rows):\n"
    '    with open(path, "w", newline="") as f:\n'
    "        w = csv.writer(f)\n"
    '        w.writerow(["id", "score"])\n'
    "        w.writerows(rows)\n"
    "\n"
    "def test_average_ignores_invalid_rows(tmp_path):\n"
    '    p = tmp_path / "s.csv"\n'
    '    _write(p, [["1", "4.0"], ["2", "N/A"], ["3", "6.0"], ["4", ""]])\n'
    "    assert average_inspection_scores(str(p)) == 5.0\n"
    "\n"
    "def test_all_valid(tmp_path):\n"
    '    p = tmp_path / "s.csv"\n'
    '    _write(p, [["1", "3.0"], ["2", "3.0"]])\n'
    "    assert average_inspection_scores(str(p)) == 3.0\n"
    "\n"
    "def test_no_valid_rows(tmp_path):\n"
    '    p = tmp_path / "s.csv"\n'
    '    _write(p, [["1", "N/A"], ["2", "xx"]])\n'
    "    assert average_inspection_scores(str(p)) == 0.0\n"
)

ISOLATION_TEST = (
    "import os\n"
    "import socket\n"
    "\n"
    "def test_no_external_network():\n"
    "    try:\n"
    '        socket.create_connection(("1.1.1.1", 80), timeout=2).close()\n'
    "    except OSError:\n"
    "        return\n"
    '    raise AssertionError("external network reachable")\n'
    "\n"
    "def test_no_database():\n"
    '    for host in ("127.0.0.1", "db"):\n'
    "        try:\n"
    '            socket.create_connection((host, 5432), timeout=2).close()\n'
    "        except OSError:\n"
    "            continue\n"
    '        raise AssertionError("database reachable")\n'
    "\n"
    "def test_no_docker_socket():\n"
    '    assert not os.path.exists("/var/run/docker.sock")\n'
    "\n"
    "def test_no_secret_files():\n"
    '    for p in ("/.env", "/app/.env", "/root/.ssh", "/root/.ssh/id_rsa"):\n'
    "        assert not os.path.exists(p), p\n"
    "\n"
    "def test_environment_clean():\n"
    "    assert not any(k.startswith('SOV_') for k in os.environ)\n"
    '    for k in ("DATABASE_URL", "JWT_SECRET", "SOV_JWT_SECRET"):\n'
    "        assert k not in os.environ\n"
    '    assert os.environ.get("HOME") == "/tmp"\n'
    "\n"
    "def test_cwd_is_workspace():\n"
    '    assert os.getcwd() == "/workspace"\n'
    "\n"
    "def test_rootfs_readonly():\n"
    "    try:\n"
    '        open("/etc/evil.txt", "w").close()\n'
    "    except OSError:\n"
    "        return\n"
    '    raise AssertionError("root filesystem writable")\n'
)

SLEEP_TEST = (
    "import time\n"
    "\n"
    "def test_slow():\n"
    "    time.sleep(30)\n"
)

OUTPUT_TEST = (
    "def test_loud():\n"
    '    print("X" * 300000, flush=True)\n'
    "    assert True\n"
)


class FakeAI:
    """Scripted stand-in for the model service: proves routing/feedback
    wiring while REAL execution stays in the Docker sandbox."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def generate(self, messages, system="", task_type="", **kw):
        self.calls.append({"task_type": task_type, "system": system,
                           "prompt": str(messages[-1].get("content", ""))})
        if not self.script:
            raise AssertionError("unexpected extra model call")
        item = self.script.pop(0)
        if callable(item):
            return item(self.calls[-1])
        return item


_orig_get_service = ai_mod.get_service


def install(fake):
    ai_mod.get_service = lambda *a, **k: fake


def restore():
    ai_mod.get_service = _orig_get_service


def revision_producer(call):
    prompt = call["prompt"]
    assert "failed its test suite" in prompt, "failure info not fed back"
    assert "test_average_ignores_invalid_rows" in prompt, \
        "actual pytest failure evidence missing from feedback"
    return FIXED_SOLUTION


def api_run(tok, body):
    return c.post("/api/v1/agents/coding_agent/run", json=body, headers=H(tok))


def run_actions(rid):
    return [r for r in audit_log.rows(10000) if r.get("resource") == rid]


T_EMP = login("employee@company.com", "Employee@2026#R4m!")
T_USR = login("user", "User123!")
T_AUD = login("audit1", "aud123")
T_ADM = login("admin", "Admin123!")

# ================================================================ A. gating
print("== A. registry, tools, auth/RBAC, document isolation ==")
reg = c.get("/api/v1/agents", headers=H(T_EMP)).json()["agents"]
entry = next((a for a in reg if a["name"] == "coding_agent"), None)
check("registry lists coding_agent", entry is not None)
check("employee may run it", bool(entry and entry["allowed"]))
check("registry tools are the allow-list",
      bool(entry) and set(entry["tools"]) == {
          "create_workspace", "write_source_file", "write_test_file",
          "run_tests", "inspect_test_result", "revise_code",
          "finalize_verified_result"}, str(entry and entry["tools"]))

spec = AGENT_REGISTRY["coding_agent"]
check("task_type CODING", spec.get("task_type") == "CODING")
check("gated by AI_CHAT+AI_AGENT_USE",
      spec.get("perms") == ["AI_CHAT", "AI_AGENT_USE"], str(spec.get("perms")))
check("documents forbidden", spec.get("forbids_documents") is True)
check("deterministic workflow (no LLM planner)",
      spec.get("coding_workflow") is True and spec.get("deterministic_plan") is True)
check("limits bounded", spec["max_steps"] <= 20 and spec["timeout_s"] <= 600)

tools = c.get("/api/v1/agents/tools", headers=H(T_EMP)).json()["tools"]
names = {t["name"] for t in tools}
check("coding tools exposed", {"create_workspace", "run_tests",
                               "finalize_verified_result"} <= names, str(names))
coding_defs = [t for t in tools if t["name"] in set(spec["tools"])]
check("tool defs complete",
      len(coding_defs) == 7
      and all({"name", "desc", "confirm", "args", "returns"} <= set(t)
              for t in coding_defs))
check("no confirmation-gated coding tool",
      all(t["confirm"] is False for t in coding_defs))
check("no doc/rag tool bound to coding agent",
      not ({"rag_search", "analyze_document", "list_my_docs", "get_audit_summary",
            "generate_report"} & set(spec["tools"])))
check("coding tools gated by AI_AGENT_USE",
      all(TOOL_REGISTRY[n]["perm"] == "AI_AGENT_USE" for n in spec["tools"])
      and all(TOOL_REGISTRY[n]["roles"] is None for n in spec["tools"]))

before_runs = len(c.get("/api/v1/agents/runs", headers=H(T_EMP)).json()["runs"])
check("unauthenticated run -> 401",
      c.post("/api/v1/agents/coding_agent/run",
             json={"goal": GOAL}).status_code == 401)
check("USER (no AI_AGENT_USE) -> 403",
      api_run(T_USR, {"goal": GOAL, "lang": "en"}).status_code == 403)
check("auditor (no AI_CHAT) -> 403",
      api_run(T_AUD, {"goal": GOAL, "lang": "en"}).status_code == 403)
check("USER agents registry -> 403",
      c.get("/api/v1/agents", headers=H(T_USR)).status_code == 403)
r = api_run(T_EMP, {"goal": GOAL, "lang": "en", "document_ids": ["whatever"]})
check("documents rejected -> 400", r.status_code == 400, str(r.status_code))
check("document rejection wording",
      "documents" in str(r.json().get("detail", "")), str(r.json()))
after_runs = len(c.get("/api/v1/agents/runs", headers=H(T_EMP)).json()["runs"])
check("denied/rejected attempts created no runs",
      before_runs == after_runs == 0, f"{before_runs}->{after_runs}")
check("denial audited",
      any("lacks AI_AGENT_USE" in (row.get("detail") or "")
          for row in audit_log.rows(2000)))

# ============================================== B. verified correction loop
print("== B. authenticated task -> sandbox -> failure -> fix -> VERIFIED ==")
fake = FakeAI([BUGGY_SOLUTION, GOOD_TESTS, revision_producer])
install(fake)
try:
    r = api_run(T_EMP, {"goal": GOAL, "lang": "en"})
finally:
    restore()
check("run accepted", r.status_code == 200, r.text[:300])
recB = r.json()
ridB = recB.get("id", "")
check("state COMPLETED", recB.get("state") == "COMPLETED",
      str(recB.get("state")))
resB = recB.get("result") or ""
check("VERIFIED result", resB.startswith("VERIFIED"), resB[:200])
check("real pytest evidence",
      "pytest completed successfully:" in resB and "passed" in resB, resB[:300])
check("actual model recorded", "qwen2.5-coder:1.5b" in resB, resB[:300])
check("iteration count recorded", "iterations=2/3" in resB, resB[:300])
check("sandbox metadata recorded", "network=none" in resB, resB[:300])

stepsB = recB.get("steps") or []
toolsB = [s.get("tool") for s in stepsB]
check("step sequence exact",
      toolsB == ["create_workspace", "write_source_file", "write_test_file",
                 "run_tests", "inspect_test_result", "revise_code",
                 "run_tests", "finalize_verified_result"], str(toolsB))
wsrc = next(s for s in stepsB if s["tool"] == "write_source_file")
wtest = next(s for s in stepsB if s["tool"] == "write_test_file")
check("source file actually created",
      isinstance(wsrc.get("files"), dict)
      and "def average_inspection_scores" in wsrc["files"].get("solution.py", ""))
check("test file actually created",
      isinstance(wtest.get("files"), dict)
      and "def test_average_ignores_invalid_rows" in
      wtest["files"].get("test_solution.py", ""))
rev = next(s for s in stepsB if s["tool"] == "revise_code")
check("correction differs from first draft",
      rev.get("files", {}).get("solution.py")
      and rev["files"]["solution.py"] != wsrc["files"]["solution.py"])
runs = [s for s in stepsB if s["tool"] == "run_tests"]
check("exactly two executions", len(runs) == 2, str(len(runs)))
check("first execution really failed",
      runs[0].get("exec", {}).get("status") == "failed"
      and runs[0]["exec"].get("exit_code") == 1, str(runs[0].get("exec")))
check("failure output is real pytest text",
      "ValueError" in runs[0]["exec"].get("output", "")
      or "1 failed" in runs[0]["exec"].get("output", ""),
      runs[0]["exec"].get("output", "")[:200])
check("second execution really passed",
      runs[1].get("exec", {}).get("status") == "passed"
      and runs[1]["exec"].get("exit_code") == 0, str(runs[1].get("exec")))
check("finalize step says VERIFIED",
      "VERIFIED" in (stepsB[-1].get("result") or ""))

check("model called3 times, all CODING",
      len(fake.calls) == 3
      and all(cc["task_type"] == "CODING" for cc in fake.calls),
      str([cc["task_type"] for cc in fake.calls]))
check("failure info fed back to coding model",
      "test_average_ignores_invalid_rows" in fake.calls[2]["prompt"],
      fake.calls[2]["prompt"][-300:])
check("prompt kept to task (min info)",
      "score" in fake.calls[0]["prompt"] and "document" not in
      fake.calls[0]["prompt"].lower().replace("documentation", ""))

wsB = sbx.workspace_for(ridB)
rootB = os.path.realpath(os.path.join(_tmp, "data", "coding"))
check("workspace isolated under data dir",
      wsB.startswith(rootB) and os.path.basename(wsB) == "cw-" + ridB, wsB)
check("workspace cleaned up after run", not os.path.exists(wsB), wsB)

actsB = {row["action"] for row in run_actions(ridB)}
needB = {"agent_started", "model_route", "coding_task_started",
         "coding_workspace_created", "code_generated", "tests_generated",
         "sandbox_started", "tests_executed", "test_failed", "code_revision",
         "tests_reexecuted", "coding_task_verified", "agent_completed"}
check("full workflow audited", needB <= actsB, str(sorted(needB - actsB)))
_global_rows = audit_log.rows(10000)
check("tool calls audited under agent_tool resource",
      any(r["action"] == "tool_requested"
          and str(r.get("resource", "")).startswith("agent_tool:")
          for r in _global_rows)
      and any(r["action"] == "tool_allowed"
              and str(r.get("resource", "")).startswith("agent_tool:")
              for r in _global_rows))
mrB = [row for row in run_actions(ridB) if row["action"] == "model_route"]
check("model_route exact",
      mrB and mrB[0].get("detail") == "task_type=CODING model=qwen2.5-coder:1.5b",
      str(mrB and mrB[0].get("detail")))
sbB = [row for row in run_actions(ridB) if row["action"] == "sandbox_started"]
check("sandbox audited network=none + image",
      len(sbB) == 2
      and all("network=none" in row["detail"] and "image=sov-sandbox:latest"
              in row["detail"] for row in sbB),
      str([row["detail"] for row in sbB]))
teB = [row for row in run_actions(ridB) if row["action"] == "tests_executed"]
check("executions audited with status",
      len(teB) == 2
      and any("iteration=1 status=failed" in row["detail"] for row in teB)
      and any("iteration=2 status=passed" in row["detail"] for row in teB),
      str([row["detail"] for row in teB]))
check("code_revision audited with hash only",
      any(row["action"] == "code_revision" and "sha256=" in row["detail"]
          and "def " not in row["detail"] for row in run_actions(ridB)))
check("verified audited with model+summary",
      any(row["action"] == "coding_task_verified"
          and "model=qwen2.5-coder:1.5b" in row["detail"]
          and "passed" in row["detail"] for row in run_actions(ridB)))
check("agent_completed says VERIFIED",
      any(row["action"] == "agent_completed" and "status=VERIFIED" in row["detail"]
          for row in run_actions(ridB)))
check("no failure event on success",
      not any(row["action"] == "coding_task_failed"
              for row in run_actions(ridB)))

rd = c.get(f"/api/v1/agents/runs/{ridB}", headers=H(T_EMP))
check("owner can read run detail", rd.status_code == 200, str(rd.status_code))
check("detail keeps generated files",
      "def average_inspection_scores" in json.dumps(rd.json().get("steps") or []))
check("other user cannot read the run",
      c.get(f"/api/v1/agents/runs/{ridB}",
            headers=H(T_USR)).status_code in (403, 404))

# ============================== C. path/filesystem isolation + guards
print("== C. traversal, secret paths, ownership, verification guard ==")
U_EMP = {"id": "u-emp-coding", "role": "EMPLOYEE"}
U_OTH = {"id": "u-oth-coding", "role": "EMPLOYEE"}
recC = agent_store.create("coding_agent", U_EMP["id"], U_EMP["role"],
                          "traversal probe", "en")
ridC = recC["id"]
out = exec_tool(U_EMP, "coding_agent", "create_workspace", {"run_id": ridC})
check("workspace created via tool",
      out.get("workspace") == "cw-" + ridC, str(out))
wsC = sbx.workspace_for(ridC)

bad_names = ["../../evil.py", "..\\..\\evil.py", ".env", ".git/config",
             "sub/evil.py", "C:\\Windows\\evil.py", "/etc/passwd", "..",
             "id_rsa", "solution.py\x00.txt", ".ssh\\id_rsa"]
codes = []
for bn in bad_names:
    try:
        exec_tool(U_EMP, "coding_agent", "write_source_file",
                  {"run_id": ridC, "filename": bn, "content": "x = 1"})
        codes.append(200)
    except HTTPException as e:
        codes.append(e.status_code)
    except Exception as e:
        codes.append(type(e).__name__)
check("traversal/secret names all rejected400",
      all(v == 400 for v in codes), str(list(zip(bad_names, codes))))
parent = os.path.dirname(wsC)
check("nothing written outside workspace",
      not os.path.exists(os.path.join(parent, "evil.py"))
      and not os.path.exists(os.path.join(_tmp, "evil.py"))
      and sorted(os.listdir(parent)) == ["cw-" + ridC], str(os.listdir(parent)))
check("workspace still empty", os.listdir(wsC) == [], str(os.listdir(wsC)))
check("audit denied the traversal attempts",
      sum(1 for row in audit_log.rows(5000)
          if row.get("resource") == "agent_tool:write_source_file"
          and row.get("decision") == "deny") >= len(bad_names))
try:
    exec_tool(U_EMP, "coding_agent", "write_test_file",
              {"run_id": ridC, "filename": "evil.py", "content": "x = 1"})
    tcode = 200
except HTTPException as e:
    tcode = e.status_code
check("test files must be test_*.py", tcode == 400, str(tcode))
out = exec_tool(U_EMP, "coding_agent", "write_source_file",
                {"run_id": ridC, "filename": "solution.py", "content": "x = 1"})
check("valid write ok with hash+bytes",
      out.get("filename") == "solution.py" and out.get("bytes") == 5
      and len(str(out.get("sha256", ""))) == 16, str(out))
try:
    exec_tool(U_EMP, "coding_agent", "write_source_file",
              {"run_id": ridC, "filename": "solution.py",
               "content": "y" * (sbx.MAX_FILE_BYTES + 1)})
    ocode = 200
except HTTPException as e:
    ocode = e.status_code
check("oversize file rejected", ocode == 400, str(ocode))
check("only one file present", os.listdir(wsC) == ["solution.py"],
      str(os.listdir(wsC)))

for tool, extra in [("write_source_file", {"filename": "stolen.py",
                                            "content": "x"}),
                    ("create_workspace", {}),
                    ("run_tests", {}),
                    ("inspect_test_result", {}),
                    ("revise_code", {"content": "x"}),
                    ("finalize_verified_result", {})]:
    args = dict(extra)
    args["run_id"] = ridC
    try:
        exec_tool(U_OTH, "coding_agent", tool, args)
        code = 200
    except HTTPException as e:
        code = e.status_code
    check(f"cross-user denied {tool}", code == 404, str(code))
check("cross-user cannot see the workspace files",
      os.listdir(wsC) == ["solution.py"])

recW = agent_store.create("work_assistant", U_OTH["id"], "EMPLOYEE",
                          "not a coding run", "en")
try:
    exec_tool(U_OTH, "coding_agent", "write_source_file",
              {"run_id": recW["id"], "filename": "a.py", "content": "x"})
    code = 200
except HTTPException as e:
    code = e.status_code
check("coding tools reject non-coding runs", code == 403, str(code))
try:
    exec_tool(U_EMP, "coding_agent", "run_tests", {})
    code = 200
except HTTPException as e:
    code = e.status_code
check("missing run_id -> 400", code == 400, str(code))
try:
    exec_tool(U_EMP, "coding_agent", "run_tests",
              {"run_id": "a-doesnotexist"})
    code = 200
except HTTPException as e:
    code = e.status_code
check("unknown run -> 404", code == 404, str(code))

# guard: no result yet
for tool in ("inspect_test_result", "finalize_verified_result"):
    try:
        exec_tool(U_EMP, "coding_agent", tool, {"run_id": ridC})
        code, msg = 200, ""
    except HTTPException as e:
        code, msg = e.status_code, str(e.detail)
    check(f"{tool} before a run ->502", code == 502 and "no test result" in msg,
          f"{code} {msg}")
try:
    exec_tool(U_EMP, "coding_agent", "revise_code",
              {"run_id": ridC, "content": "x"})
    code, msg = 200, ""
except HTTPException as e:
    code, msg = e.status_code, str(e.detail)
check("revise before a run ->502", code == 502 and "no test result" in msg,
      f"{code} {msg}")
# guard: failed result can never be finalized
sbx.write_result(wsC, {"status": "failed", "summary": "1 failed, 2 passed",
                       "exit_code": 1, "iteration": 1,
                       "output": "FAILED test_solution.py::test_x - boom"})
try:
    exec_tool(U_EMP, "coding_agent", "finalize_verified_result",
              {"run_id": ridC})
    code, msg = 200, ""
except HTTPException as e:
    code, msg = e.status_code, str(e.detail)
check("finalize denied on failed tests",
      code == 502 and "not passing" in msg, f"{code} {msg}")
out = exec_tool(U_EMP, "coding_agent", "revise_code",
                {"run_id": ridC, "content": "x = 2"})
check("revise allowed after real failure",
      out.get("revised") == ["solution.py"], str(out))
sbx.write_result(wsC, {"status": "passed", "summary": "2 passed in 0.05s",
                       "exit_code": 0, "iteration": 2})
try:
    exec_tool(U_EMP, "coding_agent", "revise_code",
              {"run_id": ridC, "content": "x = 3"})
    code, msg = 200, ""
except HTTPException as e:
    code, msg = e.status_code, str(e.detail)
check("revise denied when passing", code == 502 and "nothing to revise" in msg,
      f"{code} {msg}")
fin = exec_tool(U_EMP, "coding_agent", "finalize_verified_result",
                {"run_id": ridC})
check("finalize on passing result -> VERIFIED",
      fin.get("verification") == "VERIFIED"
      and "passed" in str(fin.get("summary")), str(fin))
agent_store.save(ridC, state="COMPLETED")
try:
    exec_tool(U_EMP, "coding_agent", "run_tests",
              {"run_id": ridC, "iteration": 1})
    code = 200
except HTTPException as e:
    code = e.status_code
check("closed run -> tools refuse (409)", code == 409, str(code))
sbx.remove_workspace(ridC)

# ================================================= D. honest unavailability
print("== D. sandbox unavailable -> honest fail-closed result ==")
fakeD = FakeAI([])
install(fakeD)
os.environ["SOV_SANDBOX"] = "none"
try:
    r = api_run(T_EMP, {"goal": GOAL, "lang": "en"})
finally:
    os.environ.pop("SOV_SANDBOX", None)
    restore()
recD = r.json()
ridD = recD.get("id", "")
check("request handled", r.status_code == 200, str(r.status_code))
check("state FAILED", recD.get("state") == "FAILED", str(recD.get("state")))
check("exact honest message",
      recD.get("result") == sbx.MSG_UNAVAILABLE, str(recD.get("result")))
check("no model call happened (fail fast)", len(fakeD.calls) == 0,
      str(len(fakeD.calls)))
check("no workspace created",
      not os.path.exists(sbx.workspace_for(ridD)))
actsD = {row["action"] for row in run_actions(ridD)}
check("started+failed audited",
      "coding_task_started" in actsD and "coding_task_failed" in actsD,
      str(sorted(actsD)))
check("unavailable reason audited",
      any("reason=sandbox_unavailable" in (row.get("detail") or "")
          for row in run_actions(ridD)))
check("no code_generated on unavailable", "code_generated" not in actsD)
check("route still audited", any(row["action"] == "model_route"
                                 for row in run_actions(ridD)))

# ============================================ E. bounded iterations, no fake
print("== E. correction limit -> honest unverified failure ==")
fakeE = FakeAI([BUGGY_SOLUTION, GOOD_TESTS, BUGGY_SOLUTION, BUGGY_SOLUTION])
install(fakeE)
try:
    r = api_run(T_EMP, {"goal": GOAL, "lang": "en"})
finally:
    restore()
recE = r.json()
ridE = recE.get("id", "")
check("run handled", r.status_code == 200, str(r.status_code))
check("state FAILED not COMPLETED", recE.get("state") == "FAILED",
      str(recE.get("state")))
check("exact unverified sentence",
      (recE.get("result") or "").startswith(sbx.MSG_UNVERIFIED),
      str(recE.get("result"))[:200])
check("never labelled VERIFIED", not (recE.get("result") or "").startswith("VERIFIED"))
check("4 model calls (2 drafts + 2 revisions)", len(fakeE.calls) == 4,
      str(len(fakeE.calls)))
stepsE = recE.get("steps") or []
runsE = [s for s in stepsE if s["tool"] == "run_tests"]
check("exactly3 executions", len(runsE) == 3, str(len(runsE)))
check("all executions failed honestly",
      all(s.get("exec", {}).get("status") == "failed" for s in runsE),
      str([s.get("exec", {}).get("status") for s in runsE]))
actsE = {row["action"] for row in run_actions(ridE)}
check("no verified event", "coding_task_verified" not in actsE)
check("failed event with count",
      any("reason=max_iterations executions=3" in (row.get("detail") or "")
          and "last=failed" in (row.get("detail") or "")
          for row in run_actions(ridE)))
teE = [row for row in run_actions(ridE) if row["action"] == "tests_executed"]
check("3 executions audited with iterations",
      len(teE) == 3
      and all(any(f"iteration={i} status=" in row["detail"] for row in teE)
              for i in (1, 2, 3)),
      str([row["detail"] for row in teE]))
check("workspace cleaned up", not os.path.exists(sbx.workspace_for(ridE)))

# =================================================== F. sandbox guarantees
print("== F. sandbox isolation/timeout/output (real containers) ==")


def probe(run_id, name, content, timeout_env=None):
    sbx.create_workspace(run_id)
    p = sbx.workspace_for(run_id)
    sbx.write_test(p, name, content)
    if timeout_env:
        os.environ["SOV_SANDBOX_TIMEOUT"] = str(timeout_env)
    try:
        res = sbx.run_pytest(p)
    finally:
        os.environ.pop("SOV_SANDBOX_TIMEOUT", None)
        sbx.remove_workspace(run_id)
    return res


res = probe("a-pronet1", "test_isolation.py", ISOLATION_TEST)
check("network/db/socket/secrets/env all denied (passed in sandbox)",
      res["status"] == "passed",
      f"{res['status']} {res.get('summary')} {res.get('output', '')[-500:]}")
check("probe ran in sandbox image", res.get("image") == "sov-sandbox:latest")
check("probe network=none", res.get("network") == "none")

res = probe("a-protime1", "test_slow.py", SLEEP_TEST, timeout_env=4)
check("execution timeout enforced", res["status"] == "timeout",
      f"{res['status']} exit={res.get('exit_code')} dur={res.get('duration_s')}")
check("timeout within budget", res.get("duration_s", 99) < 25,
      str(res.get("duration_s")))
check("timeout message exact", sbx.failure_brief(res) == sbx.MSG_TIMEOUT)

res = probe("a-proout1", "test_output.py", OUTPUT_TEST)
check("output run completed", res["status"] == "passed", str(res["status"]))
check("output flagged truncated", res["output_truncated"] is True)
check("output size capped",
      len(res["output"]) <= sbx.OUTPUT_HEAD + sbx.OUTPUT_TAIL + 200,
      str(len(res["output"])))

leftover = subprocess.run(
    ["docker", "ps", "-a", "--filter", "name=sov-sbx-", "--format", "{{.Names}}"],
    capture_output=True, text=True).stdout
check("no leftover sandbox containers", leftover.strip() == "", repr(leftover))
labeled = subprocess.run(
    ["docker", "ps", "-a", "--filter", "label=sov.sandbox=coding",
     "--format", "{{.Names}}"], capture_output=True, text=True).stdout
check("no labeled containers survive", labeled.strip() == "", repr(labeled))

# ==================================================== G. audit data hygiene
print("== G. audit hygiene ==")
all_rows = audit_log.rows(10000)
blob = " ".join((row.get("detail") or "") for row in all_rows)
check("audit has no solution source",
      "def average_inspection_scores" not in blob
      and "DictReader" not in blob)
check("audit has no test source", "def test_average" not in blob)
check("audit has no prompts", "Write the complete contents" not in blob
      and "failed its test suite" not in blob)
check("audit has no passwords",
      "Employee@2026" not in blob and "Admin@2026" not in blob
      and "aud123" not in blob)
check("audit has no jwt tokens", "eyJ" not in blob)
coding_rows = [row for row in all_rows
               if str(row.get("action", "")).startswith("coding_")]
check("coding audit events exist", len(coding_rows) >= 6,
      str(len(coding_rows)))

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
