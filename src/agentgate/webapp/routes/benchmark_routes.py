"""/api/v1/benchmarks: bank registry + case content + per-user overrides ("my way of running").

Two-layer level model on public banks (doc 33 §4 scenario 3): admins edit the bank itself
(case content / default level / online-offline); members only configure their own overrides
(per-case level + enabled), with one-click reset to defaults.
"""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...case.models import CASE_TYPES, Case, DiagnosisHint, Gold
from ...case.store import upsert_cases, update_levels, update_status, load_cases_db
from .. import auth, banks, db

router = APIRouter(prefix="/api/v1", tags=["benchmarks"])


class BenchmarkBody(BaseModel):
    name: str
    display_name: str = ""
    description_zh: str = ""
    description_en: str = ""
    source_note: str = ""
    category: str = ""
    default_level: str = "L2"
    visibility: str = "public"          # public (admin only) | private
    requirements: dict = {}             # {profile, tool_strict, materials} fairness contract


class BenchmarkPatch(BaseModel):
    display_name: Optional[str] = None
    description_zh: Optional[str] = None
    description_en: Optional[str] = None
    source_note: Optional[str] = None
    category: Optional[str] = None
    default_level: Optional[str] = None
    status: Optional[str] = None        # online | offline (public banks)
    requirements: Optional[dict] = None


class CaseBody(BaseModel):
    """Case v2 (doc 35 §4): query + level + gold; type may stay auto (inferred from gold);
    pack empty = inherit the bank's domain pack."""
    query: str
    case_id: str = ""
    level: str = "L2"
    type: str = "auto"
    pack: str = ""
    gold: dict = {}
    diagnosis_hint: dict = {}
    input_extra: dict = {}


class CasePatch(BaseModel):
    query: Optional[str] = None
    level: Optional[str] = None
    status: Optional[str] = None
    gold: Optional[dict] = None
    type: Optional[str] = None


class OverrideItem(BaseModel):
    case_id: str
    level: Optional[str] = None         # None = follow the bank default
    enabled: Optional[bool] = None


class OverridesBody(BaseModel):
    overrides: Optional[List[OverrideItem]] = None
    case_id: Optional[str] = None       # single-item form
    level: Optional[str] = None
    enabled: Optional[bool] = None


def _owner_name(bank: dict) -> str:
    u = db.get_user(bank["owner_id"]) if bank.get("owner_id") else None
    return u["username"] if u else ""


def _visible_banks(user: dict) -> List[dict]:
    """Public online banks for everyone (offline ones hidden below admin); private banks only
    for their creator and admins."""
    from ..auth import ROLE_RANK
    out = []
    for b in db.list_benchmarks():
        if b["visibility"] == "public":
            if b["status"] != "online" and ROLE_RANK.get(user["role"], -1) < ROLE_RANK["admin"]:
                continue
            out.append(b)
        elif b["owner_id"] == user["id"] or ROLE_RANK.get(user["role"], -1) >= ROLE_RANK["admin"]:
            out.append(b)
    return out


def _get_visible_bank(name: str, user: dict) -> dict:
    bank = db.get_benchmark(name)
    if not bank or bank not in _visible_banks(user):
        raise HTTPException(404, "benchmark not found: %s" % name)
    return bank


def _require_manage(bank: dict, user: dict):
    from ..auth import ROLE_RANK
    if bank["visibility"] == "public":
        if ROLE_RANK.get(user["role"], -1) < ROLE_RANK["admin"]:
            raise HTTPException(403, "public banks are managed by admins")
    elif bank["owner_id"] != user["id"] and ROLE_RANK.get(user["role"], -1) < ROLE_RANK["admin"]:
        raise HTTPException(403, "private banks are managed by their owner or admins")


def _bank_summary(bank: dict, user: dict) -> dict:
    path = banks.bank_db_path(bank, _owner_name(bank))
    dist = banks.bank_level_distribution(path)
    hist = db.bank_run_history(bank["name"], days=10)
    try:
        requirements = json.loads(bank.get("requirements") or "{}")
    except ValueError:
        requirements = {}
    return {
        "name": bank["name"], "display_name": bank["display_name"],
        "description_zh": bank["description_zh"], "description_en": bank["description_en"],
        "source_note": bank["source_note"], "category": bank["category"],
        "default_level": bank["default_level"], "visibility": bank["visibility"],
        "status": bank["status"], "owner_name": _owner_name(bank),
        "requirements": requirements,
        "case_count": sum(dist.values()), "level_dist": dist,
        "total_runs": hist["total_runs"], "runs_10d": len(hist["runs_recent"]),
        "recent_failures": hist["recent_failures"],
        "last_run": hist["runs_recent"][0] if hist["runs_recent"] else None,
    }


