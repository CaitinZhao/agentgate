"""Judging-tightening tests (A1/A2/B1/A4): the response may carry the correct answer in
a different shape than gold — the hard layer must find it before giving up, and a
judging fix must be replayable onto archived runs (rejudge) with the six-dimension
scores folding the recorded rulings back in."""
import json
import os
import tempfile

import pytest

from agentgate.analysis.scores import rescore_after_review, score_run
from agentgate.case.models import Case, Checkpoint, Gold
from agentgate.evaluator.judging import (_check_final, _extract_boolean, _unit_convert,
                                          judge_case)

PACK = {}
from agentgate.trace.normalizer import normalize


# ---------------- A1: typed compare loosening ----------------

def _case(ctype: str, final: dict, checkpoints=None) -> Case:
    return Case(case_id="t-1", version=1, level="L1", type=ctype, pack="fb",
                input={"query": "q"}, gold=Gold(final=final, checkpoints=checkpoints or []),
                suite="T")


def _resp(final_json: dict, answer: str = "") -> dict:
    return {"final_json": final_json, "answer_text": answer}


def test_boolean_finds_stance_in_answer_field():
    # numeric evidence sits in `value`, the verdict in prose — the old checker
    # only read FINAL.value and misjudged this FAIL("未明确回答")
    c = _case("boolean", {"value": "yes"})
    ok, _r, _q = _check_final(c, _resp(
        {"value": 2278, "unit": "USD millions",
         "answer": "Yes — Corning's FY2022 working capital is positive at ~$2,278 million"},
        "working capital is positive..."), PACK)
    assert ok is True


def test_boolean_chinese_and_negative_stance():
    c = _case("boolean", {"value": "no"})
    ok, _r, _q = _check_final(c, _resp({"answer": "不是。该公司未披露该数据。"}), PACK)
    assert ok is True
    c2 = _case("boolean", {"value": "yes"})
    ok2, _r2, _q2 = _check_final(c2, _resp({}, "The metric is neither positive nor negative here"), PACK)
    # "positive" and "negative" both fire -> no resolvable stance -> FAIL, never guessed
    assert ok2 is False


def test_numeric_unit_conversion():
    c = _case("numeric", {"value": 400.0, "unit": "USD millions", "tol_rel": 0.01})
    ok, _r, _q = _check_final(c, _resp({"value": 0.4, "unit": "USD billions"}), PACK)
    assert ok is True
    assert _unit_convert(0.389, "USD billions", "USD millions") == pytest.approx(389.0)


def test_numeric_falls_back_to_answer_text():
    # FINAL json malformed (value null) but the conclusion in the answer carries it
    c = _case("numeric", {"value": 1577.0, "unit": "USD millions", "tol_rel": 0.01})
    ok, _r, q = _check_final(c, _resp(
        {"value": None, "unit": "none"},
        "3M's FY2018 capital expenditures were $1,577 million. FINAL: {\"value\": null}"), PACK)
    assert ok is True
    assert q.get("value_from_text") is True


def test_numeric_text_percent_guarded_from_currency_gold():
    # a percentage can never equal a monetary amount: no conversion, plain mismatch
    c = _case("numeric", {"value": 36.0, "unit": "%", "tol_rel": 0.01})
    ok, _r, _q = _check_final(c, _resp({"value": 36.5, "unit": "%"}), PACK)
    assert ok is False                       # 36.5 vs 36.0 exceeds the 1% tolerance
    c2 = _case("numeric", {"value": 36.0, "unit": "%", "tol_rel": 0.05})
    ok2, _r2, _q2 = _check_final(c2, _resp({"value": 36.5, "unit": "%"}), PACK)
    assert ok2 is True


def test_extractive_number_normalization():
    c = _case("extractive", {"value": "16,525", "aliases": []})
    ok, _r, _q = _check_final(c, _resp({"value": "16525.0 million"}, "total was 16525.0"), PACK)
    assert ok is True


def test_final_schema_required_and_types():
    c = _case("free_text", {"schema": {"required": ["answer", "score"],
                                       "properties": {"score": {"type": "number"}}}})
    ok, results, _q = _check_final(c, _resp({"answer": "x"}, ""), PACK)
    assert ok is False and any(r.name == "final_format" for r in results)
    ok2, results2, _q2 = _check_final(c, _resp({"answer": "x", "score": "high"}, ""), PACK)
    assert ok2 is False and any("must be number" in r.reason for r in results2)
    ok3, _r3, _q3 = _check_final(c, _resp({"answer": "x", "score": 4}, ""), PACK)
    # schema valid -> falls through; free_text without a reference stays undecided
    assert ok3 is None


def test_required_checkpoint_gates_the_verdict():
    c = _case("boolean", {"value": "yes"},
              checkpoints=[Checkpoint(desc="检索原文", signal="tool",
                                      pattern="search_filing", weight=1, required=True)])
    out = judge_case(c, _resp({"value": "yes"}, "yes"), normalize([]), {}, raw_spans=[],
                     tool_blob="")   # no tool call at all
    assert out["verdict"] == "FAIL"
    assert any(r["name"] == "checkpoint_required" for r in out["checks"])


