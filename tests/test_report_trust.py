"""Report trustworthiness guards (bugs.md 一/二/三): the report must never state a fact
its own evidence contradicts, and a run with nothing judged must not print a total."""
import json

from agentgate.analysis.scores import score_run, total_score
from agentgate.result.consistency import check_artifacts, check_summary


def _row(cid, verdict, **kw):
    row = {"case_id": cid, "verdict": verdict, "judge_out": {}, "analysis": {},
           "tokens": 0, "wall_s": 0.0}
    row.update(kw)
    return row


# ---------------- P0-2: no total without judgement, no free safety 100 ----------------

def test_zero_judged_run_has_no_total():
    scores = score_run([_row("a", "PENDING"), _row("b", "PENDING")])
    assert scores["total"] is None
    assert scores["scored"] is False
    assert scores["run_scores"]["safety"] is None      # no evidence -> n/a, not 100
    assert scores["counts"]["judged"] == 0


def test_judged_run_still_scores():
    rows = [_row("a", "PASS"), _row("b", "FAIL")]
    scores = score_run(rows)
    assert scores["total"] is not None and scores["scored"] is True
    assert scores["run_scores"]["safety"] == 100.0    # evidence exists -> real 100
    assert scores["counts"]["judged"] == 2


def test_total_score_guard_directly():
    rs = {"success": None, "quality": None, "reliability": 100.0, "stability": None,
          "efficiency": 100.0, "cost": None, "safety": None}
    assert total_score(rs, n_judged=0)["total"] is None
    rs2 = dict(rs, success=50.0)
    assert total_score(rs2, n_judged=3)["total"] is not None


def test_pending_case_safety_is_na_not_100():
    scores = score_run([_row("a", "PENDING")])
    assert scores["per_case"][0]["scores"]["safety"] is None


# ---------------- P1: artifact self-consistency ----------------

def test_consistency_flags_total_without_judgement():
    scores = score_run([_row("a", "PENDING")])
    scores["total"] = 100.0                 # simulate the old fabricated total
    res = check_summary(scores, [_row("a", "PENDING")])
    assert res["degraded"] is True
    assert any(f["code"] == "total_without_judgement" for f in res["findings"])


def test_consistency_flags_model_call_contradiction():
    scores = score_run([_row("a", "PASS")])
    rows = [_row("a", "PASS", analysis={"has_model_calls": True, "n_model_calls": 0})]
    res = check_summary(scores, rows)
    assert any(f["code"] == "model_calls_contradiction" for f in res["findings"])


def test_consistency_flags_native_rate_without_resolved():
    scores = score_run([_row("a", "PENDING"), _row("b", "PENDING")])
    scores["native"] = {"harmbench": {"rate": 0.0, "resolved": 0, "total": 2}}
    res = check_summary(scores, [_row("a", "PENDING"), _row("b", "PENDING")])
    assert any(f["code"] == "native_rate_without_resolved" for f in res["findings"])


def test_consistency_clean_run_passes():
    rows = [_row("a", "PASS"), _row("b", "FAIL")]
    scores = score_run(rows)
    res = check_summary(scores, rows)
    assert res["ok"] is True and res["degraded"] is False


def test_check_artifacts_detects_the_archived_lie(tmp_path):
    # the original bug, judged STRUCTURALLY (not by matching report prose, which a wording
    # change would silently disable): a case claims 0 model calls while spans show llm work
    (tmp_path / "scores.json").write_text(json.dumps({
        "total": None, "counts": {"pass": 1, "fail": 1, "pending": 0, "judged": 2},
        "run_scores": {"safety": 100.0},
        "per_case": [{"case_id": "a", "verdict": "PASS", "model_calls": 0,
                      "scores": {"safety": 100.0}}]}), encoding="utf-8")
    (tmp_path / "spans_raw.json").write_text(json.dumps(
        [{"task_id": "a", "spans": [{"name": "llm.call"}]}]), encoding="utf-8")
    res = check_artifacts(str(tmp_path))
    assert res["degraded"] is True
    assert any(f["code"] == "model_calls_contradiction" for f in res["findings"])


def test_check_artifacts_clean_when_counts_match(tmp_path):
    (tmp_path / "scores.json").write_text(json.dumps({
        "total": 80.0, "counts": {"pass": 1, "fail": 1, "pending": 0, "judged": 2},
        "run_scores": {"safety": 100.0},
        "per_case": [{"case_id": "a", "verdict": "PASS", "model_calls": 5,
                      "scores": {"safety": 100.0}}]}), encoding="utf-8")
    (tmp_path / "spans_raw.json").write_text(json.dumps(
        [{"task_id": "a", "spans": [{"name": "llm.call"}]}]), encoding="utf-8")
    res = check_artifacts(str(tmp_path))
    assert res["ok"] is True