@router.get("/benchmarks")
def list_benchmarks(user: dict = Depends(auth.current_user),
                    mine: bool = False, category: str = ""):
    rows = [b for b in _visible_banks(user)
            if (not mine or b["owner_id"] == user["id"])
            and (not category or b["category"] == category)]
    return {"benchmarks": [_bank_summary(b, user) for b in rows]}


@router.post("/benchmarks")
def create_benchmark(body: BenchmarkBody, user: dict = Depends(auth.require_min("member"))):
    from ..auth import ROLE_RANK
    if body.visibility == "public" and ROLE_RANK.get(user["role"], -1) < ROLE_RANK["admin"]:
        raise HTTPException(403, "only admins can create public banks")
    if body.visibility not in ("public", "private"):
        raise HTTPException(400, "visibility must be public or private")
    try:
        banks.validate_bank_name(body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if body.default_level not in ("L0", "L1", "L2"):
        raise HTTPException(400, "default_level must be L0/L1/L2")
    if db.get_benchmark(body.name):
        raise HTTPException(409, "benchmark name already exists: %s" % body.name)
    bank = db.create_benchmark(
        name=body.name, visibility=body.visibility, owner_id=user["id"],
        display_name=body.display_name, description_zh=body.description_zh,
        description_en=body.description_en, source_note=body.source_note,
        category=body.category, default_level=body.default_level,
        requirements=json.dumps(body.requirements or {}, ensure_ascii=False))
    path = banks.bank_db_path(bank, user["username"])
    banks.create_bank_db(path)
    return _bank_summary(bank, user)


@router.get("/benchmarks/{name}")
def get_benchmark(name: str, user: dict = Depends(auth.current_user)):
    bank = _get_visible_bank(name, user)
    return _bank_summary(bank, user)


@router.patch("/benchmarks/{name}")
def patch_benchmark(name: str, body: BenchmarkPatch,
                    user: dict = Depends(auth.current_user)):
    bank = _get_visible_bank(name, user)
    _require_manage(bank, user)
    if body.status is not None:
        if bank["visibility"] != "public":
            raise HTTPException(400, "only public banks have an online/offline status")
        if body.status not in ("online", "offline"):
            raise HTTPException(400, "status must be online or offline")
    if body.default_level is not None and body.default_level not in ("L0", "L1", "L2"):
        raise HTTPException(400, "default_level must be L0/L1/L2")
    updated = db.update_benchmark(name, **{k: v for k, v in body.model_dump().items()
                                           if v is not None
                                           and (v != {} or k != "requirements")})
    if body.requirements is not None:
        updated = db.update_benchmark(
            name, requirements=json.dumps(body.requirements, ensure_ascii=False))
    return _bank_summary(updated, user)


@router.delete("/benchmarks/{name}")
def delete_benchmark(name: str, user: dict = Depends(auth.current_user)):
    bank = _get_visible_bank(name, user)
    _require_manage(bank, user)
    banks.delete_bank_dir(bank, _owner_name(bank))
    db.delete_benchmark(name)
    return {"ok": True}


# -- cases ---------------------------------------------------------------------

def _cases_with_overrides(bank: dict, user: dict, path) -> List[dict]:
    """Case rows for the detail page: default level + (public banks) my level / my enabled +
    the latest verdict each case got."""
    rows = load_cases_db(str(path), status=None)
    overrides = {}
    if bank["visibility"] == "public":
        overrides = {o["case_id"]: o for o in db.list_overrides(user["id"], bank["id"])}
    last_verdict = {}
    import sqlite3
    con = sqlite3.connect(str(db.platform_db()))
    con.row_factory = sqlite3.Row
    try:
        for r in con.execute(
                "SELECT ri.case_id, ri.verdict, ru.score FROM run_items ri "
                "JOIN runs ru ON ru.id = ri.run_id WHERE ri.bank=? AND ru.score IS NOT NULL "
                "ORDER BY ri.id DESC", (bank["name"],)):
            last_verdict.setdefault(r["case_id"], (r["verdict"], r["score"]))
    finally:
        con.close()
    out = []
    for c in rows:
        ov = overrides.get(c.case_id) or {}
        gold = c.gold
        out.append({
            "case_id": c.case_id, "level": c.level, "status": c.status,
            "query_preview": (c.input.get("query") or "")[:120],
            "type": c.type, "pack": c.pack, "effective_type": c.effective_type(),
            "gold": {"final": gold.final,
                     "checkpoints": [cp.model_dump() for cp in gold.checkpoints],
                     "rubric": [rp.model_dump() for rp in gold.rubric]},
            "my_level": ov.get("level"), "my_enabled": ov.get("enabled", 1),
            "last_verdict": (last_verdict.get(c.case_id) or (None, None))[0],
            "last_score": (last_verdict.get(c.case_id) or (None, None))[1],
        })
    return out


@router.get("/benchmarks/{name}/cases")
def list_cases(name: str, user: dict = Depends(auth.require_min("member"))):
    bank = _get_visible_bank(name, user)
    path = banks.bank_db_path(bank, _owner_name(bank))
    return {"cases": _cases_with_overrides(bank, user, path),
            "default_level": bank["default_level"], "visibility": bank["visibility"]}


@router.post("/benchmarks/{name}/cases")
def add_case(name: str, body: CaseBody, user: dict = Depends(auth.current_user)):
    bank = _get_visible_bank(name, user)
    _require_manage(bank, user)
    if not body.query.strip():
        raise HTTPException(400, "query must not be empty")
    if body.level not in ("L0", "L1", "L2"):
        raise HTTPException(400, "level must be L0/L1/L2")
    if body.type not in ("auto",) + CASE_TYPES:
        raise HTTPException(400, "type must be one of %s" % ", ".join(("auto",) + CASE_TYPES))
    path = banks.bank_db_path(bank, _owner_name(bank))
    existing = {c.case_id for c in load_cases_db(str(path), status=None)}
    cid = body.case_id.strip()
    if cid and cid in existing:
        raise HTTPException(409, "case_id already exists: %s" % cid)
    if not cid:
        seq = sum(1 for x in existing if x.startswith(bank["name"] + "-"))
        cid = "%s-%03d" % (bank["name"], seq + 1)
    case = Case(case_id=cid, suite=bank["name"], level=body.level,
                status="active", as_of=None, type=body.type, pack=body.pack,
                gold=Gold(**(body.gold or {})),
                diagnosis_hint=DiagnosisHint(**(body.diagnosis_hint or {})),
                input={"query": body.query, **(body.input_extra or {})})
    upsert_cases(str(path), [case])
    return {"case_id": cid, "effective_type": case.effective_type()}


@router.patch("/benchmarks/{name}/cases/{case_id}")
def patch_case(name: str, case_id: str, body: CasePatch,
               user: dict = Depends(auth.current_user)):
    bank = _get_visible_bank(name, user)
    _require_manage(bank, user)
    path = banks.bank_db_path(bank, _owner_name(bank))
    rows = load_cases_db(str(path), status=None)
    target = next((c for c in rows if c.case_id == case_id), None)
    if not target:
        raise HTTPException(404, "case not found: %s" % case_id)
    if body.level is not None:
        if body.level not in ("L0", "L1", "L2"):
            raise HTTPException(400, "level must be L0/L1/L2")
        update_levels(str(path), {case_id: body.level})
    if body.status is not None:
        if body.status not in ("active", "retired"):
            raise HTTPException(400, "status must be active or retired")
        update_status(str(path), {case_id: body.status})
    if body.query is not None:
        if not body.query.strip():
            raise HTTPException(400, "query must not be empty")
        target.input["query"] = body.query
        upsert_cases(str(path), [target])
    if body.type is not None or body.gold is not None:
        if body.type is not None:
            if body.type not in ("auto",) + CASE_TYPES:
                raise HTTPException(400, "type must be one of %s"
                                    % ", ".join(("auto",) + CASE_TYPES))
            target.type = body.type
        if body.gold is not None:
            target.gold = Gold(**body.gold)
        upsert_cases(str(path), [target])
    return {"ok": True}


# -- overview (the only page viewers can open for a bank) ------------------------

@router.get("/benchmarks/{name}/overview")
def benchmark_overview(name: str, user: dict = Depends(auth.current_user)):
    """Run history for one bank — visible to every role, listing everyone's runs (W6-4)."""
    bank = _get_visible_bank(name, user)
    hist = db.bank_run_history(bank["name"], days=10)
    runs = hist["runs_recent"]
    ok = sum(1 for r in runs if r["status"] == "succeeded"
             and not (r["gate_decision"] or "").startswith(("FAIL",)))
    return {
        "name": bank["name"], "display_name": bank["display_name"],
        "case_count": sum(banks.bank_level_distribution(
            banks.bank_db_path(bank, _owner_name(bank))).values()),
        "total_runs": hist["total_runs"], "runs_10d": len(hist["runs_recent"]),
        "recent_failures": hist["recent_failures"],
        "success_rate_10d": round(ok / len(runs), 4) if runs else None,
        "runs": runs,
    }


@router.delete("/benchmarks/{name}/cases/{case_id}")
def delete_case(name: str, case_id: str, user: dict = Depends(auth.current_user)):
    """Remove one case from the bank (edit page, admin/owner only)."""
    bank = _get_visible_bank(name, user)
    _require_manage(bank, user)
    path = banks.bank_db_path(bank, _owner_name(bank))
    import sqlite3
    con = sqlite3.connect(str(path))
    try:
        cur = con.execute("DELETE FROM cases WHERE case_id=?", (case_id,))
        con.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "case not found: %s" % case_id)
    finally:
        con.close()
    return {"ok": True}


