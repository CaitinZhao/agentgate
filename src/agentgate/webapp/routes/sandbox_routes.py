"""Sandbox exec API (P1): the only way a target agent reaches its case's sandbox.

POST /api/v1/sandbox/exec  {"token": "...", "cmd": "...", "timeout": 30}
  -> {"exit_code", "stdout", "stderr"}

The token is per sandbox, unguessable, and unregistered when the run loop closes
the case (plus a TTL sweep), so an agent can only touch its own environment and
only while its case is live. Nothing else is exposed on this endpoint.
"""
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...sandbox import registry as sb_registry

router = APIRouter(prefix="/api/v1/sandbox", tags=["sandbox"])


class ExecBody(BaseModel):
    token: str
    cmd: str
    timeout: int = 30


_exec_lock = threading.Lock()             # serialize ops per process (containers are per case)


@router.post("/exec")
def sandbox_exec(body: ExecBody):
    handle = sb_registry.resolve(body.token)
    if handle is None:
        raise HTTPException(404, "unknown or expired sandbox token")
    cmd = (body.cmd or "").strip()
    if not cmd:
        raise HTTPException(400, "cmd is required")
    timeout = max(1, min(int(body.timeout or 30), 120))
    try:
        with _exec_lock:
            out = handle.exec(cmd, timeout=timeout)
    except Exception as e:
        raise HTTPException(500, "sandbox exec failed: %s" % str(e)[:200])
    return {"exit_code": out.get("exit_code"), "stdout": out.get("stdout", ""),
            "stderr": out.get("stderr", "")}