# ---------------- golden parser guards (bugs.md 二) ----------------

def test_gold_parser_skips_years_and_entity_questions():
    from agentgate.case.public_benchmarks.financebench_adapter import _parse_gold
    # a plain year must not become the gold value
    assert _parse_gold("In FY2022 the effective tax rate was 0.62%", "tax rate in FY2022?") == \
        {"unit": "%", "value": 0.62}
    # entity question never yields a numeric gold
    assert _parse_gold("Las Vegas resorts ~90% of EBITDAR", "Which region had the highest contribution?") is None


def test_gold_parser_prefers_the_period_the_question_asks_about():
    from agentgate.case.public_benchmarks.financebench_adapter import _parse_gold
    got = _parse_gold("Effective tax rate in FY2022 was 0.62%, compared to -14.76% in FY2021",
                      "What was the effective tax rate in FY2022?")
    assert got == {"unit": "%", "value": 0.62}     # not -14.76 (FY2021)


def test_autoadapt_accepts_coarse_precision_without_draft():
    # no separate draft flag: the judging layer's precision floor widens the tolerance to
    # the gold's own precision, so a 2-significant-digit gold is usable as-is
    from agentgate.control.autoadapt import _parse_num
    coarse = _parse_num("$0.40 billion", "How much did it pay?")
    assert coarse == {"unit": "USD billions", "value": 0.4}
    assert "suspicious" not in coarse


def test_precision_floor_widens_tolerance_only_for_coarse_gold():
    from agentgate.case.models import Case, Gold
    from agentgate.evaluator.judging import _check_final
    case = Case(case_id="p", version=1, level="L1", type="numeric", pack="fb",
                input={"query": "q"}, gold=Gold(final={"value": 0.4, "unit": "USD billions",
                                                       "tol_rel": 0.01}), suite="T")
    ok, _r, q = _check_final(case, {"final_json": {"value": 0.389, "unit": "USD billions"},
                                    "answer_text": ""}, {})
    assert ok is True and q.get("tol_widened_by_precision") == 1


# ---------------- shared gold rules: the FinanceBench mis-parse classes ----------------

def _fb(cid):
    import json as _j
    from pathlib import Path
    f = (Path(__file__).resolve().parents[2] / "reference" / "refs" / "code" / "financebench"
         / "data" / "financebench_open_source.jsonl")
    if not f.is_file():
        import pytest
        pytest.skip("financebench seeds not present")
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = _j.loads(line)
        if r.get("financebench_id") == cid:
            return r
    import pytest
    pytest.skip("seed %s missing" % cid)


def test_gold_rules_reject_the_real_misparse_classes():
    from agentgate.case.public_benchmarks.financebench_adapter import _parse_gold
    from agentgate.control.autoadapt import _parse_num
    # (case, expected value or None) — each was a real false FAIL in the FB-150 bank
    cases = {
        "financebench_id_00605": 36.0,    # day inside a date ("January 28, 2023") -> 36
        "financebench_id_00585": 0.62,    # year not a value; period alignment picks FY2022
        "financebench_id_01328": 411.0,   # year not a value
        "financebench_id_00283": 77.78,   # bare number answer
        "financebench_id_02024": None,    # two figures, no period -> ambiguous
        "financebench_id_01079": None,    # bulleted enumeration
        "financebench_id_00382": None,    # "which ..." entity question
        "financebench_id_01290": None,    # entity question
        "financebench_id_00438": None,    # yes/no question with percentages in prose
    }
    for cid, want in cases.items():
        r = _fb(cid)
        got = _parse_gold(r.get("answer", ""), r.get("question", ""))
        val = got["value"] if got else None
        if want is None:
            assert val is None, "%s should stay free_text, got %s" % (cid, val)
        else:
            assert val is not None and abs(val - want) < 1e-9, "%s -> %s (want %s)" % (cid, val, want)
        # both parsers must agree (adapter and autoadapt share gold_rules)
        alt = _parse_num(r.get("answer", ""), r.get("question", ""))
        altv = alt["value"] if alt else None
        if want is None:
            assert altv is None
        else:
            assert altv is not None and abs(altv - want) < 1e-9