# -- per-user per-bank edit drafts (uncommitted changes, survive page reloads) ----------------

@router.get("/benchmarks/{name}/edit-draft")
def get_edit_draft(name: str, user: dict = Depends(auth.current_user)):
    bank = _get_visible_bank(name, user)
    _require_manage(bank, user)
    return {"draft": db.get_edit_draft(user["id"], bank["id"])}


@router.put("/benchmarks/{name}/edit-draft")
def put_edit_draft(name: str, body: dict, user: dict = Depends(auth.current_user)):
    bank = _get_visible_bank(name, user)
    _require_manage(bank, user)
    if not isinstance(body, dict) or not isinstance(body.get("draft"), dict):
        raise HTTPException(400, "body must be {draft: {...}}")
    db.put_edit_draft(user["id"], bank["id"], body["draft"])
    return {"ok": True}


@router.delete("/benchmarks/{name}/edit-draft")
def delete_edit_draft(name: str, user: dict = Depends(auth.current_user)):
    bank = _get_visible_bank(name, user)
    _require_manage(bank, user)
    return {"deleted": db.delete_edit_draft(user["id"], bank["id"])}


# -- domain pack (doc 35 §4.2: bank-level judging config, inherited by all cases) --------

@router.get("/benchmarks/{name}/pack")
def get_pack(name: str, user: dict = Depends(auth.current_user)):
    bank = _get_visible_bank(name, user)
    try:
        requirements = json.loads(bank.get("requirements") or "{}")
    except ValueError:
        requirements = {}
    from ...case import packs as pack_reg
    pid = pack_reg.pack_for_bank(bank["name"], requirements, db.data_root())
    pack = pack_reg.load_pack(pid, db.data_root()) or pack_reg.DEFAULT_GENERIC
    return {"pack_id": pid, "pack": pack, "bank_requirements": requirements,
            "editable": _can_manage(name, user)}


