"""/api/v1/admin: user management (owner/admin split) + platform settings.

Admins create/delete members and reset member passwords. Owners additionally promote/demote
admins and hold the settings. The owner account itself can never be deleted, demoted, or
password-changed here (deploy-machine CLI only).
"""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, db
from ..auth import ROLE_RANK

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{2,32}$")


class CreateUserBody(BaseModel):
    username: str
    password: str
    role: str = "member"                 # admin can only create members
    display_name: str = ""


class PatchUserBody(BaseModel):
    role: Optional[str] = None           # owner only: member <-> admin
    new_password: Optional[str] = None


class SettingsBody(BaseModel):
    proxy_upstream: Optional[str] = None
    proxy_agent_url: Optional[str] = None
    proxy_port: Optional[int] = None
    receiver_port: Optional[int] = None
    report_retention_days: Optional[int] = None
    registration_open: Optional[bool] = None


def _public_user(u: dict) -> dict:
    return {k: u[k] for k in ("id", "username", "role", "display_name",
                              "created_at", "last_login_at")}


@router.get("/users")
def list_users(user: dict = Depends(auth.require_roles("admin", "owner"))):
    if user["role"] == "owner":
        return {"users": [_public_user(u) for u in db.list_users()]}
    return {"users": [_public_user(u) for u in db.list_users(roles=["member", "viewer"])]}


@router.post("/users")
def create_user(body: CreateUserBody, user: dict = Depends(auth.require_roles("admin", "owner"))):
    if not _USERNAME_RE.match(body.username or ""):
        raise HTTPException(400, "username must match ^[a-zA-Z0-9_-]{2,32}$")
    if len(body.password or "") < 6:
        raise HTTPException(400, "password must be at least 6 characters")
    if body.role == "owner":
        raise HTTPException(400, "owner accounts cannot be created here")
    if body.role not in ("member", "viewer", "admin"):
        raise HTTPException(400, "role must be member/viewer/admin")
    if user["role"] != "owner" and body.role != "member":
        raise HTTPException(403, "admins can only create members")
    if db.get_user_by_name(body.username):
        raise HTTPException(409, "username already exists")
    u = db.create_user(body.username, auth.hash_password(body.password),
                       body.role, body.display_name)
    return _public_user(u)


@router.patch("/users/{username}")
def patch_user(username: str, body: PatchUserBody,
               user: dict = Depends(auth.require_roles("admin", "owner"))):
    target = db.get_user_by_name(username)
    if not target:
        raise HTTPException(404, "user not found: %s" % username)
    if target["role"] == "owner":
        raise HTTPException(400, "the owner account is managed via the deploy-machine CLI only")
    if body.role is not None:
        if user["role"] != "owner":
            raise HTTPException(403, "only the owner can change roles")
        if body.role not in ("admin", "member", "viewer"):
            raise HTTPException(400, "role must be admin/member/viewer")
        db.update_user(username, role=body.role)
    if body.new_password is not None:
        if len(body.new_password) < 6:
            raise HTTPException(400, "password must be at least 6 characters")
        if user["role"] != "owner" and target["role"] not in ("member", "viewer"):
            raise HTTPException(403, "admins can only reset member/viewer passwords")
        db.update_user(username, password_hash=auth.hash_password(body.new_password))
    return _public_user(db.get_user_by_name(username))


@router.delete("/users/{username}")
def delete_user(username: str, user: dict = Depends(auth.require_roles("admin", "owner"))):
    target = db.get_user_by_name(username)
    if not target:
        raise HTTPException(404, "user not found: %s" % username)
    if target["role"] == "owner":
        raise HTTPException(400, "the owner account cannot be deleted")
    if user["role"] != "owner" and target["role"] not in ("member", "viewer"):
        raise HTTPException(403, "admins can only delete members/viewers")
    n = db.delete_user(username)
    return {"deleted": n}


@router.get("/settings")
def get_settings(user: dict = Depends(auth.require_roles("owner",))):
    return {k: db.get_setting(k, "") for k in
            ("proxy_upstream", "proxy_agent_url", "proxy_port", "receiver_port",
             "report_retention_days", "registration_open")}


@router.put("/settings")
def put_settings(body: SettingsBody, user: dict = Depends(auth.require_roles("owner",))):
    from ...trace import llm_proxy
    if body.proxy_upstream is not None:
        db.set_setting("proxy_upstream", body.proxy_upstream.strip())
        llm_proxy.set_upstream(body.proxy_upstream)      # live, no restart
    if body.proxy_agent_url is not None:
        db.set_setting("proxy_agent_url", body.proxy_agent_url.strip())
    if body.proxy_port is not None:
        db.set_setting("proxy_port", str(body.proxy_port))
    if body.receiver_port is not None:
        db.set_setting("receiver_port", str(body.receiver_port))
    if body.report_retention_days is not None:
        if body.report_retention_days < 1:
            raise HTTPException(400, "report_retention_days must be >= 1")
        db.set_setting("report_retention_days", str(body.report_retention_days))
    if body.registration_open is not None:
        db.set_setting("registration_open", "true" if body.registration_open else "false")
    return get_settings(user)
