"""SIH flagship workflow approvals API (mounted under /api/v1/approvals).

  GET  /approvals                     own notes (WORK_MANAGE/ADMIN: all pending)
  GET  /approvals/{id}                full note + evidence (viewer rules below)
  GET  /approvals/{id}/document       generated .docx (viewer rules, audited)
  POST /approvals/{id}/decision       approve|reject|correct — WORK_MANAGE only

Viewer rules (fail-closed, 404 for outsiders — no existence oracle):
owner, ADMIN, or any role holding WORK_MANAGE (ADMIN/MANAGER/REVIEWER/
approving_manager). Decisions additionally REQUIRE WORK_MANAGE server-side,
so a requester can never decide their own note. The AI never decides: only
this authenticated, RBAC-checked endpoint moves a note out of
AWAITING_HUMAN_APPROVAL.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from . import approval_store, audit_log
from .auth_api import require_perm
from .rbac import has_permission

router = APIRouter(prefix="/api/v1/approvals", tags=["sih-approval-workflow"])

_PRIVATE_HEADERS = {"Cache-Control": "private, no-store, max-age=0",
                    "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "SAMEORIGIN"}


def _viewer(rec: dict, user: dict) -> bool:
    return (rec["owner_id"] == user["id"] or user["role"] == "ADMIN"
            or has_permission(user["role"], "WORK_MANAGE"))


def _get(rec_id: str, user: dict) -> dict:
    rec = approval_store.get(rec_id)
    if rec is None or not _viewer(rec, user):
        raise HTTPException(404, "approval note not found")
    return rec


def _can_decide(user: dict) -> bool:
    return has_permission(user["role"], "WORK_MANAGE")


@router.get("")
def list_approvals(user: dict = Depends(require_perm("DOCUMENT_READ"))):
    scope_all = _can_decide(user) or user["role"] == "ADMIN"
    recs = approval_store.list_for(None if scope_all else user["id"])
    return {"approvals": [approval_store.public(r) for r in recs],
            "can_decide": _can_decide(user)}


@router.get("/{rec_id}")
def detail(rec_id: str, user: dict = Depends(require_perm("DOCUMENT_READ"))):
    rec = _get(rec_id, user)
    return approval_store.public(rec, detail=True)


@router.get("/{rec_id}/document")
def document(rec_id: str, user: dict = Depends(require_perm("DOCUMENT_READ"))):
    rec = _get(rec_id, user)
    try:
        path = approval_store.document_path(rec)
    except FileNotFoundError:
        raise HTTPException(404, "document not found")
    audit_log.append(user["id"], user["role"], "approval_doc_download",
                     resource=rec_id, detail=f"{rec['filename']} {rec['status']}")
    return FileResponse(path, filename=rec["filename"], headers=_PRIVATE_HEADERS)


class DecisionIn(BaseModel):
    decision: str
    comment: str = ""

    @field_validator("decision")
    @classmethod
    def _decision(cls, v: str) -> str:
        if v not in ("approve", "reject", "correct"):
            raise ValueError("decision must be approve|reject|correct")
        return v

    @field_validator("comment")
    @classmethod
    def _comment(cls, v: str) -> str:
        return (v or "")[:1000]


@router.post("/{rec_id}/decision")
def decide(rec_id: str, b: DecisionIn,
           user: dict = Depends(require_perm("WORK_MANAGE"))):
    """Human decision point. Authenticated + RBAC: WORK_MANAGE only, and only
    while the note is awaiting approval. Recorded in the audit trail."""
    from . import ratelimit
    ratelimit.check("approvals", user["id"], user)
    rec = _get(rec_id, user)
    if rec["status"] != "AWAITING_HUMAN_APPROVAL":
        raise HTTPException(409, f"approval note is {rec['status']}, "
                                 "no decision is possible")
    status, stage = {"approve": ("APPROVED", "approved"),
                     "reject": ("REJECTED", "rejected"),
                     "correct": ("CORRECTION_REQUESTED", "correction_requested")}[b.decision]
    approval_store.update(rec_id, status=status, stage=stage,
                          decision=b.decision, decided_by=user.get("username", user["id"]),
                          decided_role=user["role"], decided_at=time.time(),
                          comment=b.comment.strip())
    audit_log.append(user["id"], user["role"], "approval_decision", resource=rec_id,
                     decision="allow" if b.decision == "approve" else "deny",
                     detail=f"decision={b.decision} by_role={user['role']} "
                            f"doc={rec['doc_id']}")
    return approval_store.public(approval_store.get(rec_id), detail=True)