def _can_manage(name: str, user: dict) -> bool:
    try:
        bank = _get_visible_bank(name, user)
        _require_manage(bank, user)
        return True
    except HTTPException:
        return False


@router.put("/benchmarks/{name}/pack")
def put_pack(name: str, body: dict, user: dict = Depends(auth.current_user)):
    """Save a (human-reviewed) pack into the data dir and bind it to the bank."""
    bank = _get_visible_bank(name, user)
    _require_manage(bank, user)
    pack = body.get("pack") if isinstance(body, dict) else None
    if not isinstance(pack, dict) or not pack.get("pack_id"):
        raise HTTPException(400, "body must be {pack: {..., pack_id}}")
    from ...case import packs as pack_reg
    pack.setdefault("authored_by", body.get("authored_by", "human"))
    pack_reg.save_pack(pack, db.data_root())
    try:
        requirements = json.loads(bank.get("requirements") or "{}")
    except ValueError:
        requirements = {}
    requirements["pack"] = pack["pack_id"]
    db.update_benchmark(name, requirements=json.dumps(requirements, ensure_ascii=False))
    return {"ok": True, "pack_id": pack["pack_id"]}


class AIRubricBody(BaseModel):
    query: str
    answer: str                                # the gold answer text to decompose


@router.post("/benchmarks/{name}/ai-rubric")
def ai_rubric(name: str, body: AIRubricBody, user: dict = Depends(auth.current_user)):
    """AI decomposes a gold answer into checkable rubric points (draft only — the author
    confirms every point in the case form before saving; the LLM never invents facts)."""
    bank = _get_visible_bank(name, user)
    _require_manage(bank, user)
    if not body.answer.strip():
        raise HTTPException(400, "answer (gold) must not be empty")
    from ...analysis import ai_client
    d = ai_client.draft_rubric(_user_ai(user), body.query, body.answer)
    if not d:
        raise HTTPException(502, "AI rubric draft failed (check the User Center AI config / gateway)")
    return {"rubric": d["rubric"]}


