"""Step 20 tests: voice I/O (run: python tests/test_step20.py).

Mock STT/TTS only — no microphone, no external APIs. Isolated temp DBs.
"""
import base64
import io
import math
import os
import struct
import sys
import tempfile
import wave

_tmp = tempfile.mkdtemp(prefix="sov_step20_")
os.environ["SOV_AUTH_DB"] = os.path.join(_tmp, "auth.db")
os.environ["SOV_AUTH_AUDIT_DB"] = os.path.join(_tmp, "auth_audit.db")
os.environ["SOV_CHAT_DB"] = os.path.join(_tmp, "chat.db")
os.environ["SOV_DOCS_DB"] = os.path.join(_tmp, "docs.db")
os.environ["SOV_VECTORS_DB"] = os.path.join(_tmp, "vectors.db")
os.environ["SOV_UPLOADS_DIR"] = os.path.join(_tmp, "uploads")
os.environ["SOV_AI_PROVIDER"] = "mock"
os.environ["SOV_EMBED_PROVIDER"] = "hash"
os.environ["SOV_TRANSLATE_PROVIDER"] = "mock"
os.environ["SOV_STT_PROVIDER"] = "mock"
os.environ["SOV_TTS_PROVIDER"] = "mock"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app import audit_log  # noqa: E402

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


