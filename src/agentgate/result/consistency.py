"""Run-artifact self-consistency check (report trustworthiness gate).

The bugs this guards against all share one shape: the report states a fact that its own
archived evidence contradicts (model calls counted 0 while spans exist; a total printed
from zero judged cases; a safety 100 while every safety case is PENDING). Each fact is
individually plausible, so the reviewer cannot spot the lie by reading the report — the
artifacts must be cross-checked before the report is trusted.

consistency_check runs on the in-memory run summary AND on the archived artifacts
(so it also guards rejudge / rebuild paths). It returns a verdict + machine-readable
findings; the run is marked `degraded` (not accept-eligible) when any ERROR fires.
"""
from typing import Dict, List

SEVERITY_ORDER = {"error": 0, "warning": 1}


def _finding(code: str, severity: str, detail: str) -> Dict:
    return {"code": code, "severity": severity, "detail": detail}


def check_summary(scores: Dict, case_rows: List[Dict], meta: Dict = None) -> Dict:
    """Cross-check a run's own outputs against each other.

    scores: scores.json content; case_rows: the scorer's per-case input rows (each with
    verdict + analysis + judge_out); meta: runs meta (target_errors etc.).
    """
    findings: List[Dict] = []
    meta = meta or {}
    counts = scores.get("counts") or {}
    per_case = scores.get("per_case") or []
    n_judged = counts.get("judged")
    if n_judged is None:
        n_judged = sum(1 for c in per_case if c.get("verdict") in ("PASS", "FAIL"))

    # 1) a total printed while nothing was judged
    if scores.get("total") is not None and n_judged == 0:
        findings.append(_finding("total_without_judgement", "error",
                                 "a total (%s) was produced but 0 cases were judged" % scores["total"]))

    # 2) model calls counted 0 while the trace carries llm spans
    for row in case_rows:
        a = row.get("analysis") or {}
        if a.get("has_model_calls") and not a.get("n_model_calls"):
            findings.append(_finding("model_calls_contradiction", "error",
                                     "%s: analysis reports 0 model calls but the trace has llm spans"
                                     % row.get("case_id")))
            break

    # 3) safety scored 100 while no case produced safety evidence
    safety = (scores.get("run_scores") or {}).get("safety")
    safety_evidenced = any(
        (c.get("scores") or {}).get("safety") is not None for c in per_case)
    if safety is not None and not safety_evidenced and per_case:
        findings.append(_finding("safety_without_evidence", "error",
                                 "safety=%s but no case produced safety evidence" % safety))

    # 4) native rate printed while every case of that suite is undecided
    for suite, nat in (scores.get("native") or {}).items():
        if nat.get("rate") is not None and nat.get("resolved") == 0:
            findings.append(_finding("native_rate_without_resolved", "error",
                                     "native rate for %s printed with 0 resolved cases" % suite))

    # 5) target-error run carrying a numeric score (attribution leak)
    n_err = int(scores.get("target_errors") or meta.get("target_errors") or 0)
    if per_case and n_err >= len(per_case) and scores.get("total") is not None:
        findings.append(_finding("target_error_scored", "error",
                                 "every case failed to reach the target yet a total was printed"))

    # 6) warning: pending majority means most of the report is inference, not measurement
    total = counts.get("total") or len(per_case)
    if total and (counts.get("pending") or 0) > total / 2:
        findings.append(_finding("pending_majority", "warning",
                                 "%d/%d cases pending — the run's readings are largely unmeasured"
                                 % (counts.get("pending"), total)))

    errors = [f for f in findings if f["severity"] == "error"]
    return {"ok": not errors, "degraded": bool(errors), "findings": findings,
            "n_errors": len(errors)}


def check_artifacts(result_dir: str) -> Dict:
    """Re-run the cross-checks against the ARCHIVED artifacts (used by rejudge/rebuild).

    Only the checks that need no per-case scorer input live here: totals vs counts, and
    the model-call contradiction read back from analysis.md vs spans_raw.json presence.
    """
    import json
    from pathlib import Path
    rd = Path(result_dir)
    findings: List[Dict] = []
    try:
        scores = json.loads((rd / "scores.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": True, "degraded": False, "findings": [], "n_errors": 0}
    counts = scores.get("counts") or {}
    n_judged = counts.get("judged")
    if n_judged is None:
        n_judged = sum(1 for c in scores.get("per_case") or []
                       if c.get("verdict") in ("PASS", "FAIL"))
    if scores.get("total") is not None and n_judged == 0:
        findings.append(_finding("total_without_judgement", "error",
                                 "total printed with 0 judged cases"))
    safety = (scores.get("run_scores") or {}).get("safety")
    if safety is not None and not any((c.get("scores") or {}).get("safety") is not None
                                      for c in scores.get("per_case") or []):
        findings.append(_finding("safety_without_evidence", "error",
                                 "safety=%s with no case-level evidence" % safety))
    # analysis claims vs the raw span archive. Judge STRUCTURALLY (counts vs spans), never
    # by matching report prose — a wording change must not silently disable the guard, and
    # both languages must be covered.
    try:
        spans = json.loads((rd / "spans_raw.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        spans = []
    spans_with_data = [s for s in spans if (s.get("spans") or [])]
    if spans_with_data:
        per_case = scores.get("per_case") or []
        # any archived case that claims zero model calls while its span archive is non-empty
        model_claims = [c for c in per_case if (c.get("model_calls") == 0)]
        if model_claims or not per_case:
            findings.append(_finding(
                "model_calls_contradiction", "error",
                "%d archived case(s) report 0 model calls while spans_raw.json holds spans "
                "for %d case(s)" % (len(model_claims) or 1, len(spans_with_data))))
    errors = [f for f in findings if f["severity"] == "error"]
    return {"ok": not errors, "degraded": bool(errors), "findings": findings,
            "n_errors": len(errors)}
