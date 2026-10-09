"""Post-hoc single-case AI judging core, shared by the manual endpoints and the
auto-adopt watcher (A4) — one implementation of "judge one archived PENDING case",
so manual suggestions and automatic adoption behave identically."""
import json
from pathlib import Path
from typing import Dict

from fastapi import HTTPException

from . import banks, db


def ai_judge_one(run: dict, case_id: str, cfg: dict, user: dict) -> dict:
    """Judge ONE PENDING case post-hoc (suggestion only); appends to judge.jsonl so the
    run detail picks it up as the reviewer's decision aid. Raises HTTPException on
    missing case/bank or judge failure; bank manage rights are enforced."""
    item = next((i for i in db.list_run_items(run["id"]) if i["case_id"] == case_id), None)
    if not item:
        raise HTTPException(404, "case not in this run: %s" % case_id)
    if item["verdict"] != "PENDING":
        raise HTTPException(400, "case is %s — only PENDING cases need judging" % item["verdict"])
    bank = db.get_benchmark(item["bank"])
    if not bank:
        raise HTTPException(404, "bank not found: %s" % item["bank"])
    from .routes.benchmark_routes import _get_visible_bank, _require_manage
    _require_manage(_get_visible_bank(bank["name"], user), user)
    owner = db.get_user(bank["owner_id"]) if bank.get("owner_id") else None
    path = banks.bank_db_path(bank, owner["username"] if owner else "")
    from ..case.store import load_cases_db
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
    from ..case import packs as pack_reg
    pack = pack_reg.resolve_pack(case, data_root=db.data_root())
    from ..analysis import ai_client
    sug = ai_client.judge_suggestion(cfg, case, pack, response, judge_out)
    if not sug:
        raise HTTPException(502, "AI judge failed (check the User Center AI config / gateway)")
    sug.update({"case_id": case_id, "adopted": False, "post_hoc": True})
    jp = Path(run["result_dir"] or "") / "judge.jsonl"
    with jp.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(sug, ensure_ascii=False) + "\n")
    return sug
