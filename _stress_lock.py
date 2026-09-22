"""Verify the 'database is locked' fix: fire many PARALLEL authenticated
requests (as a browser does on page load) and assert ZERO 500s.

Before the fix, init_db() ran write transactions on every read call, so
concurrent requests deadlocked on SQLite's write lock and returned 500.
"""
import json
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

B = "http://127.0.0.1:8124"

ROLES = [
    ("employee@company.com", "Employee@2026#R4m!", "EMPLOYEE"),
    ("manager@company.com", "Manager@2026#K7p!", "MANAGER"),
    ("admin@company.com", "Admin@2026#S9x!", "ADMIN"),
]

CALLS = [
    "/api/v1/auth/me",
    "/api/v1/employees/me",
    "/api/v1/work/schedule",
    "/api/v1/work/attendance",
    "/api/v1/work/attendance/summary",
    "/api/v1/work/tasks",
    "/api/v1/work/me",
    "/api/v1/work/activity",
    "/api/v1/work/worksheets",
    "/api/v1/work/worksheets/assigned",
    "/api/v1/analytics/criteria",
    "/api/v1/docs",
    "/api/v1/reports",
    "/api/v1/employees",
    "/api/v1/devices/status",
]


def req(path, token):
    r = urllib.request.Request(B + path, method="GET",
                               headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return path, resp.status, ""
    except urllib.error.HTTPError as e:
        return path, e.code, e.read()[:120].decode("utf-8", "replace")
    except Exception as e:
        return path, 0, str(e)


def login(u, p):
    data = json.dumps({"username": u, "password": p}).encode()
    r = urllib.request.Request(B + "/api/v1/auth/login", data=data, method="POST",
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def main():
    # Wait for server
    for _ in range(40):
        try:
            urllib.request.urlopen(B + "/health", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    else:
        print("SERVER DID NOT START"); return 1

    total = errs500 = timeouts = ok = 0
    with ThreadPoolExecutor(max_workers=16) as pool:
        futs = []
        for u, p, role in ROLES:
            tok = login(u, p)
            # Each role fires all calls at once, THREE times (browser reload effect)
            for _ in range(3):
                for c in CALLS:
                    futs.append((role, pool.submit(req, c, tok)))
        for role, f in futs:
            path, status, body = f.result()
            total += 1
            if status == 500:
                errs500 += 1
                print(f"  500 {role} {path} {body[:80]}")
            elif status == 0:
                timeouts += 1
                print(f"  TIMEOUT/ERR {role} {path} {body[:80]}")
            elif 200 <= status < 300:
                ok += 1

    print(f"\n=== CONCURRENCY STRESS: {total} parallel requests ===")
    print(f"  2xx OK      : {ok}")
    print(f"  500 (lock)  : {errs500}")
    print(f"  timeout/err : {timeouts}")
    print(f"  other       : {total - ok - errs500 - timeouts}")
    if errs500 == 0 and timeouts == 0:
        print("RESULT: PASS — no 'database is locked' 500s under concurrency")
        return 0
    print("RESULT: FAIL — lock/contention errors remain")
    return 1


if __name__ == "__main__":
    sys.exit(main())
