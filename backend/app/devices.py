"""External attendance device gateway (SIH demo).

Architecture: device -> POST /api/v1/devices/attendance (device-key auth)
-> employee identification -> attendance record -> dashboards. The gateway
never trusts the device beyond its key: employee must exist + be active,
timestamps are validated, and every event is audited with device metadata.

Fail-closed: with no SOV_DEVICE_KEYS configured, every device call is
denied. Device keys are never logged. A separate ADMIN simulator endpoint
(requiring ATTENDANCE_MANAGE) lets the demo run without physical hardware.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, field_validator

from . import audit_log
from . import userstore
from . import work as store
from . import config as cfg
from .auth_api import require_perm

router = APIRouter(tags=["device-gateway"])

DEVICE_EVENTS = ("CHECK_IN", "CHECK_OUT")


def _device_keys() -> list[str]:
    import os
    return [k.strip() for k in os.getenv("SOV_DEVICE_KEYS", "").split(",") if k.strip()]


def _check_device_key(key: str | None) -> str:
    keys = _device_keys()
    if not keys or not key or key not in keys:
        audit_log.append(None, None, "device_denied", resource="gateway", decision="deny")
        raise HTTPException(403, "device gateway disabled or invalid device key")
    return key


def _ingest(device_id: str, employee: str, timestamp: float, event: str) -> dict:
    """Shared ingest for real devices and the authorized simulator."""
    rec = (userstore.find_by_username(employee) or userstore.find_by_id(employee)
           or userstore.find_by_employee_id(employee) or userstore.find_by_phone(employee))
    if not rec or not rec["active"]:
        audit_log.append(None, None, "device_denied", resource=device_id, decision="deny",
                         detail="unknown or inactive employee")
        raise HTTPException(404, "employee not found")
    day = time.strftime("%Y-%m-%d", time.localtime(timestamp))
    if event == "CHECK_IN":
        out = store.mark_attendance(rec["id"], day, "present", f"device:{device_id}",
                                    "external device check-in")
    else:
        out = {"user_id": rec["id"], "date": day, "event": "CHECK_OUT", "status": "recorded"}
    audit_log.append(rec["id"], rec["role"], "device_event", resource=device_id,
                     detail=f"{event} employee={rec['username']} day={day}")
    return {"employee": rec["username"], "user_id": rec["id"], "date": day,
            "event": event, "attendance": out}


class DeviceEvent(BaseModel):
    employee: str
    timestamp: float = 0
    device_id: str = "DEVICE-01"
    event: str = "CHECK_IN"

    @field_validator("event")
    @classmethod
    def _event(cls, v: str) -> str:
        if v not in DEVICE_EVENTS:
            raise ValueError(f"event must be one of {DEVICE_EVENTS}")
        return v


@router.post("/api/v1/devices/attendance")
def device_attendance(b: DeviceEvent, x_device_key: str | None = Header(default=None)):
    from . import ratelimit
    ratelimit.check("device", f"dev:{(b.device_id or '')[:32]}")
    _check_device_key(x_device_key)
    ts = b.timestamp or time.time()
    if abs(time.time() - ts) > 24 * 3600:
        raise HTTPException(422, "timestamp outside acceptable skew")
    return _ingest((b.device_id or "DEVICE-01")[:64], (b.employee or "")[:160], ts, b.event)


class SimulateIn(BaseModel):
    username: str
    event: str = "CHECK_IN"

    @field_validator("event")
    @classmethod
    def _event(cls, v: str) -> str:
        if v not in DEVICE_EVENTS:
            raise ValueError(f"event must be one of {DEVICE_EVENTS}")
        return v


@router.post("/api/v1/admin/devices/simulate")
def simulate_device(b: SimulateIn, user: dict = Depends(require_perm("ATTENDANCE_MANAGE"))):
    """Demo simulator: authorized officials only. Drives the SAME ingest path
    as a physical device so the dashboard update is identical."""
    from . import ratelimit
    ratelimit.check("device", user["id"], user)
    if user["role"] not in ("ADMIN", "MANAGER", "approving_manager"):
        raise HTTPException(403, "simulator requires an admin/manager role")
    out = _ingest("SIMULATOR", (b.username or "")[:160], time.time(), b.event)
    audit_log.append(user["id"], user["role"], "device_simulated",
                     resource=out["user_id"], detail=f"{b.event} for {out['employee']}")
    return {**out, "simulated": True, "by": user["username"]}


@router.get("/api/v1/devices/status")
def device_status(user: dict = Depends(require_perm("ATTENDANCE_READ"))):
    """Synchronized-device status for the caller: whether a gateway is
    configured and when this employee's attendance was last device-synced.
    Honest empty state when no device is connected or never synced."""
    keys = _device_keys()
    last = None
    for a in store.list_attendance([user["id"]]):
        if (a.get("marked_by") or "").startswith(("device:", "SIM")):
            if last is None or (a.get("created_at") or 0) > (last.get("created_at") or 0):
                last = a
    return {"connected": bool(keys),
            "last_sync": last["created_at"] if last else None,
            "last_sync_date": last["date"] if last else None,
            "message": ("Attendance device not connected"
                        if not keys else
                        ("Device synchronized" if last else
                         "Device configured — no synced records yet"))}
