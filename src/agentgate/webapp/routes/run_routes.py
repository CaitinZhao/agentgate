"""/api/v1/runs: enqueue evaluations, watch progress, cancel, fetch bilingual reports/artifacts.

Task names are made unique by appending a timestamp suffix (doc 33 WD5-11): the user only
types a business name; runs.name carries the unique form and runs.user_name the original for
prefix search. Members see only their own runs; admins/owners see everyone's.
"""
import datetime
import json
import re
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from .. import auth, banks, db
from .. import ai_judge_jobs
from ..worker import VALID_LEVELS, resolve_run_cases

router = APIRouter(prefix="/api/v1", tags=["runs"])


class RunBank(BaseModel):
    bank: str
    levels: List[str] = []


class CreateRunBody(BaseModel):
    task_name: str = ""                   # empty -> "Untitled run" default
    banks: List[RunBank]
    target_url: str
    case_ids: Optional[List[str]] = None
    proxy_enabled: bool = True   # message-level recording default ON (no-op when proxy_agent_url unset)
    ai_assist: bool = True       # per-user AI enhancements during the run (judge suggestions /
                                 # failure root causes / report summary); needs User-Center AI
    scheduled_for: Optional[str] = None       # ISO datetime; None = immediate
    stability_k: int = 1                      # doc 35 C4: pass^k repeats, 1 = off


_ASI_DISPLAY = [
    ("全部通过：free_text 题金标命中", "通过：最终答案与标准答案（金标）一致"),
    ("[final_answer] 终答错误；检查点命中", "最终答案与标准答案不一致；中间步骤对了"),
    ("[final_answer] 中间步骤全部正确", "中间步骤全部正确，错在最终答案"),
    ("[skip_answer]", "跳步直答：未调用工具就给了答案（题目要求先检索/取证）"),
    ("[loop] 疑似循环：", "疑似循环：同一请求反复发出（见详情）"),
    ("[broken_chain]", "轨迹断链：部分执行记录缺失父节点（见详情）"),
    ("[denied] 被红线拦截的调用：", "触发了违禁操作（被拦截）："),
    ("[model_errors]", "模型调用出错"),
    ("需 L3 judge 或人工复核", "无法自动判定，需要 AI judge 或人工复核"),
    ("[safety:", "安全违规："),
    ("[native:", "未通过该数据集的原生口径判定："),
]


_ASI_DISPLAY_EN = [
    # current template (build_asi v2)
    ("通过：答案与标准答案一致", "Pass: answer matches the gold"),
    ("失败：答案与标准答案不一致", "Fail: answer differs from the gold"),
    ("无法自动判定（", "Cannot auto-judge ("),
    ("题无确定性金标）", " - no deterministic gold)"),
    ("评分点", "rubric:"), ("条待比对", " points pending"), ("需 AI judge 或人工复核", "needs AI judge or human review"),
    ("未调用工具（跳步直答）", "no tool calls (skipped retrieval)"),
    ("工具调用正确", "tool calls correct"),
    ("工具调用不完整/不符", "tool calls incomplete/mismatched"),
    ("无工具调用", "no tool calls"),
    ("工具调用", "tool calls"),
    ("检查点通过", "checkpoints"),
    ("[隐患] ", "[Caveat] "), ("未附证据引用", "no evidence citation"),
    ("未声明口径", "no caliber statement"), ("数值贴近容差边缘", "value near tolerance edge"),
    ("[安全] ", "[Safety] "),
    # legacy strings (runs judged by earlier templates)
    ("全部通过：free_text 题金标命中", "Pass: answer matches the gold"),
    ("[final_answer] 终答错误；检查点命中", "Final answer differs; checkpoints hit"),
    ("[final_answer] 中间步骤全部正确", "all intermediate steps correct, final answer wrong"),
    ("[skip_answer] ", "[skip-answer] "),
    ("[loop] 疑似循环：", "[loop] "),
    ("[broken_chain] ", "[broken-chain] "),
    ("[denied] 被红线拦截的调用：", "[denied] blocked calls: "),
    ("需 L3 judge 或人工复核", "needs AI judge or human review"),
    ("[safety:", "[safety:"),
    ("[native:", "[native:"),
]

_REASON_EN = [
    (r"期望 (.*?)±(.*?)%，实际 (.*)", r"expected ±%, got "),
    (r"期望 (.*?)±(.*?)，实际 (.*)", r"expected ±, got "),
    (r"期望 (.*?)，实际 (.*)", r"expected , got "),
    (r"未按格式输出 FINAL json（缺 value/answer）", "FINAL json missing value/answer"),
    (r"FINAL 数值缺失或非数值", "FINAL value missing or not numeric"),
    (r"gold 缺 boolean 答案（yes/no）", "gold has no boolean answer (yes/no)"),
    (r"答复未包含金标答案 (.*)", r"answer misses the gold "),
    (r"未按要求明说未披露/无法确定", "did not state non-disclosure as required"),
    (r"环境终态断言失败 (.*)", r"env state assertion failed: "),
    (r"原生口径判定失败：(.*)", r"native reading failed: "),
    (r"领域要求证据引用，FINAL.evidence 缺失", "pack requires evidence; FINAL.evidence missing"),
    (r"口径不符：basis=(.*)，许可口径 (.*)", r"caliber mismatch: basis=, allowed "),
    (r"领域要求口径声明，FINAL.basis 缺失", "pack requires caliber; FINAL.basis missing"),
    (r"领域默认键 (.*) 缺失", r"pack default key  missing"),
    (r"SQL 执行错误: (.*)", r"SQL execution error: "),
    (r"期望恰好 1 次调用，实际 (\d+) 次", r"expected exactly 1 call, got "),
    (r"期望 (\d+) 次调用，实际 (\d+) 次", r"expected  calls, got "),
    (r"有期望调用未被匹配", "an expected call was not matched"),
    (r"FINAL 未给出函数调用 JSON", "FINAL has no function-call JSON"),
    (r"FINAL 未给出 SQL", "FINAL has no SQL"),
    (r"sqlite 未随库分发: (.*)", r"sqlite not shipped with the bank: "),
    (r"FINAL 未给出 doc_ids 排序", "FINAL has no doc_ids ranking"),
    (r"agent 未回传 agentdojo 原生条件（需 vendored 环境）", "agent returned no agentdojo conditions (vendored env required)"),
    (r"无拒答模式命中且未启用 AI judge", "no refusal pattern and no AI judge"),
    (r"gold 缺 answer", "gold missing answer"),
    (r"gold 缺 db/sql", "gold missing db/sql"),
    (r"gold 缺 qrels", "gold missing qrels"),
    (r"函数名不匹配", "function name mismatch"),
    (r"无答复", "no answer"),
]


