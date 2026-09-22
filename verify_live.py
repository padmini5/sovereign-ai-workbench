"""Live-server smoke test: real uvicorn on 0.0.0.0 + HTTP (not TestClient).
Started/stopped by the caller; exits 0 only if /health + login + query pass."""
import json, subprocess, sys, time, os
import urllib.request

PORT = "8123"
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "backend.app.main:app",
     "--host", "0.0.0.0", "--port", PORT],
    cwd=os.path.dirname(os.path.abspath(__file__)),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    def get(path):
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=10) as r:
            return r.status, json.loads(r.read().decode())

    def post(path, body, token=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bearer " + token} if token else {})})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())

    ok = False
    for _ in range(30):
        try:
            st, h = get("/health")
            assert st == 200 and h["ok"] is True
            ok = True
            print("live /health:", h)
            break
        except Exception:
            time.sleep(1)
    assert ok, "server did not come up"
    st, lg = post("/api/login", {"username": "manager1", "password": "mgr123"})
    assert st == 200
    st, qr = post("/api/query", {"query": "corroded pipe NDT"}, lg["token"])
    assert st == 200 and len(qr["chunks"]) > 0
    print("live login + query ok:", [(x["chunk_id"], x["clearance_tier"]) for x in qr["chunks"]])
    print("LIVE SMOKE PASSED")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