def test_reference_check_rescues_free_text():
    # fact-level match: every reference number present + polarity agrees
    c = _case("free_text", {"reference_answer": "Corporate. Its net revenue was -$473 million."})
    ok, _r, _q = _check_final(c, _resp(
        {"answer": "The Corporate segment, with net revenue of -$473 million."},
        "Corporate segment had -$473 million"), PACK)
    assert ok is True
    # a gold number missing -> stays undecided (PENDING), never a phrasing FAIL
    c2 = _case("free_text", {"reference_answer": "Corporate. Its net revenue was -$473 million."})
    ok2, _r2, _q2 = _check_final(c2, _resp({"answer": "Corporate segment."}, ""), PACK)
    assert ok2 is None


# ---------------- B1: folding recomputes quality, not just success ----------------

def test_rescore_after_review_refolds_quality():
    scores = score_run([
        {"case_id": "a", "verdict": "PENDING", "judge_out": {}, "analysis": {},
         "tokens": 0, "wall_s": 0.0},
    ])
    assert scores["run_scores"]["quality"] is None
    reviews = [{"case_id": "a", "final_verdict": "FAIL", "reviewed_by": "rev",
                "review_note": "wrong conclusion"}]
    scores2 = rescore_after_review(scores, reviews)
    pc = scores2["per_case"][0]
    assert pc["verdict"] == "FAIL" and pc["scores"]["success"] == 0.0
    assert pc["scores"]["quality"] == 30.0     # FAIL, no checkpoints -> 30
    assert scores2["run_scores"]["quality"] == 30.0
    assert scores2["counts"]["pending"] == 0 and scores2["counts"]["fail"] == 1


def test_rescore_with_ai_coverage_overrides_quality():
    scores = score_run([
        {"case_id": "b", "verdict": "PENDING", "judge_out": {}, "analysis": {},
         "tokens": 0, "wall_s": 0.0},
    ])
    scores2 = rescore_after_review(
        scores, [{"case_id": "b", "final_verdict": "PASS", "reviewed_by": "ai:auto",
                  "review_note": "adopt"}],
        ai_coverage={"b": 0.75})
    assert scores2["per_case"][0]["scores"]["quality"] == 75.0
    assert scores2["run_scores"]["quality"] == 75.0


def test_score_run_quality_override():
    rows = [{"case_id": "c", "verdict": "FAIL", "judge_out": {}, "analysis": {},
             "tokens": 0, "wall_s": 0.0}]
    s = score_run(rows, quality_override={"c": 66.0})
    assert s["per_case"][0]["scores"]["quality"] == 66.0


# ---------------- A2: rejudge replay ----------------

@pytest.fixture()
def replay_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGATE_DATA_DIR", str(tmp_path))
    from agentgate.webapp import db
    db.connect().close()                      # schema created on first connect
    uid = db.create_user("rejudger", "pw-123456", "admin", "Rejudge")["id"]
    from agentgate.webapp.banks import create_bank_db
    bank_dir = tmp_path / "banks" / "public" / "tbank"
    bank_dir.mkdir(parents=True)
    create_bank_db(bank_dir / "cases.db")
    from agentgate.case.store import upsert_cases
    cases = [
        Case(case_id="tb-bool", version=1, level="L1", type="boolean", pack="fb",
             suite="tbank", input={"query": "positive working capital?"},
             gold=Gold(final={"value": "yes"})),
        Case(case_id="tb-free", version=1, level="L1", type="free_text", pack="fb",
             suite="tbank", input={"query": "explain"},
             gold=Gold(final={"reference_answer": "The Corporate segment, -$473 million."})),
    ]
    upsert_cases(str(bank_dir / "cases.db"), cases)
    bank_id = db.create_benchmark("tbank", "public", uid, "", "")   # name, visibility, owner, pack, desc
    run = db.create_run("replay-test", "rejudger", uid,
                        [{"bank": "tbank", "levels": []}], "http://127.0.0.1:9", False, 4318)
    rid = run["id"]
    db.add_run_item(rid, "tb-bool", "tbank", "L1", "PENDING", 100, 1.5, "old asi")
    db.add_run_item(rid, "tb-free", "tbank", "L1", "PENDING", 100, 1.5, "old asi")
    db.update_run(rid, status="succeeded", finished_at="2026-09-30T10:00:00",
                  result_dir=str(tmp_path / "results" / rid))
    rd = tmp_path / "results" / rid
    rd.mkdir(parents=True)
    (rd / "answers.jsonl").write_text("\n".join([
        json.dumps({"case_id": "tb-bool",
                    "answer_text": "Yes — it is positive at 2,278.",
                    "final_json": {"value": 2278, "answer": "Yes — positive"}}),
        json.dumps({"case_id": "tb-free",
                    "answer_text": "The Corporate segment, with net revenue of -$473 million.",
                    "final_json": {}})]), encoding="utf-8")
    (rd / "spans_raw.json").write_text(json.dumps([
        {"task_id": "tb-bool", "trace_id": "", "spans": []},
        {"task_id": "tb-free", "trace_id": "", "spans": []}]), encoding="utf-8")
    (rd / "scores.json").write_text(json.dumps(
        score_run([{"case_id": "tb-bool", "verdict": "PENDING", "judge_out": {},
                    "analysis": {}, "tokens": 100, "wall_s": 1.5},
                   {"case_id": "tb-free", "verdict": "PENDING", "judge_out": {},
                    "analysis": {}, "tokens": 100, "wall_s": 1.5}]), ensure_ascii=False))
    yield rid, rd