class AIDraftPackBody(BaseModel):
    description: str
    samples: str = ""                          # 3~5 sample cases incl. a material excerpt


class AIDraftCaseBody(BaseModel):
    query: str
    material: str = ""                         # material/dataset gold excerpt — the gold source


def _user_ai(user: dict) -> dict:
    cfg = db.ai_config_for(user["id"])
    if not cfg:
        raise HTTPException(400, "AI not configured: set base_url/api_key/model in User Center")
    return cfg


@router.post("/benchmarks/{name}/ai-draft-pack")
def ai_draft_pack(name: str, body: AIDraftPackBody, user: dict = Depends(auth.current_user)):
    """LLM drafts a pack JSON (35 §4.2.1 source 3). Draft only — the reviewer saves it via
    PUT pack, which stamps authored_by=llm-draft+human-reviewed."""
    bank = _get_visible_bank(name, user)
    _require_manage(bank, user)
    from ...analysis import ai_client
    draft = ai_client.draft_domain_pack(_user_ai(user), body.description, body.samples)
    if not draft:
        raise HTTPException(502, "AI draft failed (check the User Center AI config / gateway)")
    return {"draft": draft}


@router.post("/benchmarks/{name}/ai-draft-case")
def ai_draft_case(name: str, body: AIDraftCaseBody, user: dict = Depends(auth.current_user)):
    """Auto-type + draft structured gold from the given material (35 §4.1.1). The LLM only
    structures what the material states; the author confirms each item before saving."""
    bank = _get_visible_bank(name, user)
    _require_manage(bank, user)
    from ...case import packs as pack_reg
    from ...analysis import ai_client
    try:
        requirements = json.loads(bank.get("requirements") or "{}")
    except ValueError:
        requirements = {}
    pack = pack_reg.load_pack(pack_reg.pack_for_bank(bank["name"], requirements),
                              db.data_root()) or {}
    draft = ai_client.draft_case_gold(_user_ai(user), body.query, body.material,
                                      "；".join(pack.get("judge_hints") or []))
    if not draft:
        raise HTTPException(502, "AI draft failed (check the User Center AI config / gateway)")
    return {"draft": draft}


# -- my overrides (public banks, members+) ----------------------------------------

def _overrides_bank(name: str, user: dict) -> dict:
    bank = _get_visible_bank(name, user)
    if bank["visibility"] != "public":
        raise HTTPException(400, "private banks have no per-user overrides; edit cases directly")
    return bank


@router.get("/benchmarks/{name}/my-overrides")
def get_my_overrides(name: str, user: dict = Depends(auth.require_min("member"))):
    bank = _overrides_bank(name, user)
    return {"overrides": db.list_overrides(user["id"], bank["id"]),
            "default_level": bank["default_level"]}


@router.put("/benchmarks/{name}/my-overrides")
def put_my_overrides(name: str, body: OverridesBody,
                     user: dict = Depends(auth.require_min("member"))):
    bank = _overrides_bank(name, user)
    items = body.overrides if body.overrides is not None else \
        [OverrideItem(case_id=body.case_id or "", level=body.level, enabled=body.enabled)]
    items = [i for i in items if i.case_id]
    if not items:
        raise HTTPException(400, "no overrides given")
    path = banks.bank_db_path(bank, _owner_name(bank))
    existing = {c.case_id for c in load_cases_db(str(path), status=None)}
    for i in items:
        if i.case_id not in existing:
            raise HTTPException(404, "case not found: %s" % i.case_id)
        if i.level is not None and i.level not in ("L0", "L1", "L2"):
            raise HTTPException(400, "level must be L0/L1/L2 or null")
        db.upsert_override(user["id"], bank["id"], i.case_id, level=i.level,
                           enabled=None if i.enabled is None else int(i.enabled))
    return {"overrides": db.list_overrides(user["id"], bank["id"])}


@router.delete("/benchmarks/{name}/my-overrides")
def clear_my_overrides(name: str, user: dict = Depends(auth.require_min("member"))):
    """One-click reset to defaults: drop all my overrides in this bank."""
    bank = _overrides_bank(name, user)
    n = db.clear_overrides(user["id"], bank["id"])
    return {"cleared": n}
