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
    """The guard must fire on a REAL pipeline artifact.

    Written against `score_run` output (not hand-written JSON): the previous version of
    this test fed the guard exactly the field it wanted, so it passed while the production
    path never produced that field — a false green that hid a coverage regression.
    """
    from agentgate.analysis.scores import score_run
    # a case whose trace recorded work but whose archived count is 0 (the original bug)
    scores = score_run([_row("a", "PASS", judge_out={"process": {"n_model_calls": 0,
                                                                 "n_tool_calls": 5}})])
    assert "model_calls" in scores["per_case"][0]        # the field MUST be produced by the pipeline
    (tmp_path / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
    (tmp_path / "spans_raw.json").write_text(json.dumps(
        [{"task_id": "a", "spans": [{"name": "llm.call"}]}]), encoding="utf-8")
    res = check_artifacts(str(tmp_path))
    assert res["degraded"] is True, res
    assert any(f["code"] == "model_calls_contradiction" for f in res["findings"])


def test_check_artifacts_clean_when_counts_match(tmp_path):
    from agentgate.analysis.scores import score_run
    scores = score_run([_row("a", "PASS", judge_out={"process": {"n_model_calls": 5,
                                                                 "n_tool_calls": 3}})])
    (tmp_path / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
    (tmp_path / "spans_raw.json").write_text(json.dumps(
        [{"task_id": "a", "spans": [{"name": "llm.call"}]}]), encoding="utf-8")
    res = check_artifacts(str(tmp_path))
    assert res["ok"] is True, res


def test_check_artifacts_warns_when_counts_absent(tmp_path):
    """An archive without the count is UNCHECKED, not silently consistent."""
    (tmp_path / "scores.json").write_text(json.dumps({
        "total": 80.0, "counts": {"pass": 1, "fail": 0, "pending": 0, "judged": 1},
        "run_scores": {"safety": 100.0},
        "per_case": [{"case_id": "a", "verdict": "PASS", "scores": {"safety": 100.0}}]}),
        encoding="utf-8")
    (tmp_path / "spans_raw.json").write_text(json.dumps(
        [{"task_id": "a", "spans": [{"name": "llm.call"}]}]), encoding="utf-8")
    res = check_artifacts(str(tmp_path))
    assert any(f["code"] == "model_calls_unchecked" for f in res["findings"])


def _fb(cid):
    """One FinanceBench seed row (skips when the reference data is not present)."""
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


def test_zero_gold_uses_explicit_absolute_tolerance():
    from agentgate.case.models import Case, Gold
    from agentgate.evaluator.judging import _check_final
    def mk(g):
        return Case(case_id="z", version=1, level="L1", type="numeric", pack="fb",
                    input={"query": "q"}, gold=Gold(final=g), suite="T")
    ok, _r, q = _check_final(mk({"value": 0, "unit": "%"}),
                             {"final_json": {"value": 0.005, "unit": "%"}, "answer_text": ""}, {})
    assert ok is True and q.get("abs_tol_applied") == 0.01
    ok2, _r2, _q2 = _check_final(mk({"value": 0, "unit": "%"}),
                                 {"final_json": {"value": 0.5, "unit": "%"}, "answer_text": ""}, {})
    assert ok2 is False
    ok3, _r3, _q3 = _check_final(mk({"value": 0, "unit": "%", "tol_abs": 1}),
                                 {"final_json": {"value": 0.5, "unit": "%"}, "answer_text": ""}, {})
    assert ok3 is True


def test_change_and_enum_cases_stay_free_text():
    from agentgate.case.gold_rules import ENUM_RE, quantity_value
    # FinanceBench writes bullets WITHOUT a space
    blob = chr(10) + "-Decrease in gross Margin" + chr(10) + "-mostly offset by pricing"
    assert ENUM_RE.search(blob) is not None
    assert ENUM_RE.search("-14.76%") is None          # a negative number is not a bullet
    # two-period change question -> the gold would be a delta, not a level
    v, why = quantity_value("changed from 20% in FY2021 to 23% in FY 2022.",
                            "How much has the effective tax rate changed between FY2021 and FY2022?")
    assert v is None and "change" in why


def test_mixed_archive_does_not_false_positive(tmp_path):
    """OTel spans and the in-response audit coexist: a case that legitimately has 0 model
    calls and no spans of its OWN must not be flagged just because a sibling case has spans.
    (The guard must join PER CASE, not compare the two sets globally.)"""
    (tmp_path / "scores.json").write_text(json.dumps({
        "total": 80.0, "counts": {"pass": 2, "fail": 0, "pending": 0, "judged": 2},
        "run_scores": {"safety": 100.0},
        "per_case": [{"case_id": "A", "verdict": "PASS", "model_calls": 5,
                      "scores": {"safety": 100.0}},
                     {"case_id": "B", "verdict": "PASS", "model_calls": 0,
                      "scores": {"safety": 100.0}}]}), encoding="utf-8")
    (tmp_path / "spans_raw.json").write_text(json.dumps([
        {"task_id": "A", "spans": [{"name": "llm.call"}]},
        {"task_id": "B", "spans": []}]), encoding="utf-8")
    res = check_artifacts(str(tmp_path))
    assert res["ok"] is True, res
    assert not any(f["code"] == "model_calls_contradiction" for f in res["findings"])


def test_contradiction_is_per_case(tmp_path):
    """A true contradiction is one where the SAME case reports 0 and holds spans."""
    (tmp_path / "scores.json").write_text(json.dumps({
        "total": None, "counts": {"pass": 1, "fail": 0, "pending": 0, "judged": 1},
        "run_scores": {"safety": 100.0},
        "per_case": [{"case_id": "B", "verdict": "PASS", "model_calls": 0,
                      "scores": {"safety": 100.0}}]}), encoding="utf-8")
    (tmp_path / "spans_raw.json").write_text(json.dumps(
        [{"task_id": "B", "spans": [{"name": "llm.call"}]}]), encoding="utf-8")
    res = check_artifacts(str(tmp_path))
    assert res["degraded"] is True
    assert any(f["code"] == "model_calls_contradiction" for f in res["findings"])


def test_partial_missing_counts_warn_and_name_cases(tmp_path):
    """A PARTIAL miss must warn too: older rows keep the pre-count key set, so skipping
    them silently would leave ~20% of a run unchecked."""
    (tmp_path / "scores.json").write_text(json.dumps({
        "total": None, "counts": {"pass": 1, "fail": 0, "pending": 1, "judged": 1},
        "run_scores": {"safety": 100.0},
        "per_case": [{"case_id": "A", "verdict": "PASS", "model_calls": 5,
                      "scores": {"safety": 100.0}},
                     {"case_id": "B", "verdict": "PENDING",
                      "scores": {"safety": None}}]}), encoding="utf-8")
    (tmp_path / "spans_raw.json").write_text(json.dumps([
        {"task_id": "A", "spans": [{"name": "llm.call"}]},
        {"task_id": "B", "spans": [{"name": "llm.call"}]}]), encoding="utf-8")
    res = check_artifacts(str(tmp_path))
    warn = next(f for f in res["findings"] if f["code"] == "model_calls_unchecked")
    assert "B" in warn["detail"] and warn["severity"] == "warning"


def test_every_verdict_path_persists_counts():
    """Every score_case return path must carry the counts: a row missing them makes the
    guard skip it, and a skip is how 19% of a run stayed unverified."""
    from agentgate.analysis.scores import score_run
    for verdict, jo in (("PASS", {"process": {"n_model_calls": 5, "n_tool_calls": 3}}),
                        ("PENDING", {"process": {"n_model_calls": 2, "n_tool_calls": 1}}),
                        ("SKIPPED", {}),
                        ("FAIL", {"process": {"n_model_calls": 4, "n_tool_calls": 2},
                                  "target_error": True})):
        row = score_run([_row("x", verdict, judge_out=jo)])["per_case"][0]
        assert "model_calls" in row, "verdict %s lost the count" % verdict


def test_null_count_is_unchecked_not_consistent(tmp_path):
    """A null count is UNKNOWN, not verified: the analyzer can throw and leave
    analysis={"error": ...}, persisting model_calls=None. Missing key and null must behave
    identically; only a real number counts as checked, and only a real 0 can contradict."""
    def run(rows, spans):
        (tmp_path / "scores.json").write_text(json.dumps({
            "total": None, "counts": {"pass": 0, "fail": 0, "pending": len(rows), "judged": 0},
            "run_scores": {"safety": None}, "per_case": rows}), encoding="utf-8")
        (tmp_path / "spans_raw.json").write_text(json.dumps(spans), encoding="utf-8")
        return check_artifacts(str(tmp_path))

    llm = lambda cid: {"task_id": cid, "spans": [{"name": "llm.call"}]}
    # null + own spans -> unchecked warning (was silently "ok")
    r = run([{"case_id": "D", "verdict": "PENDING", "model_calls": None,
              "scores": {"safety": None}}], [llm("D")])
    assert any(f["code"] == "model_calls_unchecked" for f in r["findings"])
    # missing key behaves the same
    r2 = run([{"case_id": "E", "verdict": "PENDING", "scores": {"safety": None}}], [llm("E")])
    assert any(f["code"] == "model_calls_unchecked" for f in r2["findings"])
    # a real 0 with own spans is still the hard error
    r3 = run([{"case_id": "F", "verdict": "PASS", "model_calls": 0,
               "scores": {"safety": 100.0}}], [llm("F")])
    assert r3["degraded"] is True
    assert any(f["code"] == "model_calls_contradiction" for f in r3["findings"])
    # a real 0 with NO spans of its own is clean (audit-only case)
    r4 = run([{"case_id": "G", "verdict": "PASS", "model_calls": 0,
               "scores": {"safety": 100.0}}], [{"task_id": "G", "spans": []}])
    assert r4["ok"] is True and r4["findings"] == []


def test_localization_tables_have_no_broken_backreferences():
    """A replacement that references a capture group must actually carry that reference.

    A non-raw replacement string turns  into the control character , which silently
    DROPS the value ("平均每题  次工具调用" with the number missing). A translation table is
    exactly the kind of place where that goes unnoticed, so it is checked mechanically.
    """
    import re as _re
    from agentgate.analysis.scores import _IMPROVE_ZH, _NOTE_ZH
    for table, name in ((_NOTE_ZH, "_NOTE_ZH"), (_IMPROVE_ZH, "_IMPROVE_ZH")):
        for pattern, repl in table:
            groups = _re.compile(pattern).groups
            if not groups:
                continue
            # every group must be referenced, and no control char may stand in for one
            missing = [g for g in range(1, groups + 1) if ("%s%d" % (chr(92), g)) not in repl]
            assert not missing, (
                "%s: pattern %r has %d group(s) but the replacement %r omits %s "
                "(a non-raw string turns \1 into a control char and drops the value)"
                % (name, pattern, groups, repl, missing))
            assert chr(1) not in repl and chr(2) not in repl, (
                "%s: replacement %r contains a control character where a backreference "
                "belongs" % (name, repl))