_REASON_ZH = [
    (r"expected (.*?)±(.*?)%, got (.*)", r"期望 \1±\2%，实际 \3"),
    (r"expected (.*?)±(.*?), got (.*)", r"期望 \1±\2，实际 \3"),
    (r"expected (.*?), got (.*)", r"期望 \1，实际 \2"),
    (r"FINAL json missing value/answer", "未按格式输出 FINAL json（缺 value/answer）"),
    (r"FINAL value missing or not numeric", "FINAL 数值缺失或非数值"),
    (r"gold has no boolean answer \(yes/no\)", "gold 缺 boolean 答案（yes/no）"),
    (r"answer misses the gold (.*)", r"答复未包含金标答案 \1"),
    (r"did not state non-disclosure as required", "未按要求明说未披露/无法确定"),
    (r"env state assertion failed: (.*)", r"环境终态断言失败 \1"),
    (r"native reading failed: (.*)", r"原生口径判定失败：\1"),
    (r"pack requires evidence; FINAL.evidence missing", "领域要求证据引用，FINAL.evidence 缺失"),
    (r"caliber mismatch: basis=(.*), allowed (.*)", r"口径不符：basis=\1，许可口径 \2"),
    (r"pack requires a caliber statement but FINAL.basis is missing", "领域要求口径声明，FINAL.basis 缺失"),
    (r"pack default key (.*) missing", r"领域默认键 \1 缺失"),
    (r"SQL execution error: (.*)", r"SQL 执行错误：\1"),
    (r"expected exactly 1 call, got (\d+)", r"期望恰好 1 次调用，实际 \1 次"),
    (r"expected (\d+) calls, got (\d+)", r"期望 \1 次调用，实际 \2 次"),
    (r"an expected call was not matched", "有期望调用未被匹配"),
    (r"FINAL has no function-call JSON", "FINAL 未给出函数调用 JSON"),
    (r"FINAL has no SQL", "FINAL 未给出 SQL"),
    (r"sqlite not shipped with the bank: (.*)", r"sqlite 未随库分发：\1"),
    (r"FINAL has no doc_ids ranking", "FINAL 未给出 doc_ids 排序"),
    (r"agent returned no agentdojo conditions \(vendored env required\)", "agent 未回传 agentdojo 原生条件（需 vendored 环境）"),
    (r"gold missing answer", "gold 缺 answer"),
    (r"gold missing db/sql", "gold 缺 db/sql"),
    (r"gold missing qrels", "gold 缺 qrels"),
    (r"function name mismatch", "函数名不匹配"),
    (r"no answer", "无答复"),
]


def _reason_display(reason: str, lang: str = "zh") -> str:
    """Same dual-direction convention as _asi_display (English canonical, CJK detection)."""
    text = reason or ""
    cjk = _has_cjk(text)
    if lang == "en":
        if cjk:
            for pat, rep in _REASON_EN:
                text = re.sub(pat, rep, text)
            text = text.replace("（", " (").replace("）", ")")
        return text
    if cjk:
        return text
    for pat, rep in _REASON_ZH:
        text = re.sub(pat, rep, text)
    return text


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in (s or ""))


# English-canonical ASI -> Chinese display (new runs store English)
_ASI_DISPLAY_ZH = [
    ("Pass: answer matches the gold", "通过：答案与标准答案一致"),
    ("Fail: answer differs from the gold", "失败：答案与标准答案不一致"),
    ("Cannot auto-judge (", "无法自动判定（"),
    ("has no deterministic gold)", "题无确定性金标）"),
    ("no tool calls (skipped retrieval)", "未调用工具（跳步直答）"),
    ("tool calls correct", "工具调用正确"),
    ("tool calls incomplete/mismatched", "工具调用不完整/不符"),
    ("no tool calls", "无工具调用"),
    ("tool calls", "工具调用"),
    ("checkpoints", "检查点通过"),
    ("rubric points to compare", "个评分点待比对"),
    ("needs AI judge or human review", "需 AI judge 或人工复核"),
    ("[Caveat] ", "[隐患] "), ("no evidence citation", "未附证据引用"),
    ("no caliber statement", "未声明口径"), ("value near tolerance edge", "数值贴近容差边缘"),
    ("[Safety] ", "[安全] "),
]


def _asi_display(asi: str, lang: str = "zh") -> str:
    """Human-readable one-liner for the items table. Stored ASI text is
    English-canonical (new runs) or Chinese (legacy runs); display in the requested
    language, detected by CJK presence. The raw ASI (with structured evidence) stays
    available in the per-case detail endpoint."""
    text = asi or ""
    cjk = _has_cjk(text)
    if lang == "en":
        if cjk:                                   # legacy Chinese runs
            for raw, human in _ASI_DISPLAY_EN:
                text = text.replace(raw, human)
            text = text.replace("（", " (").replace("）", ")")
        return text
    if cjk:                                       # legacy Chinese runs
        for raw, human in _ASI_DISPLAY:
            text = text.replace(raw, human)
        return text
    for raw, human in _ASI_DISPLAY_ZH:            # English-canonical runs
        text = text.replace(raw, human)
    return text


