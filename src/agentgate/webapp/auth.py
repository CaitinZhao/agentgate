"""Four-role authentication for the web platform (doc 33 §2): owner > admin > member > viewer.

- password hashing: PBKDF2-HMAC-SHA256 (stdlib only, no extra dependency)
- sessions: random bearer token; only its sha256 is stored server-side (cookie "ag_session" or
  Authorization: Bearer both accepted)
- first deployment bootstraps the single owner account: password comes from the OWNER_PASSWORD
  env var or is generated randomly and printed once to the deploy log; the owner password can
  only be changed afterwards via `agentgate passwd` on the deploy machine (no web path)
"""
import hashlib
import os
import secrets
from typing import Dict, Optional

from fastapi import HTTPException, Request

from . import db

ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}
PBKDF2_ITERATIONS = 120_000
COOKIE_NAME = "ag_session"


# -- passwords ----------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2$%d$%s$%s" % (PBKDF2_ITERATIONS, salt.hex(), dk.hex())


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iters))
        return secrets.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


# -- request identity ----------------------------------------------------------

def _token_from_request(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()
    return request.cookies.get(COOKIE_NAME)


def current_user(request: Request) -> Dict:
    token = _token_from_request(request)
    if not token:
        raise HTTPException(401, "not logged in")
    user = db.get_session_user(token)
    if not user:
        raise HTTPException(401, "session expired or invalid")
    return user


def require_min(role: str):
    """Dependency: the caller's role must be >= the given role (owner > admin > member > viewer)."""

    def dep(request: Request) -> Dict:
        user = current_user(request)
        if ROLE_RANK.get(user["role"], -1) < ROLE_RANK[role]:
            raise HTTPException(403, "requires role %s or higher" % role)
        return user

    return dep


def require_roles(*roles: str):
    def dep(request: Request) -> Dict:
        user = current_user(request)
        if user["role"] not in roles:
            raise HTTPException(403, "requires one of roles: %s" % ", ".join(roles))
        return user

    return dep


# -- login/logout ----------------------------------------------------------------

def login(username: str, password: str) -> Dict:
    user = db.get_user_by_name(username)
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(401, "wrong username or password")
    db.update_user(username, last_login_at=db.now())
    token = db.create_session(user["id"])
    return {"token": token, "username": user["username"], "role": user["role"],
            "display_name": user["display_name"]}


def register(username: str, password: str, display_name: str = "") -> Dict:
    """Self-registration always creates a viewer (members are imported by admins only)."""
    if not db.get_bool_setting("registration_open", True):
        raise HTTPException(403, "registration is closed by the administrator")
    if db.get_user_by_name(username):
        raise HTTPException(409, "username already exists")
    user = db.create_user(username, hash_password(password), "viewer", display_name)
    return {"username": user["username"], "role": user["role"]}


def change_own_password(user: Dict, old_password: str, new_password: str):
    """Members/admins/viewers change their own password (old password required).

    The owner is rejected here on purpose: the owner password changes only through
    `agentgate passwd` on the deploy machine (single governance path, doc 33 §2).
    """
    if user["role"] == "owner":
        raise HTTPException(403, "owner password can only be changed via `agentgate passwd` "
                                 "on the deploy machine")
    fresh = db.get_user_by_name(user["username"])
    if not verify_password(old_password, fresh["password_hash"]):
        raise HTTPException(403, "old password is wrong")
    db.update_user(user["username"], password_hash=hash_password(new_password))


# -- bootstrap -------------------------------------------------------------------

def bootstrap_owner() -> Optional[Dict]:
    """Create the single owner account on first deployment; returns it (or the existing one).

    The initial password is printed once when auto-generated — deploy logs are the delivery
    channel (doc 33 §4 scenario 1). OWNER_PASSWORD env injection wins when present.
    """
    owners = db.list_users(roles=["owner"])
    if owners:
        return owners[0]
    username = os.environ.get("OWNER_USERNAME", "owner")
    password = os.environ.get("OWNER_PASSWORD", "")
    generated = False
    if not password:
        password = secrets.token_urlsafe(12)
        generated = True
    user = db.create_user(username, hash_password(password), "owner", "Platform Owner")
    print("=" * 64)
    if generated:
        print("[agentgate] owner account created (shown once; store it now):")
        print("[agentgate]   username: %s" % username)
        print("[agentgate]   password: %s" % password)
        print("[agentgate] change it with: agentgate passwd %s (on the deploy machine)" % username)
    else:
        print("[agentgate] owner account created from OWNER_PASSWORD env (%s)" % username)
    print("=" * 64)
    return user
