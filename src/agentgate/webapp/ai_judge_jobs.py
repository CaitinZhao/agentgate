"""Async batch AI-judge jobs: enqueue + poll instead of one long HTTP request.

The POST /runs/{run_id}/ai-judge-batch endpoint validates and enqueues; a daemon
thread judges the snapshot of PENDING case ids one by one (the LLM gateway is the
bottleneck, so jobs run serialized behind a global lock). The frontend polls
GET /runs/{run_id}/ai-judge-jobs/latest for progress and can POST
/ai-judge-jobs/{id}/cancel between cases.

Jobs are in-process state: a platform restart clears them (the judge.jsonl rows
already written survive; re-running the batch simply re-judges what is left).
"""
import threading
import time
import traceback
import uuid
from typing import Callable, Dict, List, Optional

_lock = threading.Lock()
_jobs: Dict[str, Dict] = {}          # job_id -> state
_run_index: Dict[str, str] = {}      # run_id -> latest job_id
_run_lock = threading.Lock()         # serializes the actual LLM work across jobs


def enqueue(run_id: str, case_ids: List[str],
            judge_fn: Callable[[str], Dict]) -> str:
    """Create a job for the given PENDING case ids and start it in the background.

    judge_fn(case_id) -> suggestion dict; raises on per-case failure (recorded in
    job["failed"], the loop continues). If a job is already active for this run,
    that job's id is returned instead — enqueue is idempotent per run.
    """
    job_id = uuid.uuid4().hex[:12]
    state = {"id": job_id, "run_id": run_id, "status": "running", "cancel": False,
             "total": len(case_ids), "done": 0, "judged": [], "failed": [],
             "queued": len(case_ids), "started_at": time.time(), "error": ""}
    with _lock:
        _jobs[job_id] = state
        _run_index[run_id] = job_id

    def run():
        with _run_lock:                       # LLM-bound: one batch at a time
            for case_id in case_ids:
                with _lock:
                    if _jobs[job_id]["cancel"]:
                        _jobs[job_id]["status"] = "cancelled"
                        break
                try:
                    sug = judge_fn(case_id)
                    with _lock:
                        st = _jobs[job_id]
                        st["judged"].append({
                            "case_id": case_id,
                            "verdict_suggest": sug.get("verdict_suggest"),
                            "score": sug.get("score"),
                            "covered": len(sug.get("coverage") or [])})
                        st["done"] += 1
                except Exception as e:
                    with _lock:
                        st = _jobs[job_id]
                        st["failed"].append({"case_id": case_id, "error": str(e)[:300]})
                        st["done"] += 1
            else:
                with _lock:
                    _jobs[job_id]["status"] = "done"
            with _lock:
                if _jobs[job_id]["status"] == "running":
                    _jobs[job_id]["status"] = "done"
                _jobs[job_id]["finished_at"] = time.time()

    threading.Thread(target=run, daemon=True, name="ai-judge-job-" + job_id).start()
    return job_id


def snapshot(job_id: str) -> Optional[Dict]:
    """Public view of one job (drops the private cancel flag)."""
    with _lock:
        st = _jobs.get(job_id)
        if not st:
            return None
        return {k: (v if k != "cancel" else None) for k, v in st.items()
                if k in ("id", "run_id", "status", "total", "done", "judged",
                         "failed", "started_at", "finished_at", "error")} | \
               {"remaining": max(st["total"] - st["done"], 0)}


def latest_for_run(run_id: str) -> Optional[Dict]:
    with _lock:
        job_id = _run_index.get(run_id)
    return snapshot(job_id) if job_id else None


def cancel(job_id: str) -> bool:
    with _lock:
        st = _jobs.get(job_id)
        if not st or st["status"] != "running":
            return False
        st["cancel"] = True
    return True


def sweep(max_age_s: float = 3600.0) -> None:
    """Drop finished jobs older than max_age_s (called opportunistically)."""
    now = time.time()
    with _lock:
        for jid in [j for j, st in _jobs.items()
                    if st["status"] != "running" and now - st.get("finished_at", now) > max_age_s]:
            _run_index.pop(_jobs[jid]["run_id"], None) if _run_index.get(_jobs[jid]["run_id"]) == jid else None
            _jobs.pop(jid, None)
