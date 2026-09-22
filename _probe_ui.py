"""Probe EVERY endpoint each frontend page calls, per role, against a LIVE
server. Reports status + payload shape so 'Not Found' causes are traced to
the exact route/permission/data gap. Read-only except where noted."""
import json
import sys
import urllib.request
import urllib.error

B = "http://127.0.0.1:8123"

ROLES = [
    ("employee@company.com", "Employee@2026#R4m!", "EMPLOYEE"),
    ("manager@company.com", "Manager@2026#K7p!", "MANAGER"),
    ("admin@company.com", "Admin@2026#S9x!", "ADMIN"),
    ("operator@company.com", "Operator@2026#T8q!", "OPERATOR"),
    ("reviewer@company.com", "Reviewer@2026#V6n!", "REVIEWER"),
]

# (label, method, path, body) — mirrors frontend page calls
PAGE_CALLS = {
    "Profile": [
        ("GET", "/api/v1/employees/me", None),
        ("GET", "/api/v1/employees/me/photo", None),
    ],
    "Employees": [
        ("GET", "/api/v1/employees", None),
        ("GET", "/api/v1/admin/users", None),
    ],
    "Schedule": [
        ("GET", "/api/v1/work/schedule", None),
    ],
    "Attendance": [
        ("GET", "/api/v1/work/attendance", None),
        ("GET", "/api/v1/work/attendance/summary", None),
        ("GET", "/api/v1/devices/status", None),
    ],
    "Work/Tasks (portal)": [
        ("GET", "/api/v1/work/tasks", None),
        ("GET", "/api/v1/work/me", None),
        ("GET", "/api/v1/work/activity", None),
    ],
    "Worksheets": [
        ("GET", "/api/v1/work/worksheets", None),
        ("GET", "/api/v1/work/worksheets/assigned", None),
    ],
    "Performance": [
        ("GET", "/api/v1/analytics/criteria", None),
        ("GET", "/api/v1/analytics/overview", None),
        ("GET", "/api/v1/analytics/trends?bucket=week", None),
    ],
    "Documents": [
        ("GET", "/api/v1/docs", None),
        ("GET", "/api/v1/docs?kind=csv", None),
        ("GET", "/api/v1/docs?kind=xlsx", None),
    ],
    "Spreadsheets": [
        ("GET", "/api/v1/analytics/financial", None),
    ],
    "Reports": [
        ("GET", "/api/v1/reports", None),
    ],
    "Workspace extras": [
        ("GET", "/api/v1/identity/status", None),
        ("GET", "/api/v1/voice/status", None),
        ("GET", "/api/v1/ai/status", None),
        ("GET", "/api/v1/i18n/languages", None),
        ("GET", "/api/v1/agents", None),
    ],
}


def call(method, path, body, token):
    url = B + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, raw[:80]
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:120]
    except Exception as e:
        return 0, str(e)


def emptyish(label, st, j):
    """Heuristic: does this page render NOTHING for this role?"""
    if st != 200:
        return True
    if isinstance(j, dict):
        for k in ("employees", "tasks", "rows", "worksheets", "assignments",
                  "reports", "documents", "items", "points"):
            if k in j:
                v = j[k]
                if not isinstance(v, (list, dict, str)):
                    return False  # scalar (e.g. int count) — not an "empty list"
                return len(v) == 0
    return False


summary = {}
for user, pw, role in ROLES:
    st, j = call("POST", "/api/v1/auth/login", {"username": user, "password": pw}, "")
    if st != 200:
        print(f"LOGIN FAIL {role}: {st} {j}")
        continue
    tok = j["access_token"]
    print(f"\n================ {role} ({user}) ================")
    summary[role] = []
    for page, calls in PAGE_CALLS.items():
        parts = []
        for method, path, body in calls:
            st, j = call(method, path, body, tok)
            flag = ""
            if st == 404:
                flag = "  <== 404 NOT FOUND"
            elif st == 403:
                flag = "  (403 forbidden)"
            elif emptyish(path, st, j):
                flag = "  <== EMPTY"
            detail = ""
            if st >= 400:
                detail = " " + str(j)[:110]
            parts.append(f"{path}: {st}{detail}{flag}")
            summary[role].append((page, path, st, flag))
        print(f"  [{page}]")
        for p in parts:
            print(f"    {p}")

print("\n================ 404 / EMPTY MATRIX ================")
for role, rows in summary.items():
    probs = [(pg, p, st, f) for pg, p, st, f in rows if st == 404 or "EMPTY" in f]
    if probs:
        print(f"  {role}:")
        for pg, p, st, f in probs:
            print(f"    {pg:24} {p:45} -> {st} {f}")
