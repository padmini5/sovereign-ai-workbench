"""Local voice engine tests (run: python tests/test_voice_local.py).

Real on-prem engines only (vosk model + OS TTS voices) — no mocks, no
microphone, no external APIs. Skips cleanly when engines are absent so CI
without them still passes; test_step20.py covers the mock contract.
"""
import io
import os
import sys
import tempfile
import wave

_tmp = tempfile.mkdtemp(prefix="sov_voicelocal_")
os.environ["SOV_AUTH_DB"] = os.path.join(_tmp, "auth.db")
os.environ["SOV_AUTH_AUDIT_DB"] = os.path.join(_tmp, "auth_audit.db")
os.environ["SOV_CHAT_DB"] = os.path.join(_tmp, "chat.db")
os.environ["SOV_DOCS_DB"] = os.path.join(_tmp, "docs.db")
os.environ["SOV_VECTORS_DB"] = os.path.join(_tmp, "vectors.db")
os.environ["SOV_UPLOADS_DIR"] = os.path.join(_tmp, "uploads")
os.environ["SOV_AI_PROVIDER"] = "mock"
os.environ["SOV_EMBED_PROVIDER"] = "hash"
os.environ["SOV_TRANSLATE_PROVIDER"] = "mock"
os.environ["SOV_STT_PROVIDER"] = "local"
os.environ["SOV_TTS_PROVIDER"] = "local"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import voice as V  # noqa: E402

passed = failed = 0
def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")


stt_ok = V.get_stt_provider().available()
tts_ok = V.get_tts_provider().available()
print(f"engines: stt(local)={stt_ok} tts(local)={tts_ok}")
if not (stt_ok and tts_ok):
    print("SKIP: on-prem voice engines absent (mock contract lives in test_step20.py)")
    print("\nRESULT: 0 passed, 0 failed (skipped)")
    sys.exit(0)

print("== 1. real TTS produces speech audio ==")
out = V.get_tts_provider().synthesize("hello work status", "en")
check("wav envelope", out["audio"][:4] == b"RIFF" and len(out["audio"]) > 20000,
      str(len(out["audio"])))
check("engine labelled local", out["engine"] == "local-sapi", out["engine"])
with wave.open(io.BytesIO(out["audio"]), "rb") as w:
    frames = w.readframes(w.getnframes())
import struct
peak = max(abs(v) for v in struct.unpack("<%dh" % (len(frames) // 2), frames))
check("non-silent speech", peak > 1000, str(peak))

print("== 2. real STT transcribes that speech ==")
res = V.get_stt_provider().transcribe(out["audio"], "wav", "en")
check("words recovered", all(w in res["text"].lower() for w in ("hello", "work", "status")),
      repr(res["text"]))
check("honest confidence field", res["confidence"] == 0.85 and res["lang"] == "en")

print("== 3. honest failures ==")
try:
    V.get_stt_provider().transcribe(b"\x1a\x45\xdf\xa3" + b"\x00" * 200, "webm", "en")
    check("webm rejected for local engine", False)
except Exception as e:
    check("webm rejected for local engine", "WAV" in str(e), str(e)[:120])
buf = io.BytesIO()
with wave.open(buf, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(b"\x00" * 32000)
try:
    V.get_stt_provider().transcribe(buf.getvalue(), "wav", "en")
    check("silence -> honest error", False)
except Exception as e:
    check("silence -> honest error", "no speech recognized" in str(e), str(e)[:120])

print("== 4. voice/chat loop over HTTP (local engines) ==")
from fastapi.testclient import TestClient  # noqa: E402
from backend.app.main import app  # noqa: E402
c = TestClient(app)
tok = c.post("/api/v1/auth/login", json={"username": "operator", "password": "Op123!"}).json()["access_token"]
H = {"Authorization": "Bearer " + tok}
r = c.post("/api/v1/voice/chat", files={"f": ("v.wav", out["audio"], "audio/wav")},
           data={"lang": "en"}, headers=H).json()
check("transcript is real speech", "hello" in r["transcript"].lower() and "mock transcript" not in r["transcript"],
      r["transcript"][:120])
check("answer + real audio", bool(r["message"]["content"]) and bool(r.get("audio_base64")))
import base64
raw = base64.b64decode(r["audio_base64"])
check("answer audio is speech-length wav", raw[:4] == b"RIFF" and len(raw) > 20000, str(len(raw)))
st = c.get("/api/v1/voice/status", headers=H).json()
check("status reports local engines",
      st["stt"]["provider"] == "local-stt" and st["tts"]["provider"] == "local-tts"
      and st["stt"]["available"] and st["tts"]["available"], str(st)[:150])

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
