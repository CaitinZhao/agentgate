"""Re-judge a finished run from its archived artifacts (A2, doc 35 §5.2).

A judging-logic fix or a bank repair must be replayable WITHOUT re-invoking the agent:
answers.jsonl + spans_raw.json + the CURRENT bank cases contain everything the hard
layer saw the first time. rejudge_run replays evaluate_case per case, folds the
recorded final rulings back on top (human/AI ruling wins over a fresh hard verdict),
rewrites the run artifacts (eval_results / scores / gate / reports) and updates the
run_items verdicts. The replay never talks to the target.
"""
import datetime
import json
import time
import traceback
from pathlib import Path

from . import banks, db


def _jsonl(path: Path) -> list:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def ai_coverage_from_judge(result_dir) -> dict:
    """{case_id: weighted coverage ratio 0..1} from judge.jsonl coverage rows — the AI
    judge's rubric-point scoring, reused as the quality basis for adopted cases."""
    out = {}
    for d in _jsonl(Path(result_dir) / "judge.jsonl"):
        cov = d.get("coverage") or []
        wsum = sum(float(c.get("weight") or 0) for c in cov)
        if wsum <= 0:
            continue
        hit = sum(float(c.get("weight") or 0) for c in cov if c.get("hit"))
        out[d.get("case_id", "")] = hit / wsum
    return out


def _bank_cases_map(items):
    """{case_id: Case} loaded from each bank named on the run items (current gold)."""
    cache, cases = {}, {}
    from ..case.store import load_cases_db
    for it in items:
        bname = it.get("bank") or ""
        if bname not in cache:
            bank = db.get_benchmark(bname)
            if not bank:
                cache[bname] = {}
            else:
                owner = db.get_user(bank["owner_id"]) if bank.get("owner_id") else None
                path = banks.bank_db_path(bank, owner["username"] if owner else "")
                try:
                    cache[bname] = {c.case_id: c for c in
                                    load_cases_db(str(path), status=None)}
                except Exception:
                    cache[bname] = {}
        c = cache[bname].get(it["case_id"])
        if c is not None:
            cases[it["case_id"]] = c
    return cases


