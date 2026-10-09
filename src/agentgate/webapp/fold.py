"""Review folding shared by the manual review endpoint and the auto-adopt watcher:
when no PENDING item is left open, the recorded rulings (human or adopted AI) are
folded into scores.json, the run row and both reports — one place, one rule."""
import json
from pathlib import Path


def open_pending_count(run_id: str) -> int:
    from . import db
    return sum(1 for i in db.list_run_items(run_id)
               if i["verdict"] == "PENDING" and not i.get("final_verdict"))


def fold_if_complete(run: dict) -> int:
    """Fold the recorded final rulings into scores.json / runs row / reports when every
    PENDING item has a final verdict. Returns the number of still-open items."""
    from . import db
    rid = run["id"]
    items = db.list_run_items(rid)
    open_pending = [i for i in items
                    if i["verdict"] == "PENDING" and not i.get("final_verdict")]
    if open_pending or not run.get("result_dir"):
        return len(open_pending)
    sp = Path(run["result_dir"]) / "scores.json"
    if not sp.is_file():
        return 0
    reviews = [{"case_id": i["case_id"], "final_verdict": i["final_verdict"],
                "reviewed_by": i.get("reviewed_by"), "review_note": i.get("review_note")}
               for i in items if i["verdict"] == "PENDING" and i.get("final_verdict")]
    try:
        from ..analysis.scores import rescore_after_review
        from .rejudge import ai_coverage_from_judge
        scores = json.loads(sp.read_text(encoding="utf-8"))
        scores = rescore_after_review(scores, reviews,
                                      ai_coverage=ai_coverage_from_judge(run["result_dir"]))
        sp.write_text(json.dumps(scores, ensure_ascii=False, indent=1), encoding="utf-8")
        db.update_run(rid, final_gate=scores.get("final_gate"),
                      score=str(scores.get("total") or ""))
        # the rulings changed the statistics — refresh the report files too
        # (any existing AI summary section is preserved verbatim)
        from .report_rebuild import rebuild_base_reports
        rebuild_base_reports(run, Path(run["result_dir"]))
    except (OSError, ValueError):
        pass
    return 0
