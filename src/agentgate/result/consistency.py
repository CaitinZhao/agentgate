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

    # 2) model calls counted 0 while the trace carries llm spans.
    # DEFENSE IN DEPTH ONLY — do not rely on this one: analyze_case derives both fields
    # from the same trace, so within a single in-memory run `has_model_calls` implies a
    # non-zero count and this condition is effectively unreachable by construction. The
    # guard that actually works runs against the ARCHIVED counts (check_artifacts), where
    # the count written during the run can genuinely disagree with the spans on disk.
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

    This is the guard that actually runs the model-call check: it joins each case's
    archived model-call count (per_case.model_calls, persisted by the scorer) against that
    SAME case's spans in spans_raw.json. A case missing the count is reported as
    `model_calls_unchecked` rather than passing silently; cases without spans of their own
    (audit-only targets) are never flagged just because a sibling case has spans.
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
    # join PER CASE: a case reporting 0 is only contradicted when THAT case archived spans
    # of its own. OTel export and the in-response audit are two coexisting channels, so a
    # mixed archive (some cases with spans, some audited-only) is normal — comparing the
    # two sets globally would flag an honest audited-only case just because a sibling has
    # spans. Both the verdict and the message must be per-case or they are misleading.
    spans_by_case = {s.get("task_id"): len(s.get("spans") or []) for s in spans}
    per_case = scores.get("per_case") or []
    contradicted = [c.get("case_id") for c in per_case
                    if c.get("model_calls") == 0
                    and spans_by_case.get(c.get("case_id"), 0) > 0]
    if contradicted:
        findings.append(_finding(
            "model_calls_contradiction", "error",
            "%d case(s) archived as 0 model calls while the SAME case holds spans in "
            "spans_raw.json (e.g. %s)"
            % (len(contradicted), ", ".join(contradicted[:3]))))
    # a case counts as CHECKED only when it carries a NUMBER: a missing key and an explicit
    # null are the same thing — "the count is unknown" (e.g. the analyzer threw and left
    # analysis={"error": ...}, so the row persisted model_calls=None). Both must warn; only
    # a real 0 is a fact, and only a real 0 can contradict spans. Semantics:
    #   number -> checked (0 is a suspicious CLAIM, anything else is a fact)
    #   null / missing -> unchecked (unknown, never "consistent")
    def _unknown(c) -> bool:
        return c.get("model_calls") is None
    unchecked = [c.get("case_id") for c in per_case if _unknown(c)]
    if unchecked:
        findings.append(_finding(
            "model_calls_unchecked", "warning",
            "%d case(s) carry an unknown model-call count (missing, or null because the "
            "analyzer failed), so the check skipped them (e.g. %s) — regenerate scores.json "
            "(or rejudge) to cover them"
            % (len(unchecked), ", ".join(unchecked[:3]))))
    errors = [f for f in findings if f["severity"] == "error"]
    return {"ok": not errors, "degraded": bool(errors), "findings": findings,
            "n_errors": len(errors)}
