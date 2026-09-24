"""Step 3: secure sandbox for coding-agent output.

Generated code NEVER executes on the Windows host (no host Python, no
PowerShell/cmd, no host subprocess running generated code). Each execution is
one ephemeral Docker container:

  * image: minimal python+pytest only (./sandbox; no application code, no
    secrets, no database drivers, no docker client)
  * network: NONE - no external/cloud/DB/internal reachability at all
  * filesystem: read-only root fs; the workspace streams in over stdin as a
    tar archive into a size-bounded tmpfs (RAM). Nothing from the host is
    mounted and nothing survives the container (--rm).
  * identity: non-root user, all capabilities dropped, no-new-privileges
  * limits: memory/CPU/pids/RLIMIT_FSIZE, inner GNU timeout, outer
    `docker rm -f` backstop, capped stdout/stderr capture
  * environment: explicit allow-list only - application secrets cannot leak.

Fail-closed: if the docker CLI, the sandbox image, or any execution step is
missing or fails, callers get SandboxUnavailable / SandboxError so the
workflow can report an honest failure. Host execution is never substituted
and no result is ever fabricated.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import threading
import time
import uuid

from . import config as cfg

# ---- honest user-facing messages (exact wording, never faked) ----
MSG_UNAVAILABLE = "Unable to execute code in the secure sandbox."
MSG_TIMEOUT = "Code execution timed out."
MSG_UNVERIFIED = "Unable to verify the generated code within the allowed attempts."

# ---- hard limits (host side; the container enforces its own bounds) ----
MAX_FILES = 20
MAX_FILE_BYTES = 64 * 1024
MAX_WORKSPACE_BYTES = 256 * 1024
OUTPUT_HEAD = 48 * 1024
OUTPUT_TAIL = 16 * 1024
_TMPFS_WORKSPACE = "16m"   # RAM bound: generated files cannot touch host disk
_TMPFS_TMP = "32m"
_MEMORY = "256m"
_CPUS = "0.5"
_PIDS = "64"
_FSIZE = 524288            # RLIMIT_FSIZE (bytes) inside the container
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
_RUNID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
_SUMMARY_RE = re.compile(
    r"\b\d+ (?:passed|failed|error|skipped|deselected|xfailed|xpassed)\b")
_RESULT_FILE = ".result.json"   # server-owned state; never a client name

DEFAULT_IMAGE = "sov-sandbox:latest"
DEFAULT_TIMEOUT_S = 30


class SandboxUnavailable(Exception):
    """No safe execution environment - report MSG_UNAVAILABLE."""


class SandboxError(Exception):
    """Execution attempt failed (infrastructure / unexpected)."""


class SandboxValueError(Exception):
    """Invalid client-controlled input (name/content) -> HTTP 400."""


class SandboxSecurityError(Exception):
    """Workspace violated its bounds - fail closed, deny execution."""


# ---------------------------------------------------------------- config

def image() -> str:
    return os.getenv("SOV_SANDBOX_IMAGE", "").strip() or DEFAULT_IMAGE


def timeout_s() -> int:
    try:
        v = int(os.getenv("SOV_SANDBOX_TIMEOUT", "") or DEFAULT_TIMEOUT_S)
    except ValueError:
        v = DEFAULT_TIMEOUT_S
    return max(5, min(v, 300))


def max_iterations() -> int:
    try:
        v = int(os.getenv("SOV_CODING_MAX_ITERATIONS", "") or "3")
    except ValueError:
        v = 3
    return max(1, min(v, 5))


def disabled() -> bool:
    return os.getenv("SOV_SANDBOX", "auto").strip().lower() in (
        "none", "off", "0", "disabled")


# ------------------------------------------------------------- workspace

def workspace_root() -> str:
    return cfg.data_path("coding")


def _validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not _RUNID_RE.match(run_id):
        raise SandboxValueError("invalid run id")
    return run_id


def workspace_for(run_id: str) -> str:
    """Logical path of the run's workspace (validated, containment-checked)."""
    rid = _validate_run_id(run_id)
    root = os.path.realpath(workspace_root())
    ws = os.path.join(root, "cw-" + rid)
    real = os.path.realpath(ws)
    try:
        ok = os.path.commonpath([root, real]) == root and os.path.dirname(real) == root
    except ValueError:
        ok = False
    if not ok:
        raise SandboxSecurityError("workspace path escapes its root")
    return real