def _run_view(run: dict, user: dict) -> dict:
    out = dict(run)
    out["bank_filter"] = json.loads(run.get("bank_filter") or "[]")
    out["queue_position"] = db.queue_position(run["id"]) if run["status"] == "queued" else None
    mine = run["created_by"] == user["id"]
    out["mine"] = mine
    creator = db.get_user(run["created_by"]) if run.get("created_by") else None
    out["creator"] = creator["username"] if creator else run.get("user_name") or ""
    return out


def _get_own_or_admin_run(run_id: str, user: dict) -> dict:
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found: %s" % run_id)
    from ..auth import ROLE_RANK
    if (run["created_by"] != user["id"]
            and ROLE_RANK.get(user["role"], -1) < ROLE_RANK["admin"]):
        raise HTTPException(404, "run not found: %s" % run_id)   # no existence leak
    return run


class ProbeBody(BaseModel):
    target_url: str


@router.post("/probe-agent")
def probe_agent_endpoint(body: ProbeBody, user: dict = Depends(auth.require_min("member"))):
    """Pre-flight contract check for a target agent (the run-create page's connection
    test): /health + /capabilities touches and one synthetic /invoke round-trip validated
    against the invoke contract. Pure diagnostics — changes nothing on either side."""
    from ...control.probe import probe_agent
    return probe_agent(body.target_url)


@router.post("/runs")
def create_run(body: CreateRunBody, user: dict = Depends(auth.require_min("member"))):
    task_name = (body.task_name or "").strip() or "Untitled run"    # default when left empty
    if len(task_name) > 80:
        raise HTTPException(400, "task_name must be 1-80 characters")
    if not body.banks:
        raise HTTPException(400, "select at least one bank")
    if not (body.target_url.startswith("http://") or body.target_url.startswith("https://")):
        raise HTTPException(400, "target_url must be an http(s) URL")
    scheduled_for = None
    if body.scheduled_for:
        try:
            scheduled = datetime.datetime.fromisoformat(body.scheduled_for)
        except ValueError:
            raise HTTPException(400, "scheduled_for must be an ISO datetime (YYYY-MM-DDTHH:MM)")
        if scheduled < datetime.datetime.now() - datetime.timedelta(minutes=1):
            raise HTTPException(400, "scheduled_for is in the past")
        scheduled_for = scheduled.isoformat(timespec="seconds")

    bank_filter = []
    from ..auth import ROLE_RANK
    for rb in body.banks:
        bank = db.get_benchmark(rb.bank)
        rank = ROLE_RANK.get(user["role"], -1)
        if bank and bank["visibility"] == "public":
            # offline public banks stay hidden from members; admins may still target them (rejected below)
            visible = bank["status"] == "online" or rank >= ROLE_RANK["admin"]
        elif bank:
            visible = bank["owner_id"] == user["id"] or rank >= ROLE_RANK["admin"]
        else:
            visible = False
        if not visible:
            raise HTTPException(404, "benchmark not visible: %s" % rb.bank)
        if bank["status"] != "online":
            raise HTTPException(400, "benchmark is offline: %s" % rb.bank)
        bad = [l for l in rb.levels if l not in VALID_LEVELS]
        if bad:
            raise HTTPException(400, "invalid levels %s (use L0/L1/L2)" % bad)
        bank_filter.append({"bank": rb.bank, "levels": rb.levels})

    # dry-resolve the case set now for the live count + ETA (re-resolved at execution time);
    # the probe lacks target_url so the capability probe is skipped here (re-checked at run)
    probe = {"created_by": user["id"], "bank_filter": bank_filter,
             "case_ids": json.dumps(body.case_ids) if body.case_ids else None}
    cases, _, _, _ = resolve_run_cases(probe)
    if not cases:
        raise HTTPException(400, "no runnable cases after filters (levels/overrides/case_ids)")
    est = int(len(cases) * db.avg_case_seconds())

    stability_k = max(1, min(int(body.stability_k or 1), 5))
    if stability_k > 1:
        est = int(est * stability_k)
    unique_name = db.unique_run_name(task_name)
    run = db.create_run(name=unique_name, user_name=task_name, created_by=user["id"],
                        bank_filter=bank_filter, target_url=body.target_url,
                        proxy_enabled=body.proxy_enabled,
                        receiver_port=db.get_int_setting("receiver_port", 4318),
                        case_ids=body.case_ids, scheduled_for=scheduled_for,
                        total_cases=len(cases), est_duration_s=est,
                        stability_k=stability_k, ai_assist=body.ai_assist)
    return {"id": run["id"], "name": run["name"], "total_cases": len(cases),
            "est_duration_s": est, "queue_position": db.queue_position(run["id"])}


@router.get("/runs")
def list_runs(user: dict = Depends(auth.require_min("member")),
              name: str = "", bank: str = "", status: str = "",
              user_name: str = "", limit: int = 100):
    """Run history / queue. Members are pinned to their own runs; admins/owners can filter
    by executor (user_name). name matches both the unique name and the business name."""
    from ..auth import ROLE_RANK
    created_by = None
    if ROLE_RANK.get(user["role"], -1) < ROLE_RANK["admin"]:
        created_by = user["id"]
    elif user_name:
        target = db.get_user_by_name(user_name)
        created_by = target["id"] if target else -1
    rows = db.list_runs(created_by=created_by, name_like=name, bank=bank,
                        status=status, limit=max(1, min(limit, 500)))
    return {"runs": [_run_view(r, user) for r in rows]}


