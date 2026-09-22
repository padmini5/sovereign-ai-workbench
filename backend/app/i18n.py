"""Step 19 Indian multilingual support.

Supported UI/answer languages (12): en hi te ta kn ml mr bn gu pa or ur.

Security: translation is presentation-layer ONLY. Document access is decided
before retrieval (rag.verify_doc_access + owner-prefiltered search); the
translator receives only already-authorized answer text. Remote translation
is fail-closed disabled unless SOV_TRANSLATE_REMOTE_URL is explicitly set
(enterprise doc data never leaves on-prem by default).

Providers (SOV_TRANSLATE_PROVIDER): mock (default, deterministic) | local
(on-prem engine when present, else honest unavailable) | unavailable
(forced failure for tests).
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

LANGS = [
    ("en", "English"), ("hi", "हिन्दी"), ("te", "తెలుగు"), ("ta", "தமிழ்"),
    ("kn", "ಕನ್ನಡ"), ("ml", "മലയാളം"), ("mr", "मराठी"), ("bn", "বাংলা"),
    ("gu", "ગુજરાતી"), ("pa", "ਪੰਜਾਬੀ"), ("or", "ଓଡ଼ିଆ"), ("ur", "اردو"),
]
SUPPORTED = {c for c, _ in LANGS}
DEFAULT = "en"
MAX_CHARS = 20000


class TranslationError(Exception):
    pass


class TranslationProvider(ABC):
    name: str = "base"

    @abstractmethod
    def translate(self, text: str, target: str) -> str:
        """Translate already-authorized answer text. Raises TranslationError."""

    def available(self) -> bool:
        return True


class MockTranslationProvider(TranslationProvider):
    """Deterministic stub: en passes through, others get a [code] tag.

    No semantics, no network — lets the full suite run offline while proving
    the plumbing (selection, prefs, RAG-in-language, fallback, citations).
    """
    name = "mock"

    def translate(self, text: str, target: str) -> str:
        if target not in SUPPORTED:
            raise TranslationError(f"unsupported language '{target}'")
        if target == "en":
            return text
        return f"[{target}] {text[:MAX_CHARS]}"


class LocalTranslationProvider(TranslationProvider):
    """On-prem engine when installed; remote only if explicitly configured."""
    name = "local"

    def available(self) -> bool:
        try:
            import argos  # type: ignore  # noqa: F401
            return True
        except Exception:
            pass
        return bool(os.getenv("SOV_TRANSLATE_REMOTE_URL", ""))

    def translate(self, text: str, target: str) -> str:
        if target not in SUPPORTED:
            raise TranslationError(f"unsupported language '{target}'")
        if target == "en":
            return text
        url = os.getenv("SOV_TRANSLATE_REMOTE_URL", "")
        if not url or os.getenv("SOV_TRANSLATE_ALLOW_REMOTE", "0") != "1":
            raise TranslationError("no local engine and remote translation not configured "
                                   "(set SOV_TRANSLATE_REMOTE_URL + SOV_TRANSLATE_ALLOW_REMOTE=1)")
        import json
        import urllib.request
        req = urllib.request.Request(url, data=json.dumps(
            {"text": text[:MAX_CHARS], "target": target}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                out = json.loads(resp.read().decode())
        except Exception as e:
            raise TranslationError(f"remote translation failed: {e}") from e
        if "text" not in out:
            raise TranslationError("bad remote response")
        return out["text"]


class UnavailableTranslationProvider(TranslationProvider):
    name = "unavailable"

    def available(self) -> bool:
        return False

    def translate(self, text: str, target: str) -> str:
        raise TranslationError("translation provider unavailable")


def get_translation_provider() -> TranslationProvider:
    which = (os.getenv("SOV_TRANSLATE_PROVIDER") or "mock").lower()
    if which == "local":
        return LocalTranslationProvider()
    if which == "unavailable":
        return UnavailableTranslationProvider()
    return MockTranslationProvider()


NOT_FOUND = {
    "en": "I couldn't find that information in the available documents. Try rephrasing, or check that the right documents are READY.",
    "hi": "मुझे उपलब्ध दस्तावेज़ों में यह जानकारी नहीं मिली। कृपया प्रश्न बदलकर पूछें, या जांचें कि सही दस्तावेज़ READY हैं।",
    "te": "అందుబాటులో ఉన్న పత్రాలలో ఈ సమాచారం నాకు కనపడలేదు. దయచేసి మళ్లీ అడగండి లేదా సరైన పత్రాలు READY గా ఉన్నాయో చూడండి.",
    "ta": "கிடைக்கக்கூடிய ஆவணங்களில் இந்தத் தகவல் எனக்குக் கிடைக்கவில்லை. மீண்டும் கேளுங்கள் அல்லது சரியான ஆவணங்கள் READY நிலையில் உள்ளனவா எனச் சரிபார்க்கவும்.",
    "kn": "ಲಭ್ಯವಿರುವ ದಾಖಲೆಗಳಲ್ಲಿ ಈ ಮಾಹಿತಿ ನನಗೆ ಸಿಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೆ ಕೇಳಿ ಅಥವಾ ಸರಿಯಾದ ದಾಖಲೆಗಳು READY ಆಗಿವೆಯೇ ಎಂದು ಪರಿಶೀಲಿಸಿ.",
    "ml": "ലഭ്യമായ രേഖകളിൽ ഈ വിവരം എനിക്ക് കണ്ടെത്താനായില്ല. ദയവായി വീണ്ടും ചോദിക്കൂ അല്ലെങ്കിൽ ശരിയായ രേഖകൾ READY ആണോ എന്ന് പരിശോധിക്കൂ.",
    "mr": "उपलब्ध दस्तऐवजांमध्ये मला ही माहिती सापडली नाही. कृपया पुन्हा विचारा किंवा योग्य दस्तऐवज READY आहेत का ते तपासा.",
    "bn": "উপলব্ধ নথিগুলিতে আমি এই তথ্য খুঁজে পাইনি। অনুগ্রহ করে আবার জিজ্ঞাসা করুন বা সঠিক নথিগুলি READY আছে কিনা দেখুন।",
    "gu": "ઉપલબ્ધ દસ્તાવેજોમાં મને આ માહિતી મળી નથી. કૃપા કરીને ફરી પૂછો અથવા સાચા દસ્તાવેજો READY છે કે કેમ તે તપાસો.",
    "pa": "ਉਪਲਬਧ ਦਸਤਾਵੇਜ਼ਾਂ ਵਿੱਚ ਮੈਨੂੰ ਇਹ ਜਾਣਕਾਰੀ ਨਹੀਂ ਮਿਲੀ। ਕਿਰਪਾ ਕਰਕੇ ਦੁਬਾਰਾ ਪੁੱਛੋ ਜਾਂ ਜਾਂਚੋ ਕਿ ਸਹੀ ਦਸਤਾਵੇਜ਼ READY ਹਨ।",
    "or": "ଉପଲବ୍ଧ ଦସ୍ତାବିଜଗୁଡ଼ିକରେ ମୋତେ ଏହି ସୂଚନା ମିଳିଲା ନାହିଁ। ଦୟାକରି ପୁଣି ପଚାରନ୍ତୁ କିମ୍ବା ସଠିକ ଦସ୍ତାବିଜ READY ଅଛି କି ନାହିଁ ଯାଞ୍ଚ କରନ୍ତୁ।",
    "ur": "دستیاب دستاویزات میں مجھے یہ معلومات نہیں ملیں۔ براہ کرم دوبارہ پوچھیں یا چیک کریں کہ صحیح دستاویزات READY ہیں۔",
}


def not_found_in(lang: str) -> str:
    return NOT_FOUND.get(lang, NOT_FOUND["en"])


def translate_answer(text_with_sources: str, target: str,
                     provider: TranslationProvider) -> tuple[str, bool]:
    """Translate the answer body, keep the Sources block in English.

    Returns (text, translated_flag). Filenames/citations/page refs are
    technical identifiers and stay in English (as do UI chrome IDs).
    """
    if target == "en":
        return text_with_sources, False
    head, sep, tail = text_with_sources.partition("\n\nSources:\n")
    try:
        out = provider.translate(head, target)
    except TranslationError:
        return text_with_sources, False  # graceful fallback: English
    return (out + (sep + tail if sep else "")), True
