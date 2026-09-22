"""Step 20 voice I/O providers (STT/TTS).

Abstractions so local/on-prem engines plug in without touching app code.
Defaults are mock (deterministic, offline, test-safe). Remote engines are
fail-closed disabled unless explicitly configured — enterprise audio/text
never leaves on-prem by default.

SOV_STT_PROVIDER / SOV_TTS_PROVIDER: mock | local | unavailable.
"""
from __future__ import annotations

import base64
import io
import math
import os
import struct
import wave
from abc import ABC, abstractmethod

MAX_AUDIO_MB_DEFAULT = 5
MAX_TTS_CHARS = 2000


class STTError(Exception):
    pass


class TTSError(Exception):
    pass


def sniff_audio(data: bytes) -> str:
    """wav|webm|ogg|mp3 from magic bytes. Raises STTError when unknown."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:3] == b"ID3" or data[:2] == b"\xff\xfb":
        return "mp3"
    raise STTError("unrecognized audio format (wav/webm/ogg/mp3 expected)")


def max_audio_bytes() -> int:
    try:
        return int(float(os.getenv("SOV_MAX_AUDIO_MB", str(MAX_AUDIO_MB_DEFAULT))) * 1024 * 1024)
    except Exception:
        return MAX_AUDIO_MB_DEFAULT * 1024 * 1024


class STTProvider(ABC):
    name: str = "base"

    @abstractmethod
    def transcribe(self, audio: bytes, fmt: str, lang: str) -> dict:
        """Return {text, confidence, lang}. Raises STTError."""

    def available(self) -> bool:
        return True


class MockSTTProvider(STTProvider):
    """Deterministic stub: echoes language-tagged canned text.

    Lets the voice->chat->voice loop run offline. `heard` records calls
    for assertions.
    """
    name = "mock-stt"

    def __init__(self):
        self.heard: list[dict] = []

    def transcribe(self, audio: bytes, fmt: str, lang: str) -> dict:
        self.heard.append({"bytes": len(audio), "fmt": fmt, "lang": lang})
        return {"text": f"mock transcript in {lang}: what do my documents say",
                "confidence": 0.99, "lang": lang}


class LocalSTTProvider(STTProvider):
    """On-prem engine: vosk (offline model) when installed, else remote
    only if explicitly configured, else unavailable. Never a cloud default."""
    name = "local-stt"

    def available(self) -> bool:
        if _vosk_model_dir() and _vosk_importable():
            return True
        for mod in ("faster_whisper", "whisper"):
            try:
                __import__(mod)
                return True
            except Exception:
                continue
        return bool(os.getenv("SOV_STT_REMOTE_URL", ""))

    def transcribe(self, audio: bytes, fmt: str, lang: str) -> dict:
        if fmt == "wav" and _vosk_model_dir() and _vosk_importable():
            return _vosk_transcribe(audio, lang)
        url = os.getenv("SOV_STT_REMOTE_URL", "")
        if not url or os.getenv("SOV_STT_ALLOW_REMOTE", "0") != "1":
            raise STTError("local speech recognition needs WAV audio with the "
                           "on-prem engine installed; otherwise configure "
                           "SOV_STT_REMOTE_URL + SOV_STT_ALLOW_REMOTE=1")
        import json
        import urllib.request
        req = urllib.request.Request(url, data=json.dumps(
            {"audio_b64": base64.b64encode(audio).decode(), "fmt": fmt,
             "lang": lang}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                out = json.loads(resp.read().decode())
        except Exception as e:
            raise STTError(f"remote STT failed: {e}") from e
        if "text" not in out:
            raise STTError("bad remote STT response")
        return {"text": out["text"][:5000],
                "confidence": float(out.get("confidence", 0.0)), "lang": lang}


class UnavailableSTTProvider(STTProvider):
    name = "unavailable-stt"

    def available(self) -> bool:
        return False

    def transcribe(self, audio: bytes, fmt: str, lang: str) -> dict:
        raise STTError("speech-to-text provider unavailable")


def get_stt_provider() -> STTProvider:
    which = (os.getenv("SOV_STT_PROVIDER") or "mock").lower()
    if which == "local":
        return LocalSTTProvider()
    if which == "unavailable":
        return UnavailableSTTProvider()
    return MockSTTProvider()


class TTSProvider(ABC):
    name: str = "base"

    @abstractmethod
    def synthesize(self, text: str, lang: str) -> dict:
        """Return {audio, mime, engine}. Raises TTSError."""

    def available(self) -> bool:
        return True


def _tone_wav(text: str) -> bytes:
    """Deterministic placeholder WAV (440Hz blips per 32 chars, ≤4s).

    Real speech needs an engine; this is envelope-valid audio/wav so the
    play/stop UI and byte-level tests work offline.
    """
    rate, n = 8000, min(4 * 8000, max(8000, len(text) // 32 * 8000))
    frames = b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 440 * i / rate)))
                      for i in range(n))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


class MockTTSProvider(TTSProvider):
    name = "mock-tts"

    def synthesize(self, text: str, lang: str) -> dict:
        return {"audio": _tone_wav(text), "mime": "audio/wav",
                "engine": "mock", "chars": len(text), "lang": lang}


def _vosk_importable() -> bool:
    try:
        import vosk  # noqa: F401
        return True
    except Exception:
        return False


def _vosk_model_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))  # backend/app
    cands = [
        os.getenv("SOV_VOSK_MODEL_DIR", ""),
        os.path.join(os.path.dirname(here), "models", "vosk-small-en"),
    ]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "conf", "model.conf")):
            return c
    return ""


_vosk_model = None


def _get_vosk_model():
    global _vosk_model
    if _vosk_model is None:
        from vosk import Model
        _vosk_model = Model(_vosk_model_dir())
    return _vosk_model


def _wav_to_pcm16k(data: bytes) -> bytes:
    """Decode WAV bytes -> 16kHz mono 16-bit PCM for vosk (pure stdlib)."""
    with wave.open(io.BytesIO(data), "rb") as w:
        nch, sw, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        if n <= 0 or n > 16 * 60 * 16000:
            raise STTError("audio length out of range")
        raw = w.readframes(n)
    if sw == 1:
        samples = [(b - 128) * 256 for b in raw]
    elif sw == 2:
        samples = list(struct.unpack("<%dh" % (len(raw) // 2), raw))
    elif sw == 4:
        samples = [max(-32768, min(32767, int(v * 32767)))
                   for v in struct.unpack("<%df" % (len(raw) // 4), raw)]
    else:
        raise STTError("unsupported WAV sample width")
    if nch > 1:
        samples = [sum(samples[i:i + nch]) // nch for i in range(0, len(samples), nch)]
    if rate != 16000 and samples:
        ratio = rate / 16000
        samples = [samples[min(len(samples) - 1, int(i * ratio))] for i in range(int(len(samples) / ratio))]
    if not samples:
        raise STTError("no speech recognized")
    return struct.pack("<%dh" % len(samples), *[max(-32768, min(32767, s)) for s in samples])


def _vosk_transcribe(audio: bytes, lang: str) -> dict:
    from vosk import KaldiRecognizer
    pcm = _wav_to_pcm16k(audio)
    rec = KaldiRecognizer(_get_vosk_model(), 16000)
    import json as _json
    for i in range(0, len(pcm), 8000):
        rec.AcceptWaveform(pcm[i:i + 8000])
    try:
        text = _json.loads(rec.FinalResult()).get("text", "").strip()
    except Exception as e:
        raise STTError(f"transcription failed: {e}")
    if not text:
        raise STTError("no speech recognized — please speak clearly and retry")
    return {"text": text[:5000], "confidence": 0.85, "lang": lang}


class LocalTTSProvider(TTSProvider):
    """On-prem engine: pyttsx3 (OS voices, e.g. Windows SAPI) when installed,
    else piper/espeak binaries, else remote only if explicitly configured."""
    name = "local-tts"

    def available(self) -> bool:
        try:
            import pyttsx3  # noqa: F401
            return True
        except Exception:
            pass
        import shutil
        if shutil.which("piper") or shutil.which("espeak-ng") or shutil.which("espeak"):
            return True
        try:
            import piper  # type: ignore  # noqa: F401
            return True
        except Exception:
            pass
        return bool(os.getenv("SOV_TTS_REMOTE_URL", ""))

    def synthesize(self, text: str, lang: str) -> dict:
        try:
            import pyttsx3  # noqa: F401
            return _sapi_synthesize(text, lang)
        except Exception:
            pass
        url = os.getenv("SOV_TTS_REMOTE_URL", "")
        if not url or os.getenv("SOV_TTS_ALLOW_REMOTE", "0") != "1":
            raise TTSError("no local TTS engine and remote TTS not configured "
                           "(set SOV_TTS_REMOTE_URL + SOV_TTS_ALLOW_REMOTE=1)")
        import json
        import urllib.request
        req = urllib.request.Request(url, data=json.dumps(
            {"text": text[:MAX_TTS_CHARS], "lang": lang}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                out = json.loads(resp.read().decode())
        except Exception as e:
            raise TTSError(f"remote TTS failed: {e}") from e
        try:
            audio = base64.b64decode(out["audio_b64"])
        except Exception:
            raise TTSError("bad remote TTS response")
        return {"audio": audio, "mime": out.get("mime", "audio/wav"),
                "engine": "remote", "chars": len(text), "lang": lang}


import threading as _threading

_tts_lock = _threading.Lock()


def _sapi_synthesize(text: str, lang: str) -> dict:
    """Windows SAPI (or any OS pyttsx3 driver): real offline speech to WAV."""
    import pyttsx3
    import tempfile
    clip = text[:MAX_TTS_CHARS]
    if not clip.strip():
        raise TTSError("nothing to speak")
    with _tts_lock:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        try:
            engine = pyttsx3.init()
            try:
                want = (lang or "en").lower()
                for v in engine.getProperty("voices") or []:
                    blob = f"{getattr(v, 'id', '')} {getattr(v, 'name', '')} {getattr(v, 'languages', '')}".lower()
                    if want in blob or (want == "en" and "english" in blob):
                        engine.setProperty("voice", v.id)
                        break
            except Exception:
                pass
            engine.save_to_file(clip, tmp.name)
            engine.runAndWait()
            try:
                engine.stop()
            except Exception:
                pass
            del engine
            with open(tmp.name, "rb") as f:
                audio = f.read()
        finally:
            try:
                os.remove(tmp.name)
            except Exception:
                pass
    if len(audio) < 100 or not audio.startswith(b"RIFF"):
        raise TTSError("speech synthesis produced no audio")
    return {"audio": audio, "mime": "audio/wav",
            "engine": "local-sapi", "chars": len(clip), "lang": lang}


class UnavailableTTSProvider(TTSProvider):
    name = "unavailable-tts"

    def available(self) -> bool:
        return False

    def synthesize(self, text: str, lang: str) -> dict:
        raise TTSError("text-to-speech provider unavailable")


def get_tts_provider() -> TTSProvider:
    which = (os.getenv("SOV_TTS_PROVIDER") or "mock").lower()
    if which == "local":
        return LocalTTSProvider()
    if which == "unavailable":
        return UnavailableTTSProvider()
    return MockTTSProvider()
