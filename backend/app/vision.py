"""OCR/vision provider abstraction (Phase 3).

No cloud API is hard-coded. LocalVisionProvider validates + previews with
Pillow and OCRs only if an on-prem engine is present (tesseract binary via
optional pytesseract). Otherwise OCR reports `ocr_unavailable` honestly —
images are still stored, validated, and previewable, ready for a future
local engine (register it in get_vision_provider()).
"""
from __future__ import annotations

import io
import os
import shutil
from abc import ABC, abstractmethod

THUMB_MAX_PX = 512


class VisionProvider(ABC):
    name: str = "base"

    @abstractmethod
    def validate(self, data: bytes) -> dict:
        """Verify decodable image. Returns {format, width, height} or raises."""

    @abstractmethod
    def thumbnail(self, data: bytes, max_px: int = THUMB_MAX_PX) -> bytes:
        """PNG thumbnail bytes."""

    @abstractmethod
    def ocr(self, data: bytes) -> dict:
        """Return {text, confidence, engine, status}."""


class VisionError(Exception):
    pass


class LocalVisionProvider(VisionProvider):
    name = "local"

    def validate(self, data: bytes) -> dict:
        from PIL import Image
        try:
            im = Image.open(io.BytesIO(data))
            im.load()  # force full decode (catches truncated/corrupt)
            if im.format not in ("PNG", "JPEG", "JPG", "MPO"):
                raise VisionError(f"unsupported image format {im.format}")
            return {"format": im.format, "width": im.width, "height": im.height}
        except VisionError:
            raise
        except Exception as e:
            raise VisionError(f"invalid image: {e}")

    def thumbnail(self, data: bytes, max_px: int = THUMB_MAX_PX) -> bytes:
        from PIL import Image
        try:
            im = Image.open(io.BytesIO(data)).convert("RGB")
            im.thumbnail((max_px, max_px))
            buf = io.BytesIO()
            im.save(buf, "PNG")
            return buf.getvalue()
        except Exception as e:
            raise VisionError(f"thumbnail failed: {e}")

    def ocr(self, data: bytes) -> dict:
        try:
            import pytesseract  # type: ignore
            from PIL import Image
        except Exception:
            return {"text": "", "confidence": 0.0, "engine": "none", "status": "ocr_unavailable"}
        if not shutil.which("tesseract"):
            return {"text": "", "confidence": 0.0, "engine": "none", "status": "ocr_unavailable"}
        try:
            im = Image.open(io.BytesIO(data))
            d = pytesseract.image_to_data(im, output_type=pytesseract.Output.DICT)
            words = [w for w in d.get("text", []) if w.strip()]
            confs = [float(c) for w, c in zip(d.get("text", []), d.get("conf", []))
                     if w.strip() and float(c) >= 0]
            conf = round(sum(confs) / len(confs) / 100, 3) if confs else 0.0
            return {"text": " ".join(words)[:20000], "confidence": conf,
                    "engine": "tesseract-local", "status": "done"}
        except Exception as e:
            return {"text": "", "confidence": 0.0, "engine": "tesseract-local",
                    "status": "ocr_failed", "error": str(e)[:200]}


def get_vision_provider() -> VisionProvider:
    which = (os.getenv("SOV_VISION_PROVIDER") or "local").lower()
    return LocalVisionProvider()  # 'local' is the only engine today; registry point