def mk_wav(secs=0.2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        n = int(8000 * secs)
        w.writeframes(b"".join(struct.pack("<h", int(5000 * math.sin(2 * math.pi * 440 * i / 8000)))
                               for i in range(n)))
    return buf.getvalue()


def login(u, p):
    r = c.post("/api/v1/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


WAV = mk_wav()
AUDIO = lambda tok, name="v.wav", data=WAV, extra=None: c.post(
    "/api/v1/voice/chat", files={"f": (name, data, "audio/wav")},
    data={"lang": "en", **(extra or {})}, headers=H(tok))

T_MGR = login("manager", "Mgr123!")
T_ADM = login("admin", "Admin123!")

print("== 1. voice input + transcription ==")
r = c.post("/api/v1/voice/stt", files={"f": ("v.wav", WAV, "audio/wav")},
           data={"lang": "hi"}, headers=H(T_MGR)).json()
check("stt hi transcript", "mock transcript in hi" in r["text"] and r["lang"] == "hi"
      and r["provider"] == "mock-stt", str(r)[:150])

print("== 2. invalid audio / auth ==")
check("empty -> 400",
      c.post("/api/v1/voice/stt", files={"f": ("e.wav", b"", "audio/wav")},
             data={"lang": "en"}, headers=H(T_MGR)).status_code == 400)
check("text-as-audio -> 415",
      c.post("/api/v1/voice/stt", files={"f": ("x.wav", b"not audio" * 10, "audio/wav")},
             data={"lang": "en"}, headers=H(T_MGR)).status_code == 415)
check("bad lang -> 422",
      c.post("/api/v1/voice/stt", files={"f": ("v.wav", WAV, "audio/wav")},
             data={"lang": "xx"}, headers=H(T_MGR)).status_code == 422)
check("no token -> 401/403",
      c.post("/api/v1/voice/stt", files={"f": ("v.wav", WAV, "audio/wav")},
             data={"lang": "en"}).status_code in (401, 403))
os.environ["SOV_MAX_AUDIO_MB"] = "0"
check("oversize -> 413",
      c.post("/api/v1/voice/stt", files={"f": ("v.wav", WAV, "audio/wav")},
             data={"lang": "en"}, headers=H(T_MGR)).status_code == 413)
del os.environ["SOV_MAX_AUDIO_MB"]

print("== 3. text-to-speech ==")
r = c.post("/api/v1/voice/tts", json={"text": "hello world", "lang": "hi"}, headers=H(T_MGR))
check("tts wav bytes", r.status_code == 200 and r.headers["content-type"] == "audio/wav"
      and r.content[:4] == b"RIFF", f"{r.status_code} {r.headers.get('content-type')}")
check("tts empty -> 422",
      c.post("/api/v1/voice/tts", json={"text": " ", "lang": "en"}, headers=H(T_MGR)).status_code == 422)
check("tts long -> 422",
      c.post("/api/v1/voice/tts", json={"text": "x" * 2001, "lang": "en"},
             headers=H(T_MGR)).status_code == 422)
check("tts bad lang -> 422",
      c.post("/api/v1/voice/tts", json={"text": "x", "lang": "xx"}, headers=H(T_MGR)).status_code == 422)

print("== 4. voice chat end-to-end ==")
r = AUDIO(T_MGR).json()
check("transcript + answer + audio",
      "mock transcript" in r["transcript"] and r["message"]["content"].startswith("[MANAGER")
      and base64.b64decode(r["audio_base64"])[:4] == b"RIFF" and r["audio_mime"] == "audio/wav",
      str(r)[:250])
check("chat fields parity", {"conversation_id", "sources", "lang", "translated"} <= set(r))

print("== 5. RAG via voice + citations ==")
did = c.post("/api/v1/docs/upload",
             files={"f": ("voice-rag.txt", b"documents say cobalt violin shipment manifest delta", "text/plain")},
             headers={"Authorization": f"Bearer {T_MGR}"}).json()["id"]
c.post(f"/api/v1/docs/{did}/analyze", headers=H(T_MGR))
r = AUDIO(T_MGR, extra={"document_ids": f'["{did}"]', "mode": "my_docs", "lang": "en"}).json()
check("voice rag sources", r.get("sources") and r["sources"][0]["doc_id"] == did, str(r)[:250])
check("citations in voice answer",
      "Sources:" in r["message"]["content"] and "voice-rag.txt" in r["message"]["content"])

print("== 6. RBAC / document permissions via voice ==")
r = c.post("/api/v1/admin/users", json={"username": "tmp_v", "password": "Tmp12345", "role": "OPERATOR"},
           headers=H(T_ADM))
T_TMP = login("tmp_v", "Tmp12345")
check("stranger voice doc -> 403",
      AUDIO(T_TMP, extra={"document_ids": f'["{did}"]'}).status_code == 403)
r = AUDIO(T_TMP, extra={"mode": "my_docs"}).json()
check("stranger my_docs -> no leak",
      r.get("sources") == [] and "cobalt" not in r["message"]["content"].lower())
check("bad token -> 401/403",
      c.post("/api/v1/voice/chat", files={"f": ("v.wav", WAV, "audio/wav")},
             data={"lang": "en"}, headers=H("junk")).status_code in (401, 403))

print("== 7. multilingual voice flow ==")
r = AUDIO(T_MGR, extra={"lang": "hi"}).json()
check("hi voice: translated answer + audio",
      r["lang"] == "hi" and "[hi]" in r["message"]["content"] and r["audio_base64"],
      r["message"]["content"][:200])
check("hi transcript tagged", "hi" in r["transcript"])

print("== 8. provider failures ==")
os.environ["SOV_STT_PROVIDER"] = "unavailable"
check("stt down -> 502",
      c.post("/api/v1/voice/stt", files={"f": ("v.wav", WAV, "audio/wav")},
             data={"lang": "en"}, headers=H(T_MGR)).status_code == 502)
check("voice chat stt down -> 502", AUDIO(T_MGR).status_code == 502)
os.environ["SOV_STT_PROVIDER"] = "mock"
os.environ["SOV_TTS_PROVIDER"] = "unavailable"
check("tts down -> 502",
      c.post("/api/v1/voice/tts", json={"text": "x", "lang": "en"},
             headers=H(T_MGR)).status_code == 502)
r = AUDIO(T_MGR).json()
check("voice chat survives tts outage (answer stands, audio omitted)",
      r["audio_base64"] is None and r["audio_error"] and r["message"]["content"],
      str(r)[:200])
os.environ["SOV_TTS_PROVIDER"] = "mock"

print("== 9. status + audit ==")
st = c.get("/api/v1/voice/status", headers=H(T_MGR)).json()
check("status block", st["stt"]["available"] is True and st["tts"]["available"] is True
      and len(st["languages"]) == 12 and st["remote_configured"] == {"stt": False, "tts": False},
      str(st)[:200])
rows = audit_log.rows(1000)
actions = {r["action"] for r in rows}
for a in ["voice_stt", "voice_tts", "voice_chat"]:
    check(f"{a} audited", a in actions)
blob = " ".join((r.get("detail") or "") for r in rows)
check("no audio/transcript bulk in audit", "cobalt violin" not in blob)

uid = [u["id"] for u in c.get("/api/v1/admin/users", headers=H(T_ADM)).json()["users"]
       if u["username"] == "tmp_v"][0]
c.delete(f"/api/v1/admin/users/{uid}", headers=H(T_ADM))

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
