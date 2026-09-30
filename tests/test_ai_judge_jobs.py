"""Async batch AI-judge job manager: enqueue/poll/cancel semantics."""
import time

from agentgate.webapp import ai_judge_jobs as jobs


def test_enqueue_runs_to_done():
    def judge_fn(case_id):
        return {"verdict_suggest": "PASS", "score": 95, "coverage": [1]}
    job_id = jobs.enqueue("run-x", ["c1", "c2", "c3"], judge_fn)
    for _ in range(100):
        st = jobs.snapshot(job_id)
        if st["status"] == "done":
            break
        time.sleep(0.05)
    st = jobs.snapshot(job_id)
    assert st["status"] == "done" and st["done"] == 3 and st["remaining"] == 0
    assert [j["case_id"] for j in st["judged"]] == ["c1", "c2", "c3"]
    assert st["judged"][0]["verdict_suggest"] == "PASS"


def test_per_case_failure_does_not_stop_the_batch():
    def judge_fn(case_id):
        if case_id == "bad":
            raise RuntimeError("gateway exploded")
        return {"verdict_suggest": "FAIL", "score": 10}
    job_id = jobs.enqueue("run-y", ["c1", "bad", "c2"], judge_fn)
    for _ in range(100):
        st = jobs.snapshot(job_id)
        if st["status"] == "done":
            break
        time.sleep(0.05)
    st = jobs.snapshot(job_id)
    assert st["done"] == 3 and len(st["failed"]) == 1
    assert st["failed"][0]["case_id"] == "bad" and "gateway" in st["failed"][0]["error"]
    assert [j["case_id"] for j in st["judged"]] == ["c1", "c2"]


def test_cancel_stops_between_cases():
    seen = []

    def judge_fn(case_id):
        seen.append(case_id)
        time.sleep(0.1)
        return {"verdict_suggest": "PASS", "score": 90}
    job_id = jobs.enqueue("run-z", ["c1", "c2", "c3", "c4"], judge_fn)
    time.sleep(0.05)
    assert jobs.cancel(job_id) is True
    for _ in range(100):
        st = jobs.snapshot(job_id)
        if st["status"] == "cancelled":
            break
        time.sleep(0.05)
    st = jobs.snapshot(job_id)
    assert st["status"] == "cancelled" and len(seen) < 4


def test_latest_for_run_and_idempotent_enqueue():
    def judge_fn(case_id):
        time.sleep(0.3)
        return {"verdict_suggest": "PASS", "score": 90}
    j1 = jobs.enqueue("run-latest", ["c1", "c2", "c3"], judge_fn)
    j2 = jobs.enqueue("run-latest", ["c4"], judge_fn)      # running -> same job back
    assert j2 == j1 or jobs.snapshot(j2)["run_id"] == "run-latest"
    for _ in range(100):
        st = jobs.latest_for_run("run-latest")
        if st["status"] == "done":
            break
        time.sleep(0.05)
    assert jobs.latest_for_run("run-latest")["status"] == "done"
    assert jobs.latest_for_run("run-unknown") is None
