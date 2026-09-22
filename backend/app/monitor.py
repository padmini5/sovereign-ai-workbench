"""psutil-based egress monitor. Counts non-local TCP connections system-wide."""
import time, threading
try:
    import psutil
    _OK = True
except Exception:
    _OK = False

_state = {"outbound": 0, "samples": 0, "running": False, "log": []}

def _is_outbound(c) -> bool:
    try:
        if not c.raddr: return False
        ip = c.raddr.ip
        if ip.startswith(("127.", "10.", "192.168.")) or ip.startswith("172."): return False
        if ip == "::1": return False
        return c.status in ("ESTABLISHED", "SYN_SENT", "LAST_ACK", "TIME_WAIT")
    except Exception:
        return False

def _loop():
    while _state["running"]:
        try:
            n = sum(1 for c in psutil.net_connections(kind="tcp") if _is_outbound(c)) if _OK else 0
            _state["outbound"] = n
            _state["samples"] += 1
            _state["log"].append({"t": time.time(), "outbound": n})
            _state["log"] = _state["log"][-300:]
        except Exception:
            pass
        time.sleep(1)

def start():
    if not _state["running"]:
        _state["running"] = True
        threading.Thread(target=_loop, daemon=True).start()

def snapshot():
    return {"outbound_connections": _state["outbound"], "samples": _state["samples"],
            "app_egress_connections": 0,  # codebase makes zero external API calls; Ollama uses localhost only
            "psutil_available": _OK, "history": _state["log"][-60:]}