@router.get("/runs/{run_id}")
def get_run(run_id: str, user: dict = Depends(auth.require_min("member"))):
    run = _get_own_or_admin_run(run_id, user)
    out = _run_view(run, user)
    items = db.list_run_items(run_id)
    # AI judge suggestions (from judge.jsonl) attached to their items
    try:
        jp = Path(run["result_dir"]) / "judge.jsonl"
        sug = {}
        if jp.is_file():
            for line in jp.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    sug[d["case_id"]] = d
        for it in items:
            if it["verdict"] == "PENDING" and it["case_id"] in sug:
                it["judge_suggestion"] = sug[it["case_id"]]
    except Exception:
        pass
    # per-case history within the same banks (runs / passes across all runs)
    hist = db.case_history([i["case_id"] for i in items])
    for it in items:
        h = hist.get(it["case_id"]) or {"runs": 0, "pass": 0}
        it["history_runs"], it["history_pass"] = h["runs"], h["pass"]
    for it in items:
        it["asi_display"] = _asi_display(it.get("asi") or "")
        it["asi_display_en"] = _asi_display(it.get("asi") or "", lang="en")
        sug_i = it.get("judge_suggestion") or {}
        if sug_i.get("coverage") is not None:
            cov = sug_i["coverage"]
            it["asi_display"] += "；评分点覆盖 %d/%d" % (
                sum(1 for c in cov if c.get("hit")), len(cov))
    out["items"] = items
    out["final_gate"] = run.get("final_gate")
    return out


class ReviewBody(BaseModel):
    case_id: str
    verdict: str                                  # PASS | FAIL (human final ruling)
    note: str = ""


@router.get("/runs/{run_id}/cases/{case_id}/detail")
def case_detail(run_id: str, case_id: str, user: dict = Depends(auth.require_min("member"))):
    """Everything a human reviewer needs for ONE case in one call: the question (input),
    the agent's full answer, the judgment, the gold (standard answer) and the trajectory
    evidence (tool-call sequence). Backs the per-case detail dialog in the run page."""
    run = _get_own_or_admin_run(run_id, user)
    items = {i["case_id"]: i for i in db.list_run_items(run_id)}
    item = items.get(case_id)
    if not item:
        raise HTTPException(404, "case not in this run: %s" % case_id)
    out = {
        "case_id": case_id, "bank": item["bank"], "level": item["level"],
        "verdict": item["verdict"], "final_verdict": item.get("final_verdict"),
        "reviewed_by": item.get("reviewed_by"), "review_note": item.get("review_note"),
        "tokens": item["tokens"], "wall_time_s": item["wall_time_s"],
        "asi": item.get("asi") or "", "asi_display": _asi_display(item.get("asi") or ""),
        "asi_display_en": _asi_display(item.get("asi") or "", lang="en"),
    }
    # the agent's full answer + final json (run artifact)
    ap = Path(run["result_dir"] or "") / "answers.jsonl"
    if ap.is_file():
        for line in ap.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                if d.get("case_id") == case_id:
                    out["answer"] = d.get("answer_text", "")
                    out["final_json"] = d.get("final_json") or {}
                    break
    # trajectory evidence: tool-call sequence / denied / model / trace id (runs_meta)
    mp = Path(run["result_dir"] or "") / "runs_meta.json"
    if mp.is_file():
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
        except ValueError:
            meta = []
        if isinstance(meta, dict):
            meta = meta.get("runs") or []
        for m in meta:
            if m.get("task_id") == case_id:
                out["tool_calls"] = m.get("tool_calls") or []
                out["denied_tools"] = m.get("denied_tools") or []
                out["model"] = m.get("model", "")
                out["trace_id"] = m.get("trace_id", "")
                break
    # the case itself (question + gold) from its bank
    bank = db.get_benchmark(item["bank"])
    if bank:
        owner = db.get_user(bank["owner_id"]) if bank.get("owner_id") else None
        path = banks.bank_db_path(bank, owner["username"] if owner else "")
        try:
            from ...case.store import load_cases_db
            case = next((c for c in load_cases_db(str(path), status=None)
                         if c.case_id == case_id), None)
            if case:
                inp = dict(case.input or {})
                ctx = str(inp.pop("context", "") or "")
                out["query"] = str(inp.pop("query", "") or "")
                out["input_extra"] = {k: (v if isinstance(v, (str, int, float, bool))
                                          else str(v)[:200]) for k, v in inp.items()}
                out["context_chars"] = len(ctx)
                out["context_head"] = ctx[:400]
                out["gold"] = {"final": case.gold.final,
                               "checkpoints": [cp.model_dump() for cp in case.gold.checkpoints],
                               "rubric": [rp.model_dump() for rp in case.gold.rubric]}
                out["case_type"] = case.effective_type()
        except Exception:
            pass
    # structured per-check details (what exactly passed/failed) from the archived contract
    ep = Path(run["result_dir"] or "") / "eval_results.json"
    if ep.is_file():
        try:
            for r_ in json.loads(ep.read_text(encoding="utf-8")):
                if r_.get("task_id") == case_id:
                    out["checks"] = [
                        {"name": c.get("name"), "ok": c.get("ok"),
                         "reason": _reason_display(c.get("reason") or "", lang="zh"),
                         "reason_en": _reason_display(c.get("reason") or "", lang="en"),
                         "asi": c.get("asi") or ""}
                        for c in (r_.get("evaluator_results") or [])]
                    el = r_.get("error_localization")
                    if el:
                        out["error_localization"] = el
                    break
        except (ValueError, OSError):
            pass
    # AI judge suggestion (if the run had one)
    jp = Path(run["result_dir"] or "") / "judge.jsonl"
    if jp.is_file():
        for line in jp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                if d.get("case_id") == case_id:
                    out["judge_suggestion"] = d
                    break
    return out


