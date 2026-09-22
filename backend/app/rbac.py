"""Phase 1 RBAC — canonical 5-role permission matrix (SIH26117).

ADMIN / MANAGER / OPERATOR / REVIEWER / USER with granular permissions.
Step 18 additionally defines the six legacy demo roles (field_engineer,
process_engineer, safety_inspector, approving_manager, security_admin,
auditor) with least-privilege sets derived from roles.yaml — additive only,
the original five sets are unchanged.
The backend resolves permissions server-side from the role; the JWT only
carries identity (user_id, username, role). Frontend mirrors are UI-only.
"""
from __future__ import annotations

PERMISSIONS = [
    "USER_READ", "USER_CREATE", "USER_UPDATE", "USER_DELETE",
    "DOCUMENT_READ", "DOCUMENT_UPLOAD", "DOCUMENT_DELETE", "DOCUMENT_ANALYZE",
    "AI_CHAT", "AI_AGENT_USE",
    "REPORT_READ", "REPORT_CREATE",
    "AUDIT_READ",
    "MODEL_READ", "MODEL_CONFIGURE",
    "SYSTEM_CONFIGURE",
    # Step 27 workspace permissions (additive; existing sets extended below).
    "WORK_READ", "WORK_MANAGE",
    "ATTENDANCE_READ", "ATTENDANCE_MANAGE",
    "WORKSHEET_READ", "WORKSHEET_MANAGE",
    "ANALYTICS_READ",
]

ROLES = [
    # Phase 1 canonical five (unchanged below — do not alter their sets).
    "ADMIN", "MANAGER", "OPERATOR", "REVIEWER", "USER",
    # SIH demo: EMPLOYEE (additive; employee self-service, OPERATOR-grade).
    "EMPLOYEE",
    # Step 18 legacy demo roles (additive; least-privilege per roles.yaml).
    "field_engineer", "process_engineer", "safety_inspector",
    "approving_manager", "security_admin", "auditor",
]

_ALL = set(PERMISSIONS)