def create_workspace(run_id: str) -> dict:
    rid = _validate_run_id(run_id)
    root = os.path.realpath(workspace_root())
    os.makedirs(root, exist_ok=True)
    ws = os.path.join(root, "cw-" + rid)
    if os.path.exists(ws):
        raise SandboxValueError("workspace already exists")
    os.makedirs(ws)
    try:
        os.chmod(ws, 0o700)
    except OSError:
        pass
    return {"workspace": "cw-" + rid, "files_cap": MAX_FILES,
            "file_bytes_cap": MAX_FILE_BYTES,
            "workspace_bytes_cap": MAX_WORKSPACE_BYTES}


def remove_workspace(run_id: str) -> None:
    """Best-effort cleanup after the run (files are embedded in the run record)."""
    try:
        rid = _validate_run_id(run_id)
    except SandboxValueError:
        return
    ws = os.path.join(os.path.realpath(workspace_root()), "cw-" + rid)
    shutil.rmtree(ws, ignore_errors=True)


def safe_filename(name: object) -> str:
    """Flat names only: no separators, no drive/UNC forms, no dotfiles, no
    traversal - path escape is rejected by construction, containment is then
    re-checked with realpath/commonpath as a second layer."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise SandboxValueError(f"unsafe file name: {str(name)[:48]!r}")
    if any(ch in name for ch in "/\\:"):
        raise SandboxValueError(f"unsafe file name: {name[:48]!r}")
    return name


def _require_ws(ws: str) -> str:
    root = os.path.realpath(workspace_root())
    try:
        real = os.path.realpath(ws)
        ok = os.path.commonpath([root, real]) == root
    except ValueError:
        ok = False
    if not ok:
        raise SandboxSecurityError("workspace path escapes its root")
    if not os.path.isdir(real):
        raise SandboxError("workspace not created")
    return real


def _existing_files(ws_real: str) -> list[str]:
    out = []
    for f in os.listdir(ws_real):
        if f.startswith(".") or not _NAME_RE.match(f):
            continue
        if os.path.isfile(os.path.join(ws_real, f)):
            out.append(f)
    return out


def _write(ws: str, name: object, content: object, prefix: str | None = None) -> dict:
    real = _require_ws(ws)
    fname = safe_filename(name)
    if not fname.endswith(".py"):
        raise SandboxValueError("file name must end with .py")
    if prefix and not fname.startswith(prefix):
        raise SandboxValueError(f"file name must start with {prefix}")
    if not isinstance(content, str):
        raise SandboxValueError("file content must be text")
    if "\x00" in content:
        raise SandboxValueError("file content contains NUL")
    try:
        data = content.encode("utf-8")
    except UnicodeEncodeError:
        raise SandboxValueError("file content is not valid UTF-8")
    if len(data) > MAX_FILE_BYTES:
        raise SandboxValueError(f"file exceeds {MAX_FILE_BYTES} bytes")
    existing = _existing_files(real)
    target = os.path.join(real, fname)
    had = fname in existing
    if not had and len(existing) >= MAX_FILES:
        raise SandboxValueError(f"workspace file limit ({MAX_FILES}) reached")
    total = 0
    for f in existing:
        if f == fname:
            continue
        total += os.path.getsize(os.path.join(real, f))
    if total + len(data) > MAX_WORKSPACE_BYTES:
        raise SandboxValueError(
            f"workspace size limit ({MAX_WORKSPACE_BYTES} bytes) reached")
    try:
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
    except OSError as e:
        raise SandboxValueError(f"cannot write file: {e.strerror or e}")
    return {"filename": fname, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()[:16]}


def write_source(ws: str, name: object, content: object) -> dict:
    return _write(ws, name, content)


def write_test(ws: str, name: object, content: object) -> dict:
    return _write(ws, name, content, prefix="test_")


def write_result(ws: str, res: dict) -> None:
    real = _require_ws(ws)
    try:
        payload = json.dumps(res, ensure_ascii=True)
        with open(os.path.join(real, _RESULT_FILE), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
    except (OSError, TypeError, ValueError) as e:
        raise SandboxError(f"cannot record test result: {e}")


def read_result(ws: str) -> dict | None:
    real = _require_ws(ws)
    path = os.path.join(real, _RESULT_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


# ----------------------------------------------------------- availability

_available = {"ts": 0.0, "ok": False}


def check_available(force: bool = False) -> None:
    """Fail-closed preflight: docker CLI + sandbox image must exist. Success
    is cached briefly; failures are never cached (so a freshly built image
    is picked up immediately)."""
    if disabled():
        raise SandboxUnavailable(MSG_UNAVAILABLE)
    now = time.time()
    if not force and _available["ok"] and now - _available["ts"] < 30.0:
        return
    if not shutil.which("docker"):
        raise SandboxUnavailable(MSG_UNAVAILABLE)
    try:
        rc = subprocess.run(["docker", "image", "inspect", image()],
                            timeout=20, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL).returncode
    except Exception:
        rc = 1
    if rc != 0:
        raise SandboxUnavailable(MSG_UNAVAILABLE)
    _available["ts"] = now
    _available["ok"] = True


# ------------------------------------------------------------- execution

def _tar_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for name in sorted(files):
            data = files[name]
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _parse_summary(output: str, rc: int | None) -> str:
    for ln in reversed(output.splitlines()):
        ln = ln.strip(" =\t")
        if len(ln) <= 200 and _SUMMARY_RE.search(ln):
            return ln
    if rc == 5:
        return "no tests collected"
    if rc is None:
        return ""
    return f"pytest exit {rc}"


def _classify(timed_out: bool, rc: int | None, elapsed: float,
              budget: int, pipe_ok: bool) -> str:
    if timed_out or rc == 124:
        return "timeout"
    if not pipe_ok:
        return "error"
    if rc == 137:   # KILL: inner timeout expiry or a memory/pids limit
        return "timeout" if elapsed >= budget * 0.8 else "error"
    if rc == 0:
        return "passed"
    if rc in (1, 5):
        return "failed"
    return "error"


def failure_brief(res: dict) -> str:
    """Concise, honest failure info (safe to feed back to the coding model
    and to store in audit): summary + failing test ids + assertion lines."""
    status = str(res.get("status") or "")
    if status == "timeout":
        return MSG_TIMEOUT
    summary = str(res.get("summary") or "")
    output = str(res.get("output") or "")
    heads = [ln.strip() for ln in output.splitlines()
             if ln.startswith("FAILED ") or ln.startswith("ERROR ")][:8]
    elines = [ln.strip() for ln in output.splitlines()
              if ln.lstrip().startswith("E ")][:12]
    parts = [p for p in [summary] + heads + elines if p]
    if not parts:
        parts = [f"status={status} exit={res.get('exit_code')}",
                 output[-400:]]
    return "\n".join(p for p in parts if p)[:1500]


def run_pytest(ws: str) -> dict:
    """Stream the workspace into ONE ephemeral, network-isolated container
    and run pytest there. Returns a machine-readable result; never raises
    for test failures (those are honest `failed` statuses)."""
    check_available()
    real = _require_ws(ws)
    files: dict[str, bytes] = {}
    for fname in sorted(os.listdir(real)):
        if fname.startswith(".") or not _NAME_RE.match(fname):
            continue          # server state files never enter the sandbox
        path = os.path.join(real, fname)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as fh:
            files[fname] = fh.read()
    if (len(files) > MAX_FILES
            or sum(len(b) for b in files.values()) > MAX_WORKSPACE_BYTES):
        raise SandboxSecurityError("workspace exceeds its limits")
    archive = _tar_bytes(files)
    budget = timeout_s()
    host_deadline = float(budget) + 15.0
    cname = "sov-sbx-" + uuid.uuid4().hex[:12]
    inner = (f"tar -xf - -C /workspace && exec timeout -s KILL {budget} "
             "python -m pytest -q -s --tb=short -p no:cacheprovider /workspace")
    cmd = ["docker", "run", "--rm", "-i", "--name", cname,
           "--network", "none",
           "--read-only",
           "--tmpfs", f"/workspace:rw,noexec,nosuid,nodev,size={_TMPFS_WORKSPACE},mode=1777",
           "--tmpfs", f"/tmp:rw,nosuid,nodev,size={_TMPFS_TMP},mode=1777",
           "--memory", _MEMORY, "--memory-swap", _MEMORY,
           "--cpus", _CPUS, "--pids-limit", str(_PIDS),
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
           "--ulimit", f"fsize={_FSIZE}:{_FSIZE}",
           "--ulimit", "nofile=256:256",
           "--label", "sov.sandbox=coding",
           "-w", "/workspace",
           "-e", "HOME=/tmp", "-e", "PYTHONPATH=/workspace",
           "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTHONUNBUFFERED=1",
           image(), "sh", "-c", inner]
    t0 = time.time()
    try:
        res = _run_container(cmd, cname, archive, budget, host_deadline, t0)
    except (SandboxUnavailable, SandboxError, SandboxSecurityError,
            SandboxValueError):
        raise
    except Exception as e:
        raise SandboxError(f"docker execution failed: {str(e)[:120]}")
    return res


def _rm_f(name: str) -> None:
    try:
        subprocess.run(["docker", "rm", "-f", name], timeout=20,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _run_container(cmd: list[str], cname: str, archive: bytes,
                   budget: int, host_deadline: float, t0: float) -> dict:
    head = bytearray()
    tail = bytearray()
    total = 0
    lock = threading.Lock()

    def _drain(stream) -> None:
        nonlocal total
        while True:
            try:
                chunk = stream.read(8192)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            with lock:
                total += len(chunk)
                room = OUTPUT_HEAD - len(head)
                if room > 0:
                    head.extend(chunk[:room])
                    chunk = chunk[room:]
                if chunk:
                    tail.extend(chunk)
                    if len(tail) > OUTPUT_TAIL:
                        del tail[: len(tail) - OUTPUT_TAIL]

    timed_out = False
    pipe_ok = True
    state = {"proc": None}

    def _abort() -> None:
        nonlocal timed_out
        timed_out = True
        _rm_f(cname)                       # kills the container on the daemon
        proc = state["proc"]
        if proc is not None:
            try:
                proc.kill()                # kills docker.exe's attach process
            except OSError:
                pass
            try:
                proc.wait(timeout=15)
            except Exception:
                pass

    def _remaining() -> float:
        return max(0.5, host_deadline - (time.time() - t0))

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, bufsize=0)
    state["proc"] = proc
    reader = threading.Thread(target=_drain, args=(proc.stdout,), daemon=True)
    reader.start()

    def _writer() -> None:
        nonlocal pipe_ok
        try:
            proc.stdin.write(archive)
            proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pipe_ok = False
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    writer = threading.Thread(target=_writer, daemon=True)
    writer.start()
    writer.join(_remaining())
    if writer.is_alive():
        _abort()
        writer.join(5)
    if not timed_out:
        try:
            proc.wait(timeout=_remaining())
        except subprocess.TimeoutExpired:
            _abort()
    reader.join(15)
    if timed_out or proc.poll() is None:
        if proc.poll() is None and not timed_out:
            _abort()
    try:
        proc.stdout.close()
    except OSError:
        pass
    if not timed_out:
        # normal exit: `--rm` already removed the container
        pass
    elapsed = round(time.time() - t0, 2)
    rc = proc.returncode
    with lock:
        body = bytes(head) + bytes(tail)
        dropped = total - (len(head) + len(tail))
        read_total = total
    text = body.decode("utf-8", errors="replace")
    if dropped > 0:
        # keep a contiguous head; the marker sits where bytes were dropped
        with lock:
            head_text = bytes(head).decode("utf-8", errors="replace")
            tail_text = bytes(tail).decode("utf-8", errors="replace")
        text = (head_text + f"\n... [{dropped} bytes truncated] ...\n"
                + tail_text)
    status = _classify(timed_out, rc, elapsed, budget, pipe_ok)
    summary = _parse_summary(text, rc)
    if not pipe_ok:
        summary = "sandbox workspace transfer failed"
    return {"status": status, "exit_code": rc, "summary": summary,
            "output": text, "output_truncated": dropped > 0,
            "duration_s": elapsed, "timeout_s": budget, "image": image(),
            "network": "none", "container": cname, "read_bytes": read_total}