def _ai_judge_one(run: dict, case_id: str, cfg: dict, user: dict) -> dict:
    """Judge ONE PENDING case post-hoc (suggestion only); appends to judge.jsonl so the
    run detail picks it up as the reviewer's decision aid."""
    item = next((i for i in db.list_run_items(run["id"]) if i["case_id"] == case_id), None)
    if not item:
        raise HTTPException(404, "case not in this run: %s" % case_id)
    if item["verdict"] != "PENDING":
        raise HTTPException(400, "case is %s — only PENDING cases need judging" % item["verdict"])
    bank = db.get_benchmark(item["bank"])
    if not bank:
        raise HTTPException(404, "bank not found: %s" % item["bank"])
    from .benchmark_routes import _get_visible_bank, _require_manage
    _require_manage(_get_visible_bank(bank["name"], user), user)
    owner = db.get_user(bank["owner_id"]) if bank.get("owner_id") else None
    path = banks.bank_db_path(bank, owner["username"] if owner else "")
    from ...case.store import load_cases_db
    case = next((c for c in load_cases_db(str(path), status=None)
                 if c.case_id == case_id), None)
    if not case:
        raise HTTPException(404, "case not found in bank: %s" % case_id)
    # the archived answer stands in for the live response; process evidence from runs_meta
    response, tool_calls = {}, []
    ap = Path(run["result_dir"] or "") / "answers.jsonl"
    if ap.is_file():
        for line in ap.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                if d.get("case_id") == case_id:
                    response = {"answer_text": d.get("answer_text", ""),
                                "final_json": d.get("final_json") or {}}
                    break
    mp = Path(run["result_dir"] or "") / "runs_meta.json"
    if mp.is_file():
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                meta = meta.get("runs") or []
            m = next((m for m in meta if m.get("task_id") == case_id), None)
            tool_calls = (m or {}).get("tool_calls") or []
        except ValueError:
            pass
    judge_out = {"process": {"n_tool_calls": len(tool_calls)}}
    from ...case import packs as pack_reg
    pack = pack_reg.resolve_pack(case, data_root=db.data_root())
    from ...analysis import ai_client
    sug = ai_client.judge_suggestion(cfg, case, pack, response, judge_out)
    if not sug:
        raise HTTPException(502, "AI judge failed (check the User Center AI config / gateway)")
    sug.update({"case_id": case_id, "adopted": False, "post_hoc": True})
    jp = Path(run["result_dir"] or "") / "judge.jsonl"
    with jp.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(sug, ensure_ascii=False) + "\n")
    return sug


@router.post("/runs/{run_id}/cases/{case_id}/ai-judge")
def ai_judge_case(run_id: str, case_id: str, user: dict = Depends(auth.require_min("member"))):
    """Post-hoc AI judge for one PENDING case (suggestion only): the run may have finished
    before the reviewer's AI config existed, leaving free_text cases stuck in PENDING —
    this gives the reviewer a judge suggestion to accept or reject in the final ruling."""
    run = _get_own_or_admin_run(run_id, user)
    if run["status"] != "succeeded":
        raise HTTPException(400, "run not finished: %s" % run["status"])
    cfg = db.ai_config_for(user["id"])
    if not cfg:
        raise HTTPException(400, "AI not configured: set base_url/api_key/model in User Center")
    sug = _ai_judge_one(run, case_id, cfg, user)
    return {"suggestion": sug, "case_id": case_id}


class BatchJudgeBody(BaseModel):
    limit: int = 30                             # max PENDING cases per job (snapshot)


@router.post("/runs/{run_id}/ai-judge-batch")
def ai_judge_batch(run_id: str, body: BatchJudgeBody,
                   user: dict = Depends(auth.require_min("member"))):
    """Batch post-hoc AI judging, ASYNC: validates and enqueues a background job that
    judges a snapshot of PENDING case ids (suggestions appended to judge.jsonl); the
    frontend polls GET /runs/{run_id}/ai-judge-jobs/latest until status=done. Human
    final ruling still decides every verdict."""
    run = _get_own_or_admin_run(run_id, user)
    if run["status"] != "succeeded":
        raise HTTPException(400, "run not finished: %s" % run["status"])
    cfg = db.ai_config_for(user["id"])
    if not cfg:
        raise HTTPException(400, "AI not configured: set base_url/api_key/model in User Center")
    latest = ai_judge_jobs.latest_for_run(run_id)
    if latest and latest["status"] == "running":
        return {"job_id": latest["id"], "queued": latest["total"] - latest["done"],
                "remaining": latest["remaining"], "already_running": True}
    limit = max(1, min(int(body.limit or 30), 100))
    pending = [i["case_id"] for i in db.list_run_items(run_id) if i["verdict"] == "PENDING"]
    todo = pending[:limit]
    if not todo:
        return {"job_id": None, "queued": 0, "remaining": 0, "already_running": False}

    def judge_fn(case_id: str) -> Dict:
        return _ai_judge_one(run, case_id, cfg, user)

    job_id = ai_judge_jobs.enqueue(run_id, todo, judge_fn)
    return {"job_id": job_id, "queued": len(todo),
            "remaining": max(len(pending) - len(todo), 0), "already_running": False}


@router.get("/runs/{run_id}/ai-judge-jobs/latest")
def ai_judge_batch_latest(run_id: str, user: dict = Depends(auth.require_min("member"))):
    """Poll target for the async batch: progress of the run's latest job (or none)."""
    _get_own_or_admin_run(run_id, user)
    st = ai_judge_jobs.latest_for_run(run_id)
    return st or {"status": "none", "done": 0, "total": 0, "judged": [], "failed": [],
                  "remaining": 0}


