"""Explicit private image analysis ("Analyze with Private AI").

This is the ONLY path that runs OCR/vision text recognition on an uploaded
image — and it runs solely on an explicit, authorized request:
  1. JWT authentication (require_perm),
  2. DOCUMENT_ANALYZE permission (server-side),
  3. owner-or-ADMIN scope (fail-closed 404, no existence oracle),
  4. local-only processing (vision.py on-prem registry; never external),
  5. ephemeral result: OCR text is returned, never persisted, never indexed,
     never audited beyond metadata.

Upload and /docs analyze store metadata only and never call this module.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import private_images
from .auth_api import require_perm

router = APIRouter(prefix="/api/v1/images", tags=["image-privacy"])


class ImageAnalyzeIn(BaseModel):
    question: str = ""


@router.post("/{doc_id}/analyze")
def analyze_image(doc_id: str, b: ImageAnalyzeIn,
                  user: dict = Depends(require_perm("DOCUMENT_ANALYZE"))):
    from . import ratelimit
    ratelimit.check("analyze", user["id"], user)
    if len(b.question or "") > 2000:
        raise HTTPException(422, "question too long")
    try:
        return private_images.analyze_private_image(user, doc_id, b.question or "")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"image analysis failed: {str(e)[:150]}")
