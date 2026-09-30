"""/api/v1/auth: register (viewer) / login / logout / me / change own password."""
import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth, db

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{2,32}$")


class RegisterBody(BaseModel):
    username: str
    password: str
    display_name: str = ""


class LoginBody(BaseModel):
    username: str
    password: str


class PasswordBody(BaseModel):
    old_password: str
    new_password: str


def _validate_credentials(username: str, password: str):
    if not _USERNAME_RE.match(username or ""):
        raise HTTPException(400, "username must match ^[a-zA-Z0-9_-]{2,32}$")
    if len(password or "") < 6:
        raise HTTPException(400, "password must be at least 6 characters")


@router.post("/register")
def register(body: RegisterBody):
    _validate_credentials(body.username, body.password)
    return auth.register(body.username, body.password, body.display_name)


@router.post("/login")
def login(body: LoginBody, response: Response):
    result = auth.login(body.username, body.password)
    response.set_cookie(auth.COOKIE_NAME, result["token"], httponly=True,
                        samesite="lax", max_age=7 * 86400, path="/")
    return result


@router.post("/logout")
def logout(request: Request, response: Response):
    token = auth._token_from_request(request)
    if token:
        db.delete_session(token)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(auth.current_user)):
    ai = db.get_user_settings(user["id"])
    return {"username": user["username"], "role": user["role"],
            "display_name": user["display_name"] or "",
            "created_at": user.get("created_at") or "",
            "last_login_at": user.get("last_login_at") or "",
            "ai_configured": bool(ai.get("ai_base_url") and ai.get("ai_model")),
            "ai_prompt_dismissed": str(ai.get("ai_prompt_dismissed", "")).lower() in ("1", "true", "yes"),
            "ai": {"base_url": ai.get("ai_base_url", ""), "model": ai.get("ai_model", ""),
                   "judge_auto": str(ai.get("ai_judge_auto", "")).lower() in ("1", "true", "yes")}}


class AISettingsBody(BaseModel):
    base_url: str = ""
    api_key: Optional[str] = None             # empty = keep the stored key
    model: str = ""
    judge_auto: bool = False                  # doc 35 §5.1: high-confidence auto-adopt, default OFF

    class Config:
        extra = "ignore"


@router.post("/me/ai-prompt-dismissed")
def post_ai_prompt_dismissed(user: dict = Depends(auth.current_user)):
    """First-login AI-assist prompt: remember that this user chose "later" so the
    dialog does not nag again (it also stays hidden once AI is configured)."""
    db.set_user_settings(user["id"], {"ai_prompt_dismissed": "1"})
    return {"ok": True}


@router.put("/me/ai-settings")
def put_ai_settings(body: AISettingsBody, user: dict = Depends(auth.current_user)):
    """User-Center AI settings (doc 34): optional, per-user, enables AI report summary /
    judge suggestions / pack & gold drafting. The key is stored server-side only."""
    values = {"ai_base_url": body.base_url.strip(), "ai_model": body.model.strip(),
              "ai_judge_auto": "1" if body.judge_auto else ""}
    if body.api_key:                          # only overwrite when a new key is given
        values["ai_api_key"] = body.api_key.strip()
    db.set_user_settings(user["id"], values)
    return {"ok": True, "ai_configured": bool(db.ai_config_for(user["id"]))}


@router.get("/me/ai-prompts")
def get_ai_prompts(user: dict = Depends(auth.current_user)):
    """Every AI prompt the platform ships (analysis/prompts.py registry) with the user's
    current override, so prompts are inspectable and editable — never baked-in magic."""
    from ...analysis import prompts as prompt_reg
    mine = db.get_user_settings(user["id"])
    overrides = prompt_reg.parse_user_prompts(mine.get("ai_prompts", ""))
    return {"prompts": [
        {"name": name, "title": spec["title"], "desc": spec["desc"],
         "title_en": spec.get("title_en", spec["title"]),
         "desc_en": spec.get("desc_en", spec["desc"]),
         "default": spec["default"], "current": overrides.get(name, ""),
         "overridden": name in overrides}
        for name, spec in prompt_reg.PROMPTS.items()]}


class PromptBody(BaseModel):
    name: str
    text: str = ""                            # empty = reset to the builtin default


@router.put("/me/ai-prompts")
def put_ai_prompt(body: PromptBody, user: dict = Depends(auth.current_user)):
    """Save one prompt override (or reset it with text=""). Applies to THIS user only."""
    from ...analysis import prompts as prompt_reg
    if body.name not in prompt_reg.PROMPTS:
        raise HTTPException(404, "unknown prompt: %s" % body.name)
    mine = db.get_user_settings(user["id"])
    overrides = prompt_reg.parse_user_prompts(mine.get("ai_prompts", ""))
    if body.text.strip():
        overrides[body.name] = body.text
    else:
        overrides.pop(body.name, None)
    db.set_user_settings(user["id"], {"ai_prompts": json.dumps(overrides, ensure_ascii=False)})
    return {"ok": True, "overridden": bool(overrides)}


@router.patch("/password")
def change_password(body: PasswordBody, user: dict = Depends(auth.current_user)):
    if len(body.new_password or "") < 6:
        raise HTTPException(400, "password must be at least 6 characters")
    auth.change_own_password(user, body.old_password, body.new_password)
    return {"ok": True}