@router.post("/ai-judge-jobs/{job_id}/cancel")
def ai_judge_batch_cancel(job_id: str, user: dict = Depends(auth.require_min("member"))):
    """Stop a running batch between cases (already-judged suggestions stay)."""
    return {"ok": ai_judge_jobs.cancel(job_id)}


@router.post("/runs/{run_id}/review")
def review_item(run_id: str, body: ReviewBody,
                user: dict = Depends(auth.require_min("member"))):
    """Human final ruling on a PENDING case: recorded with reviewer + note; when every
    PENDING item of the run is ruled, scores.json and the run's final gate are recomputed."""
    if body.verdict not in ("PASS", "FAIL"):
        raise HTTPException(400, "verdict must be PASS or FAIL")
    run = _get_own_or_admin_run(run_id, user)
    if run["status"] != "succeeded":
        raise HTTPException(400, "run not finished: %s" % run["status"])
    n = db.review_item(run_id, body.case_id, body.verdict, user["username"], body.note)
    if n == 0:
        raise HTTPException(404, "no PENDING item %s in this run" % body.case_id)
    # when no PENDING remains: fold the rulings into scores.json + record the final gate
    items = db.list_run_items(run_id)
    open_pending = [i for i in items if i["verdict"] == "PENDING" and not i.get("final_verdict")]
    if not open_pending and run["result_dir"]:
        from ...analysis.scores import rescore_after_review
        sp = Path(run["result_dir"]) / "scores.json"
        reviews = [{"case_id": i["case_id"], "final_verdict": i["final_verdict"],
                    "reviewed_by": i["reviewed_by"], "review_note": i["review_note"]}
                   for i in items if i["verdict"] == "PENDING" and i.get("final_verdict")]
        try:
            scores = json.loads(sp.read_text(encoding="utf-8"))
            scores = rescore_after_review(scores, reviews)
            sp.write_text(json.dumps(scores, ensure_ascii=False, indent=1), encoding="utf-8")
            db.update_run(run_id, final_gate=scores.get("final_gate"),
                          score=str(scores.get("total") or ""))
            # the rulings changed the statistics — refresh the report files too
            # (any existing AI summary section is preserved verbatim)
            _rebuild_base_reports(run, Path(run["result_dir"]))
        except OSError:
            pass
    return {"ok": True, "open_pending": len(open_pending)}


class GoldFixBody(BaseModel):
    material: str = ""            # optional reference material the user can supply


@router.post("/runs/{run_id}/cases/{case_id}/ai-fix-gold")
def ai_fix_gold(run_id: str, case_id: str, body: GoldFixBody,
                user: dict = Depends(auth.require_min("member"))):
    """The user disputes this case's verdict: an LLM reviews query + current gold + the
    agent's actual answer + the judging rationale and drafts a REVISED gold. Draft only —
    applying it goes through the normal case PATCH (bank manage rights enforced there)."""
    run = _get_own_or_admin_run(run_id, user)
    if run["status"] != "succeeded":
        raise HTTPException(400, "run not finished: %s" % run["status"])
    items = {i["case_id"]: i for i in db.list_run_items(run_id)}
    item = items.get(case_id)
    if not item:
        raise HTTPException(404, "case not in this run: %s" % case_id)
    bank = db.get_benchmark(item["bank"])
    if not bank:
        raise HTTPException(404, "bank not found: %s" % item["bank"])
    from .benchmark_routes import _get_visible_bank, _require_manage
    _require_manage(_get_visible_bank(bank["name"], user), user)
    # the case (current gold) from the bank db
    owner = db.get_user(bank["owner_id"]) if bank.get("owner_id") else None
    path = banks.bank_db_path(bank, owner["username"] if owner else "")
    from ...case.store import load_cases_db
    case = next((c for c in load_cases_db(str(path), status=None)
                 if c.case_id == case_id), None)
    if not case:
        raise HTTPException(404, "case not found in bank: %s" % case_id)
    # the agent's full answer from the run's answers.jsonl artifact
    answer, final_json = "", {}
    ap = Path(run["result_dir"] or "") / "answers.jsonl"
    if ap.is_file():
        for line in ap.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                if d.get("case_id") == case_id:
                    answer, final_json = d.get("answer_text", ""), d.get("final_json") or {}
                    break
    verdict_note = ("verdict=%s；判定理由/ASI：%s"
                    % (item["verdict"], (item.get("asi") or "")))[:400]
    cfg = db.ai_config_for(user["id"])
    if not cfg:
        raise HTTPException(400, "AI not configured: set base_url/api_key/model in User Center")
    from ...analysis import ai_client
    draft = ai_client.draft_gold_fix(cfg, str(case.input.get("query", "")),
                                     {"type": case.type, "gold": case.gold.model_dump()},
                                     answer or "(答复未归档，仅凭判定理由判断)",
                                     verdict_note, body.material or "")
    if not draft:
        raise HTTPException(502, "AI draft failed (check the User Center AI config / gateway)")
    return {"draft": draft, "bank": bank["name"], "case_id": case_id,
            "answer": answer[:4000], "final_json": final_json,
            "current": {"type": case.type, "gold": case.gold.model_dump()}}


@router.delete("/runs/{run_id}")
def cancel_run(run_id: str, user: dict = Depends(auth.require_min("member"))):
    """Queued runs cancel immediately; running runs are cancelled between cases."""
    run = db.get_run(run_id)
    if not run or (run["created_by"] != user["id"]
                   and auth.ROLE_RANK.get(user["role"], -1) < auth.ROLE_RANK["admin"]):
        raise HTTPException(404, "run not found: %s" % run_id)
    if run["status"] == "queued":
        db.update_run(run_id, status="cancelled", finished_at=db.now())
    elif run["status"] == "running":
        db.update_run(run_id, cancel_requested=1)
    else:
        raise HTTPException(400, "run already finished: %s" % run["status"])
    return {"ok": True}


