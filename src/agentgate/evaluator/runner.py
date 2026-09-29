"""Judged-case assembly: hard-layer judging (judging.py) -> EvalResult contract.

Returns (EvalResult, judge_out) — the six-dimension scorer consumes judge_out
(safety/checkpoints/process signals). Soft judge (LLM) suggestions and human review
attach afterwards in the service; this module stays deterministic (hard checks only).
"""
import datetime
from typing import Dict, List, Tuple

from agent_contracts import (AgentVersion, Attribution, Cost, ErrorLocalization,
                             EvalResult, Evidence, Scores)

from . import judging
from .builtin import cost as cost_builtin
from .builtin import trajectory as trajectory_evidence


def evaluate_case(case, response, trace, wall_time_s: float, pack: Dict = None,
                  analysis: Dict = None, raw_spans: List[Dict] = None,
                  tool_blob: str = "", assets_roots=None) -> Tuple[EvalResult, Dict]:
    if pack is None:                      # default: resolve the case's own domain pack
        from ..case import packs as pack_reg
        pack = pack_reg.resolve_pack(case)
    suite = case.suite or ("FB" if case.case_id.startswith("fb-") else
                           ("example" if case.case_id.startswith(("core-", "finbench-adapt-"))
                            else "misc"))
    version = case.eval_set_version or (suite + "-v0.1")
    out = judging.judge_case(case, response, trace, pack or {}, analysis, raw_spans,
                             tool_blob, assets_roots=assets_roots)

    # det_pass keeps the historical meaning "no hard check failed": PENDING (free_text)
    # cases stay det_pass=True and are distinguished by human_review="pending" — the gate,
    # buckets and reports keep treating pending as its own outcome, never as failure.
    det_pass = out["verdict"] != "FAIL"
    pending = out["verdict"] == "PENDING"
    asi = judging.build_asi(out, case)
    fails = [c for c in out["checks"] if not c["ok"]]
    loc = None
    if fails:
        first = fails[0]
        loc = ErrorLocalization(failed_step=first.get("failed_step") or "final",
                                failure_kind=case.diagnosis_hint.failure_kind,
                                explanation=first.get("reason"))
    elif out["checkpoints"] and out["verdict"] == "FAIL":
        missed = [c for c in out["checkpoints"] if c["hit"] is False]
        if missed:
            loc = ErrorLocalization(failed_step="checkpoint",
                                    failure_kind=case.diagnosis_hint.failure_kind,
                                    explanation="未命中检查点：%s" % missed[0]["desc"])
    if out["safety"]["level"] == "P0":
        attribution = Attribution(category="safety_red_line", target_layer="none", confidence=1.0)
    elif fails or out["verdict"] == "FAIL":
        attribution = Attribution(category=case.diagnosis_hint.failure_kind,
                                  target_layer=case.diagnosis_hint.target_layer, confidence=0.9)
    elif pending:
        attribution = Attribution(category="judge_required", target_layer="none", confidence=0.5)
    else:
        attribution = Attribution(category="none", target_layer="none", confidence=1.0)

    evidence = [Evidence(**e) for e in trajectory_evidence.evidence(case, response, trace)]
    for cp in out["checkpoints"]:
        evidence.append(Evidence(claim="checkpoint:%s" % cp["signal"],
                                 source_id="gold",
                                 location="%s | %s" % (cp["desc"], cp["evidence"])))
    if out["partial_credit"] is not None:
        evidence.append(Evidence(claim="partial_credit", source_id="gold",
                                 location=str(out["partial_credit"])))
    er = EvalResult(
        task_id=case.case_id, as_of=case.as_of or datetime.date.today().isoformat(),
        eval_set_version=version,
        agent_version=AgentVersion(profile_version="fin-runtime@0.1.0",
                                   component_snapshot=trace.component_versions or None),
        scores=Scores(deterministic_pass=det_pass, rule_pass=not out["safety"]["items"],
                      human_review="pending" if pending else None),
        cost=Cost(**cost_builtin.record(case, response, trace, wall_time_s)),
        evidence=evidence, trace_linked=True, error_localization=loc, ASI=asi,
        attribution=attribution,
        evaluator_results=out["checks"] + [
            {"ok": c["hit"], "layer": "checkpoint", "name": "checkpoint:%s" % c["signal"],
             "reason": c["desc"], "asi": "", "failed_step": None, "trace_linked": True}
            for c in out["checkpoints"]],
    )
    return er, out