ROLE_PERMISSIONS: dict[str, set[str]] = {
    # Full control: users, docs, AI, reports, audit, models, system.
    "ADMIN": set(_ALL),
    # Team lead: people (read), docs workflow, AI, reports, models (read).
    # No user mutations, no doc delete, no audit, no model/system configure.
    # Step 27: full workspace management for their department scope.
    "MANAGER": {
        "USER_READ",
        "DOCUMENT_READ", "DOCUMENT_UPLOAD", "DOCUMENT_ANALYZE",
        "AI_CHAT", "AI_AGENT_USE",
        "REPORT_READ", "REPORT_CREATE",
        "MODEL_READ",
        "WORK_READ", "WORK_MANAGE",
        "ATTENDANCE_READ", "ATTENDANCE_MANAGE",
        "WORKSHEET_READ", "WORKSHEET_MANAGE",
        "ANALYTICS_READ",
    },
    # Hands-on worker: upload + read docs, use AI, file reports.
    # Step 27: own work/attendance/worksheets (employee self-service).
    "OPERATOR": {
        "DOCUMENT_READ", "DOCUMENT_UPLOAD",
        "AI_CHAT", "AI_AGENT_USE",
        "REPORT_READ", "REPORT_CREATE",
        "MODEL_READ",
        "WORK_READ", "ATTENDANCE_READ", "WORKSHEET_READ",
    },
    # Independent checker: read + analyze, reports, read audit trail.
    # Step 27: review actions on work/worksheets (create/assign stay managerial).
    "REVIEWER": {
        "DOCUMENT_READ", "DOCUMENT_ANALYZE",
        "AI_CHAT", "AI_AGENT_USE",
        "REPORT_READ", "REPORT_CREATE",
        "AUDIT_READ",
        "MODEL_READ",
        "WORK_READ", "WORK_MANAGE",
        "ATTENDANCE_READ", "WORKSHEET_READ",
    },
    # Base consumer: read docs, chat, read reports/models.
    # Step 27: own work/attendance/worksheets (employee self-service).
    "USER": {
        "DOCUMENT_READ",
        "AI_CHAT",
        "REPORT_READ",
        "MODEL_READ",
        "WORK_READ", "ATTENDANCE_READ", "WORKSHEET_READ",
    },
    # SIH demo EMPLOYEE: own work self-service + evidence upload + voice/chat.
    # Needs DOCUMENT_ANALYZE so employees can process their own uploads and
    # ask the assistant about them (additive; nothing else changed).
    "EMPLOYEE": {
        "DOCUMENT_READ", "DOCUMENT_UPLOAD", "DOCUMENT_ANALYZE",
        "AI_CHAT", "AI_AGENT_USE",
        "REPORT_READ", "REPORT_CREATE",
        "MODEL_READ",
        "WORK_READ", "ATTENDANCE_READ", "WORKSHEET_READ",
    },
    # --- Step 18 legacy roles (additive; existing five sets untouched) ---
    # Field engineer: hands-on docs + AI assistance. Step 27: own work.
    "field_engineer": {
        "DOCUMENT_READ", "DOCUMENT_UPLOAD", "DOCUMENT_ANALYZE",
        "AI_CHAT", "AI_AGENT_USE",
        "WORK_READ", "ATTENDANCE_READ", "WORKSHEET_READ",
    },
    # Process engineer: field work + report drafting. Step 27: own work.
    "process_engineer": {
        "DOCUMENT_READ", "DOCUMENT_UPLOAD", "DOCUMENT_ANALYZE",
        "AI_CHAT", "AI_AGENT_USE",
        "REPORT_READ", "REPORT_CREATE",
        "WORK_READ", "ATTENDANCE_READ", "WORKSHEET_READ",
    },
    # Safety inspector: inspect/analyze documents with AI help. Step 27: own work.
    "safety_inspector": {
        "DOCUMENT_READ", "DOCUMENT_UPLOAD", "DOCUMENT_ANALYZE",
        "AI_CHAT", "AI_AGENT_USE",
        "WORK_READ", "ATTENDANCE_READ", "WORKSHEET_READ",
    },
    # Approving manager: workflow + reports + model visibility. Step 27: team scope.
    "approving_manager": {
        "DOCUMENT_READ", "DOCUMENT_UPLOAD", "DOCUMENT_ANALYZE",
        "AI_CHAT", "AI_AGENT_USE",
        "REPORT_READ", "REPORT_CREATE",
        "MODEL_READ",
        "WORK_READ", "WORK_MANAGE",
        "ATTENDANCE_READ", "ATTENDANCE_MANAGE",
        "WORKSHEET_READ", "WORKSHEET_MANAGE",
        "ANALYTICS_READ",
    },
    # Security admin: users/audit/models/system — content-blind by design
    # (no DOCUMENT_* permissions: list/preview/retrieve are all blocked, and
    # RAG retrieval is ownership-filtered). AI_CHAT + AI_AGENT_USE exist only
    # to run the admin_assistant agent (general chat / summarization of text
    # the admin supplies — never document content).
    "security_admin": {
        "USER_READ", "USER_CREATE", "USER_UPDATE", "USER_DELETE",
        "AUDIT_READ",
        "MODEL_READ", "MODEL_CONFIGURE",
        "SYSTEM_CONFIGURE",
        "REPORT_READ",
        "AI_CHAT", "AI_AGENT_USE",
    },
    # Auditor: read-only oversight of audit trail, reports, models.
    "auditor": {
        "AUDIT_READ",
        "REPORT_READ",
        "MODEL_READ",
    },
}


def role_permissions(role: str) -> set[str]:
    return set(ROLE_PERMISSIONS.get(role, set()))


def has_permission(role: str, perm: str) -> bool:
    return perm in ROLE_PERMISSIONS.get(role, set())