def rejudge_run(run: dict) -> dict:
    """Replay the hard judge over one finished run. Returns a summary dict; raises
    ValueError when the run has no replayable artifacts."""
    rid = run["id"]
    rd = Path(run["result_dir"] or "")
    if not rd.is_dir():
        raise ValueError("run has no archived results (result_dir missing)")
    items = db.list_run_items(rid)
    if not items:
        raise ValueError("run has no items")
    answers = {d.get("case_id"): d for d in _jsonl(rd / "answers.jsonl")}
    try:
        spans_raw = json.loads((rd / "spans_raw.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        spans_raw = []
    spans = {d.get("task_id"): (d.get("spans") or []) for d in spans_raw}
    try:
        mcalls = {d.get("task_id"): (d.get("calls") or [])
                  for d in json.loads((rd / "llm_calls_raw.json").read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        mcalls = {}
    suggestions = {d.get("case_id"): d for d in _jsonl(rd / "judge.jsonl")}
    try:
        orig_results = {r.get("task_id"): r for r in
                        json.loads((rd / "eval_results.json").read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        orig_results = {}

    cases = _bank_cases_map(items)
    roots = _search_roots(items, cases)
    from ..case import packs as pack_reg
    from ..control.service import _error_result, _tool_blob
    from ..evaluator.runner import evaluate_case
    from ..trace.normalizer import normalize

    results, case_rows, n_judged, changes = [], [], 0, []
    for it in items:
        cid = it["case_id"]
        case = cases.get(cid)
        replayable = (it["verdict"] != "SKIPPED" and case is not None
                      and cid in answers)
        if not replayable:
            results.append(orig_results.get(cid) or {
                "task_id": cid, "scores": {"deterministic_pass": True, "rule_pass": True,
                                           "human_review": None}})
            case_rows.append({"case_id": cid, "verdict": it["verdict"], "judge_out": {},
                              "analysis": {}, "tokens": it.get("tokens") or 0,
                              "wall_s": it.get("wall_time_s") or 0.0, "asi": it.get("asi") or "",
                              "stability": None, "suite": it.get("bank") or "", "origin": ""})
            continue
        a = answers[cid]
        response = {"answer_text": a.get("answer_text", ""),
                    "final_json": a.get("final_json") or {}}
        raw_spans = spans.get(cid) or []
        trace = normalize(raw_spans)
        calls = mcalls.get(cid) or []
        pack = pack_reg.resolve_pack(case, data_root=db.data_root())
        try:
            from ..analysis.trajectory import analyze_case
            analysis = analyze_case(case, response, trace, calls)
        except Exception:
            analysis = {}
        wall = float(it.get("wall_time_s") or 0.0)
        try:
            er, judge_out = evaluate_case(case, response, trace, wall, pack=pack,
                                          analysis=analysis, raw_spans=raw_spans,
                                          tool_blob=_tool_blob(calls),
                                          assets_roots=roots, sandbox=None)
        except Exception as e:
            er, judge_out = _error_result(case, e, wall, error_kind="judge")
        er.cost.tokens = int(it.get("tokens") or 0)
        er.cost.wall_time_s = wall
        hard = judge_out.get("verdict", "PENDING")
        verdict = hard
        if it.get("final_verdict"):          # human / adopted-AI ruling keeps winning
            verdict = it["final_verdict"]
            er.ASI = (er.ASI + " [rejudge] hard=%s, kept ruling %s" % (hard, verdict)).strip()
        er.scores.deterministic_pass = verdict != "FAIL"
        er.scores.rule_pass = not (judge_out.get("safety") or {}).get("items")
        er.scores.human_review = "pending" if verdict == "PENDING" else None
        er.run_id = rid
        results.append(er.model_dump())
        case_rows.append({"case_id": cid, "verdict": verdict, "judge_out": judge_out,
                          "analysis": analysis, "tokens": er.cost.tokens, "wall_s": wall,
                          "asi": er.ASI, "stability": None,
                          "query": str((case.input or {}).get("query", ""))[:200],
                          "origin": case.source.origin, "suite": case.suite or it.get("bank") or "",
                          "native": judge_out.get("native"),
                          "judge_suggest": suggestions.get(cid)})
        n_judged += 1
        if verdict != it["verdict"]:
            changes.append({"case_id": cid, "from": it["verdict"], "to": verdict,
                            "ruled": bool(it.get("final_verdict"))})

    # ---- write artifacts ----
    from ..result.export.json_report import export as export_json
    from ..result.gates import evaluate_gate
    from ..analysis.scores import score_run
    try:
        runs_meta = json.loads((rd / "runs_meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        runs_meta = []
    if isinstance(runs_meta, dict):
        runs_meta = runs_meta.get("runs") or []
    cov = ai_coverage_from_judge(rd)
    # AI rubric coverage is the quality basis for cases whose verdict came from an
    # adopted judge suggestion (their coverage was scored, the formula was not)
    quality_override = {}
    for row in case_rows:
        sug = row.pop("judge_suggest", None)
        if (row["verdict"] in ("PASS", "FAIL") and row["case_id"] in cov
                and (sug or {}).get("verdict_suggest") in ("PASS", "FAIL")):
            quality_override[row["case_id"]] = cov[row["case_id"]]
    gate = evaluate_gate(results)
    scores = score_run(case_rows, quality_override=quality_override or None)
    export_json(str(rd), results, runs_meta, gate)
    (rd / "scores.json").write_text(json.dumps(scores, ensure_ascii=False, indent=1),
                                    encoding="utf-8")

    # ---- db: items + run row ----
    verdict_by_id = {r["case_id"]: r["verdict"] for r in case_rows}
    asi_by_id = {r["case_id"]: (r.get("asi") or "") for r in case_rows}
    db.set_item_verdicts(rid, verdict_by_id, asi_by_id)
    counts = scores.get("counts") or {}
    decision = ("FAIL" if counts.get("fail") else
                ("PENDING(%d)" % counts.get("pending", 0) if counts.get("pending") else "GREEN"))
    db.update_run(rid, gate_decision=decision, final_gate=scores.get("final_gate"),
                  score=(str(scores.get("total")) if scores.get("total") is not None
                         else run.get("score") or ""))
    from .report_rebuild import rebuild_base_reports
    rebuild_base_reports(dict(run, finished_at=run.get("finished_at") or
                              datetime.datetime.now().isoformat(timespec="seconds")), rd)
    return {"rejudged": n_judged, "total_items": len(items),
            "changes": changes, "counts": counts, "scores": scores,
            "quality_overridden": len(quality_override)}


def _search_roots(items, cases):
    """Context-file search roots for the replay: each bank's directory (assets ship next
    to cases.db) + the repo cases/ tree (same rule as the worker)."""
    roots, seen = [], set()

    def add(p):
        p = Path(p)
        if p.is_dir() and str(p) not in seen:
            seen.add(str(p))
            roots.append(p)

    bank_dirs = set()
    for it in items:
        bank = db.get_benchmark(it.get("bank") or "")
        if bank:
            owner = db.get_user(bank["owner_id"]) if bank.get("owner_id") else None
            path = banks.bank_db_path(bank, owner["username"] if owner else "")
            bank_dirs.add(str(Path(path).parent))
    for d in sorted(bank_dirs):
        add(d)
    for base in [Path(__file__).resolve().parents[i] for i in range(1, 6)]:
        add(base / "cases")
    return roots