def test_rejudge_replays_and_updates_everything(replay_env):
    rid, rd = replay_env
    from agentgate.webapp import db
    from agentgate.webapp.rejudge import rejudge_run
    summary = rejudge_run(db.get_run(rid))
    assert summary["rejudged"] == 2
    by = {c["case_id"]: c for c in summary["changes"]}
    assert by["tb-bool"]["to"] == "PASS"      # the boolean cross-field rescue
    assert by["tb-free"]["to"] == "PASS"      # the reference-answer rescue
    items = {i["case_id"]: i for i in db.list_run_items(rid)}
    assert items["tb-bool"]["verdict"] == "PASS"
    scores = json.loads((rd / "scores.json").read_text(encoding="utf-8"))
    assert scores["counts"]["pending"] == 0 and scores["counts"]["pass"] == 2
    assert scores["run_scores"]["success"] == 100.0
    er = {r["task_id"]: r for r in json.loads(
        (rd / "eval_results.json").read_text(encoding="utf-8"))}
    assert er["tb-bool"]["scores"]["deterministic_pass"] is True


def test_rejudge_keeps_human_ruling(replay_env):
    rid, _rd = replay_env
    from agentgate.webapp import db
    db.review_item(rid, "tb-bool", "FAIL", "human-reviewer", "I disagree")
    from agentgate.webapp.rejudge import rejudge_run
    summary = rejudge_run(db.get_run(rid))
    by = {c["case_id"]: c for c in summary["changes"]}
    assert by["tb-bool"]["to"] == "FAIL" and by["tb-bool"]["ruled"] is True
    items = {i["case_id"]: i for i in db.list_run_items(rid)}
    assert items["tb-bool"]["verdict"] == "FAIL"
    assert "kept ruling" in items["tb-bool"]["asi"]


def test_ai_coverage_from_judge(tmp_path):
    (tmp_path / "judge.jsonl").write_text("\n".join([
        json.dumps({"case_id": "x", "coverage": [
            {"point": "p1", "weight": 2.0, "hit": True},
            {"point": "p2", "weight": 1.0, "hit": False}]}),
        json.dumps({"case_id": "y", "coverage": []}),
    ]), encoding="utf-8")
    from agentgate.webapp.rejudge import ai_coverage_from_judge
    cov = ai_coverage_from_judge(tmp_path)
    assert cov["x"] == pytest.approx(2 / 3)
    assert "y" not in cov


# ---------------- A4: adoption policy ----------------

def test_adopt_policy_safety_banks_never_auto_pass():
    from agentgate.webapp.auto_judge import adopt_policy
    sug_hi_pass = {"verdict_suggest": "PASS", "confidence": "high"}
    sug_hi_fail = {"verdict_suggest": "FAIL", "confidence": "high"}
    sug_lo = {"verdict_suggest": "FAIL", "confidence": "low"}
    assert adopt_policy("injection", sug_hi_pass) == ""     # never auto-clear safety
    assert adopt_policy("harmbench", sug_hi_pass) == ""
    assert adopt_policy("injection", sug_hi_fail) == "FAIL"
    assert adopt_policy("fb-150", sug_hi_pass) == "PASS"
    assert adopt_policy("fb-150", sug_lo) == ""             # low confidence stays a suggestion


def test_ai_judge_job_records_adopted():
    from agentgate.webapp import ai_judge_jobs
    judged = {}

    def judge_fn(cid):
        return {"verdict_suggest": "PASS", "confidence": "high", "coverage": []}

    def adopt_fn(cid, sug):
        judged[cid] = sug["verdict_suggest"]
        return "PASS"

    job_id = ai_judge_jobs.enqueue("run-x", ["c1", "c2"], judge_fn, adopt_fn=adopt_fn)
    for _ in range(100):
        st = ai_judge_jobs.snapshot(job_id)
        if st["status"] == "done":
            break
        import time
        time.sleep(0.05)
    assert st["status"] == "done"
    assert all(j["adopted"] == "PASS" for j in st["judged"])
    assert judged == {"c1": "PASS", "c2": "PASS"}
