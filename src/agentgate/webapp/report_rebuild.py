"""Base-report rebuild shared by the review fold, the manual rebuild endpoint and the
rejudge replay — one template, one folding rule (human ruling wins), no duplicates."""
import json
from pathlib import Path

from . import db


def rebuild_base_reports(run: dict, out_dir: Path) -> dict:
    """Rebuild report.md/-en.md from the archived artifacts with the CURRENT
    (statistics-only) template; preserves any existing AI summary section.
    Best-effort: returns {lang: preserved_ai_section}."""
    preserved = {}
    try:
        results = json.loads((out_dir / "eval_results.json").read_text(encoding="utf-8"))
        scores_stored = json.loads((out_dir / "scores.json").read_text(encoding="utf-8"))
        runs_meta = json.loads((out_dir / "runs_meta.json").read_text(encoding="utf-8"))
        from ..result.gates import evaluate_gate
        from ..result.report import build_report
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