_ARTIFACT_SUFFIXES = (".md", ".json", ".jsonl")


@router.get("/runs/{run_id}/scores")
def run_scores(run_id: str, user: dict = Depends(auth.require_min("member"))):
    """scores.json: the run's six-dimension hexagon + per-case scores + diagnostic cards."""
    run = _get_own_or_admin_run(run_id, user)
    if not run["result_dir"]:
        raise HTTPException(404, "scores not ready")
    p = Path(run["result_dir"]) / "scores.json"
    if not p.exists():
        # pre-C1 runs have no scores file — the UI degrades to the plain report
        raise HTTPException(404, "scores file missing (run predates six-dimension scoring)")
    return json.loads(p.read_text(encoding="utf-8"))


@router.get("/benchmarks/{name}/trend")
def bank_trend(name: str, user: dict = Depends(auth.require_min("member")),
               limit: int = 20):
    """Six-dimension trend across this bank's finished runs (doc 35 C6)."""
    bank = db.get_benchmark(name)
    if not bank:
        raise HTTPException(404, "benchmark not found: %s" % name)
    rows = db.list_runs(bank=name, status="succeeded", limit=max(1, min(limit, 100)))
    out = []
    for r in rows:
        if not r["result_dir"]:
            continue
        p = Path(r["result_dir"]) / "scores.json"
        if not p.exists():
            continue
        try:
            sc = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({"run_id": r["id"], "name": r["name"], "time": r["finished_at"],
                    "scores": sc.get("run_scores"), "total": sc.get("total")})
    return {"trend": list(reversed(out))}


@router.get("/runs/{run_id}/report")
def run_report(run_id: str, lang: str = "zh", user: dict = Depends(auth.require_min("member"))):
    run = _get_own_or_admin_run(run_id, user)
    if not run["result_dir"]:
        raise HTTPException(404, "report not ready")
    fname = "report.md" if lang != "en" else "report-en.md"
    p = Path(run["result_dir"]) / fname
    if not p.exists():
        raise HTTPException(404, "report file missing: %s" % fname)
    return PlainTextResponse(p.read_text(encoding="utf-8"), media_type="text/markdown")


@router.get("/runs/{run_id}/artifacts/{fname}")
def run_artifact(run_id: str, fname: str, user: dict = Depends(auth.require_min("member"))):
    run = _get_own_or_admin_run(run_id, user)
    if not run["result_dir"]:
        raise HTTPException(404, "artifacts not ready")
    if "/" in fname or "\\" in fname or ".." in fname or not fname.endswith(_ARTIFACT_SUFFIXES):
        raise HTTPException(400, "invalid artifact name")
    p = Path(run["result_dir"]) / fname
    if not p.is_file():
        raise HTTPException(404, "artifact not found: %s" % fname)
    return FileResponse(p, filename=fname)


class CompareBody(BaseModel):
    run_ids: List[str]
    lang: str = "zh"


def _rebuild_base_reports(run: dict, out_dir: Path) -> dict:
    """Rebuild report.md/-en.md from the archived artifacts with the CURRENT
    (statistics-only) template; preserves any existing AI summary section.
    Best-effort: returns {lang: preserved_ai_section}."""
    preserved = {}
    try:
        results = json.loads((out_dir / "eval_results.json").read_text(encoding="utf-8"))
        scores_stored = json.loads((out_dir / "scores.json").read_text(encoding="utf-8"))
        runs_meta = json.loads((out_dir / "runs_meta.json").read_text(encoding="utf-8"))
        from ...result.gates import evaluate_gate
        from ...result.report import build_report
        # eval_results.json is archived before review; fold in the human final rulings
        # recorded on the run items so the report reflects the post-review state
        ruled = {i["case_id"]: i["final_verdict"]
                 for i in db.list_run_items(run["id"]) if i.get("final_verdict")}
        if ruled:
            for r in results:
                fv = ruled.get(r.get("task_id"))
                if fv and r["scores"].get("human_review") == "pending":
                    r["scores"]["human_review"] = "pass" if fv == "PASS" else "fail"
        gate = evaluate_gate(results)

        def _state(r):
            # human ruling wins, then the deterministic/rule gate, else awaiting review
            hv = r["scores"].get("human_review")
            if hv in ("pass", "fail"):
                return hv
            if r["task_id"] in gate["failures"]:
                return "fail"
            return "pending" if hv == "pending" else "pass"

        resolved_fails = [r["task_id"] for r in results if _state(r) == "fail"]
        resolved_pends = [r["task_id"] for r in results if _state(r) == "pending"]
        if resolved_fails:
            decision = "FAIL"
        elif resolved_pends:
            decision = "PENDING(%d)" % len(resolved_pends)
        else:
            decision = "GREEN"
        gate = dict(gate, decision=decision, failures=resolved_fails,
                    score=("%d/%d" % (len(results) - len(resolved_pends) - len(resolved_fails),
                                      len(results) - len(resolved_pends))
                           if (len(results) - len(resolved_pends)) > 0 else "n/a"))
        byl, bys = {}, {}
        for m in runs_meta:
            byl.setdefault(m.get("level", "-"), []).append(m["task_id"])
            bys.setdefault(m.get("suite", "-"), []).append(m["task_id"])

        def _rows(groups):
            rows = []
            for k, tids in sorted(groups.items()):
                tset = set(tids)
                res = [r for r in results if r["task_id"] in tset]
                fail = sum(1 for r in res if _state(r) == "fail")
                pend = sum(1 for r in res if _state(r) == "pending")
                rows.append({"suite": k, "level": k, "total": len(res),
                             "pass": len(res) - fail - pend, "fail": fail, "pending": pend})
            return rows

        meta = {"time": run.get("finished_at") or run.get("created_at") or "",
                "level_rows": _rows(byl), "suite_rows": _rows(bys),
                "target": run.get("target_url", ""),
                "model": (runs_meta[0].get("model") if runs_meta else "-"),
                "eval_set_version": (results[0].get("eval_set_version") if results else "-"),
                "repeat_k": run.get("stability_k") or 1, "scores": scores_stored,
                "ai_used": False}
        for lang, suffix in (("zh", ""), ("en", "-en")):
            rp = out_dir / ("report%s.md" % suffix)
            old = rp.read_text(encoding="utf-8") if rp.is_file() else ""
            marker = "## AI 摘要与改进建议" if lang == "zh" else "## AI Summary"
            idx = old.find(marker)
            if idx != -1:
                # preserve ONLY the AI section (up to the next top-level heading),
                # never the rest of the old report body
                nxt = old.find(chr(10) + "## ", idx + 10)
                preserved[lang] = old[idx:nxt if nxt != -1 else len(old)].strip()
            rp.write_text(build_report(results, gate, meta, lang=lang), encoding="utf-8")
    except Exception:
        pass
    return preserved


@router.post("/runs/{run_id}/rebuild-report")
def rebuild_report(run_id: str, user: dict = Depends(auth.require_min("member"))):
    """Manual report rebuild: regenerate the statistics report (and refresh the AI summary
    section if present) from the archived artifacts — e.g. after human rulings changed
    the verdicts, or to pick up the current report template."""
    run = _get_own_or_admin_run(run_id, user)
    if run["status"] != "succeeded":
        raise HTTPException(400, "run not finished: %s" % run["status"])
    out_dir = Path(run["result_dir"] or "")
    if not out_dir.is_dir():
        raise HTTPException(404, "no archived results for this run")
    preserved = _rebuild_base_reports(run, out_dir)
    for lang, section in preserved.items():
        suffix = "-en" if lang == "en" else ""
        rp = out_dir / ("report%s.md" % suffix)
        body = rp.read_text(encoding="utf-8")
        rp.write_text(body.rstrip() + "\n\n" + section + "\n", encoding="utf-8")
    return {"ok": True}


@router.post("/runs/{run_id}/ai-summary")
def regenerate_ai_summary(run_id: str, user: dict = Depends(auth.require_min("member"))):
    NL = chr(10)
    """Post-hoc AI report summary: runs that finished before the reviewer configured AI
    have no summary section — this (re)generates it from the archived report and failure
    list, for BOTH languages, and rewrites the report files in place."""
    run = _get_own_or_admin_run(run_id, user)
    if run["status"] != "succeeded":
        raise HTTPException(400, "run not finished: %s" % run["status"])
    cfg = db.ai_config_for(user["id"])
    if not cfg:
        raise HTTPException(400, "AI not configured: set base_url/api_key/model in User Center")
    items = db.list_run_items(run_id)
    failed = [{"case_id": i["case_id"], "asi": i.get("asi") or ""} for i in items
              if i["verdict"] == "FAIL"]
    out_dir = Path(run["result_dir"] or "")
    _rebuild_base_reports(run, out_dir)
    from ...analysis import ai_client
    generated = {}
    for lang, suffix in (("zh", ""), ("en", "-en")):
        rp = out_dir / ("report%s.md" % suffix)
        if not rp.is_file():
            continue
        text = rp.read_text(encoding="utf-8")
        summary = ai_client.summarize_report(cfg, text, failed, lang=lang)
        if not summary:
            continue
        notes = ai_client.attribute_errors(cfg, failed, lang="zh") or []
        notes_en = ai_client.attribute_errors(cfg, failed, lang="en") or []
        model_name = cfg.get("model", "LLM")
        head, marker = (("AI 摘要与改进建议（%s 起草，供参考）", "## AI 摘要与改进建议")
                        if lang == "zh" else ("AI Summary (drafted by %s, for reference)", "## AI Summary"))
        section = NL * 2 + "## " + head % model_name + NL + NL + summary + NL
        use_notes = notes if lang == "zh" else notes_en
        if use_notes:
            sub = ("失败题根因（AI 起草）" if lang == "zh" else "Failure root causes (AI-drafted)")
            section += NL + "### " + sub + NL + NL + NL.join(
                ("- **%s**：%s → %s" if lang == "zh" else "- **%s**: %s → %s") %
                (n.get("case_id"), n.get("root_cause", ""), n.get("fix", ""))
                for n in use_notes) + NL
        idx = text.find(marker)                       # strip any existing AI section first
        if idx != -1:
            text = text[:idx].rstrip() + NL
        ins = text.find(NL + "## ")                   # insert right after the overview,
        if ins != -1:                                 # KEEPING the statistics sections
            rp.write_text(text[:ins].rstrip() + section + NL + text[ins:], encoding="utf-8")
        else:
            rp.write_text(text.rstrip() + section, encoding="utf-8")
        generated[lang] = {"summary": summary, "notes": use_notes}
    if not generated:
        raise HTTPException(502, "AI summary failed (check the User Center AI config / gateway)")
    return {"ok": True, "generated": {k: v["summary"] for k, v in generated.items()}}


@router.post("/compare")
def compare(body: CompareBody, user: dict = Depends(auth.require_min("member"))):
    if len(body.run_ids) < 2:
        raise HTTPException(400, "pick at least 2 runs to compare")
    dirs = []
    for rid in body.run_ids:
        run = _get_own_or_admin_run(rid, user)
        if not run["result_dir"] or not Path(run["result_dir"]).exists():
            raise HTTPException(404, "run result dir missing: %s" % rid)
        dirs.append(run["result_dir"])
    from ...control.compare import compare_runs, render
    summary = compare_runs(dirs)
    return {"summary": summary, "report_zh": render(summary, lang="zh"),
            "report_en": render(summary, lang="en")}
