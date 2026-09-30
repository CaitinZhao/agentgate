"""Serial evaluation worker (doc 33 §7): one global thread consumes the runs queue.

The worker never decides evaluation outcomes — it only orchestrates: resolve the case set
(bank filter + the run creator's overrides + level filters + the banks' requirement
contract), hand cases to run_case_set, stream per-case verdicts into run_items, archive
reports under data/results/<run_id>/, and honor cancellation between cases.
"""
import json
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Dict, List, Tuple

from ..control.service import run_case_set
from . import banks, db

VALID_LEVELS = {"L0", "L1", "L2"}


def make_target(url: str, llm_base_url: str = ""):
    """Target factory (module-level so tests can monkeypatch it with an offline MockTarget).

    llm_base_url: per-run proxy address delivered to capable agents via the optional
    /invoke field (empty = agent uses its own LLM base)."""
    from ..run.targets.base import HttpTarget
    return HttpTarget(url, llm_base_url=llm_base_url)


def _as_json(value, default):
    """Accept both a JSON string (DB rows) and an already-parsed object (dry-run probes)."""
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def agent_capabilities(url: str, timeout: float = 5.0):
    """Pre-flight capability probe: GET {target}/capabilities -> dict, or None when the agent
    does not expose it (permissive: unknown agents are never skipped)."""
    try:
        import httpx
        r = httpx.get(url.rstrip("/") + "/capabilities", timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _trace_wait_s(caps) -> float:
    """Per-case wait for asynchronously exported OTel spans, derived from /capabilities.

    Agents declaring "traces": true get the full window; agents with a capabilities
    document that lacks the flag are taken at their word (short window); unknown
    agents (no endpoint) keep a middle window as a safety margin."""
    if isinstance(caps, dict):
        return 15.0 if caps.get("traces") or caps.get("otel_export") else 3.0
    return 8.0


def _repo_cases_dirs():
    """The source-tree cases/ dir (context files ship with the repo). Resolves for editable
    installs (parents of this file) and for the container's source copy (/app/agentgate);
    nothing when the package sits in site-packages without a source tree next to it."""
    seen = set()
    for base in [Path(__file__).resolve().parents[i] for i in range(1, 6)] +                 [Path("/app/agentgate"), Path.cwd()]:
        d = base / "cases"
        if d.is_dir() and str(d) not in seen:
            seen.add(str(d))
            yield d


def resolve_run_cases(run: Dict) -> Tuple[List, Dict[str, str], List[Path], List]:
    """Compute the exact case set of a run (doc 33 §7: the agent never picks cases itself).

    Per bank in bank_filter: load its cases.db; for public banks apply the run creator's
    overrides first (disabled -> removed; my level -> replaces the default), then the run's
    level filter. Bank `requirements` (fairness contract): a bank may require a profile the
    agent must support — mismatched cases are SKIPPED with a reason (never FAIL) when the
    agent's /capabilities says it does not support it; tool_strict=false switches to
    outcome-only judging (required_tools dropped; forbidden_tools red lines stay).
    Duplicate case_ids across banks: first bank wins.
    Returns (cases, {case_id: bank_name}, context search roots, skipped[(case, reason)]).
    """
    bank_filter = _as_json(run.get("bank_filter"), [])
    creator = db.get_user(run["created_by"]) if run.get("created_by") else None
    creator_id = creator["id"] if creator else -1
    want_ids = set(_as_json(run.get("case_ids"), [])) or None
    cases, bank_of, roots, seen, skipped = [], {}, [], set(), []
    target_url = run.get("target_url") or ""
    caps = agent_capabilities(target_url) if target_url else None
    caps_profiles = set((caps or {}).get("profiles") or [])
    for entry in bank_filter:
        name = entry.get("bank", "")
        levels = set(entry.get("levels") or [])
        bank = db.get_benchmark(name)
        if not bank:
            continue                       # bank deleted after enqueue
        owner = db.get_user(bank["owner_id"]) if bank.get("owner_id") else None
        path = banks.bank_db_path(bank, owner["username"] if owner else "")
        if not Path(path).exists():
            continue
        try:
            requirements = json.loads(bank.get("requirements") or "{}")
        except ValueError:
            requirements = {}
        bank_cases = banks.load_bank_cases(path)
        if bank["visibility"] == "public":
            for ov in db.list_overrides(creator_id, bank["id"]):
                if not ov["enabled"]:
                    bank_cases = [c for c in bank_cases if c.case_id != ov["case_id"]]
                elif ov["level"]:
                    for c in bank_cases:
                        if c.case_id == ov["case_id"]:
                            c.level = ov["level"]

        def _wanted(c) -> bool:
            if levels and c.level.upper() not in levels:
                return False
            if want_ids is not None and c.case_id not in want_ids:
                return False
            return True
        candidates = [c for c in bank_cases if _wanted(c)]

        need_profile = requirements.get("profile", "")
        # env_scope: "agent-side" (default) = agent must provide the environment
        # (profile handshake -> SKIP when unsupported); "judge-only" = assets exist
        # only for SCORING (e.g. Spider's SQLite) and the agent may answer by any
        # means - a missing profile degrades the profile prompt instead of skipping.
        env_scope = requirements.get("env_scope", "agent-side")
        if need_profile and env_scope != "judge-only" and caps is not None and caps_profiles \
                and need_profile not in caps_profiles:
            reason = ("agent does not support required profile '%s' (bank requirement); "
                      "provision the environment for the agent or drop the requirement"
                      % need_profile)
            skipped.extend((c, reason) for c in candidates)
            continue
        if requirements.get("tool_strict") is False:
            for c in candidates:       # outcome-only judging: tool checkpoints are advisory
                c.gold.checkpoints = [cp for cp in c.gold.checkpoints
                                      if cp.signal != "tool"]
        for c in candidates:
            if c.case_id in seen:
                continue
            seen.add(c.case_id)
            if not c.suite:
                c.suite = bank["name"]
            if not c.pack:
                c.pack = requirements.get("pack") or ""
            # W7 completeness: the capability handshake checks the agent DECLARES the
            # profile; the /invoke must actually ASK for it. Cases authored without an
            # explicit profile inherit the bank requirement here.
            if need_profile and not c.input.get("profile"):
                c.input["profile"] = need_profile
            cases.append(c)
            bank_of[c.case_id] = bank["name"]
        roots.append(Path(path).parent)
    roots += list(_repo_cases_dirs())      # repo-shipped context files (container-safe)
    roots += [Path("cases"), Path(".")]    # legacy context_file roots still honored
    return cases, bank_of, roots, skipped


class Worker(threading.Thread):
    """Runs the queue. AGENTGATE_WORKER_CONCURRENCY (default 1) executes up to N runs in
    parallel — runs are independent (per-bank), the default stays serial so the deployed
    platform keeps its exact single-run semantics; bank-level parallelism only changes
    wall-clock, never per-case behavior (spans are matched per trace_id)."""

    def __init__(self, receiver=None, poll_seconds: float = 2.0, proxy_sink: str = ""):
        super().__init__(daemon=True, name="agentgate-worker")
        self.receiver = receiver
        self.poll_seconds = poll_seconds
        self.proxy_sink = proxy_sink
        self.stop_event = threading.Event()
        self._last_retention_sweep = 0.0

    def stop(self):
        self.stop_event.set()

    def _claim_run(self):
        """Atomically claim the next queued run (BEGIN IMMEDIATE: two worker threads
        must never execute the same run)."""
        import sqlite3
        con = db.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM runs WHERE status='queued' AND "
                "(scheduled_for IS NULL OR scheduled_for <= ?) "
                "ORDER BY created_at, id LIMIT 1", (db.now(),)).fetchone()
            if row is None:
                con.execute("COMMIT")
                return None
            con.execute("UPDATE runs SET status='running', started_at=? WHERE id=?",
                        (db.now(), row["id"]))
            con.execute("COMMIT")
            return dict(row) if not isinstance(row, sqlite3.Row) else dict(row)
        except Exception:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            con.close()

    def run(self):
        import os
        from concurrent.futures import ThreadPoolExecutor
        try:
            n = max(1, int(os.environ.get("AGENTGATE_WORKER_CONCURRENCY", "1") or 1))
        except ValueError:
            n = 1
        if n == 1:                       # default: exact serial semantics
            while not self.stop_event.is_set():
                try:
                    run = self._claim_run()
                    if run:
                        self.execute_run(run)
                    else:
                        self._sweep_retention()
                        self.stop_event.wait(self.poll_seconds)
                except Exception:
                    traceback.print_exc()  # the worker loop must survive any single failure
                    self.stop_event.wait(self.poll_seconds)
            return
        with ThreadPoolExecutor(max_workers=n, thread_name_prefix="agentgate-run") as ex:
            inflight = set()
            while not self.stop_event.is_set():
                try:
                    if len(inflight) < n:
                        run = self._claim_run()
                        if run:
                            inflight.add(ex.submit(self.execute_run, run))
                            continue
                    for f in [f for f in inflight if f.done()]:
                        inflight.discard(f)
                    self._sweep_retention()
                    self.stop_event.wait(self.poll_seconds)
                except Exception:
                    traceback.print_exc()
                    self.stop_event.wait(self.poll_seconds)

    def _sweep_retention(self):
        """Hourly: delete archived result dirs older than the retention window (runs rows stay)."""
        now_ts = time.time()
        if self._last_retention_sweep and now_ts - self._last_retention_sweep < 3600:
            return
        self._last_retention_sweep = now_ts
        days = db.get_int_setting("report_retention_days", 30)
        for r in db.retention_expired_dirs(days):
            shutil.rmtree(r["result_dir"], ignore_errors=True)
            db.update_run(r["id"], result_dir="")
        # generated working files (red-marked excel etc.) live for a day at most
        tmp = db.data_root() / "tmp"
        if tmp.is_dir():
            for f in tmp.glob("bulk-*"):
                if now_ts - f.stat().st_mtime > 86400:
                    f.unlink(missing_ok=True)

    def execute_run(self, run: Dict):
        rid = run["id"]
        db.update_run(rid, status="running", started_at=db.now())
        cases, bank_of, roots, skipped = resolve_run_cases(run)
        db.update_run(rid, total_cases=len(cases) + len(skipped))
        if not cases and not skipped:
            db.update_run(rid, status="failed", finished_at=db.now(),
                          error="no runnable cases after filters (bank empty/offline, "
                                "levels too narrow, or case_ids matched nothing)")
            return
        # requirement skips are recorded as first-class SKIPPED items (never FAILures)
        for case, reason in skipped:
            db.add_run_item(rid, case.case_id, bank_of.get(case.case_id, ""),
                            case.level, "SKIPPED", 0, 0.0, reason)
        if not cases:
            db.update_run(rid, status="succeeded", finished_at=db.now(),
                          gate_decision="SKIPPED(%d)" % len(skipped), score="n/a",
                          result_dir="")
            return
        out_dir = db.data_root() / "results" / rid

        def on_done(case_id, bank, level, verdict, tokens, wall_time_s, asi):
            db.add_run_item(rid, case_id, bank_of.get(case_id, bank), level, verdict,
                            tokens, wall_time_s, asi)

        def cancel_check():
            fresh = db.get_run(rid)
            return bool(fresh and fresh["cancel_requested"])

        try:
            # message-level recording: when the run opts in, deliver the proxy address
            # (as seen from the agent) per case via the optional /invoke llm_base_url field
            llm_base = db.get_setting("proxy_agent_url", "").strip() \
                if run.get("proxy_enabled") else ""
            target = make_target(run["target_url"], llm_base)
            # optional AI enhancements ride the run creator's User-Center config (doc 34;
            # unset -> plain deterministic judging). Never logged, never sent to the agent.
            ai_cfg = db.ai_config_for(run["created_by"])                 if (run.get("created_by") and run.get("ai_assist", 1)) else None
            summary = run_case_set(None, target, str(out_dir),
                                   receiver_port=run.get("receiver_port") or 4318,
                                   cases_list=cases, receiver=self.receiver,
                                   on_case_done=on_done, cancel_check=cancel_check,
                                   search_roots=roots,
                                   proxy_sink=self.proxy_sink or None,
                                   data_root=db.data_root(),
                                   repeat_k=run.get("stability_k") or 1,
                                   ai_cfg=ai_cfg,
                                   trace_wait_s=_trace_wait_s(
                                       agent_capabilities(run["target_url"])
                                       if run.get("target_url") else None))
        except Exception as e:
            traceback.print_exc()          # full stack in docker logs; runs.error keeps the repr
            db.update_run(rid, status="failed", finished_at=db.now(), error=repr(e)[:500])
            return
        status = "cancelled" if summary.get("cancelled") else "succeeded"
        n_err = int((summary.get("meta") or {}).get("target_errors") or 0)
        if status == "succeeded" and n_err and n_err >= len(summary.get("results") or []):
            # every case failed to even reach the target (connection refused etc.):
            # nothing was measured — the run is a failed evaluation, not a low score
            db.update_run(rid, status="failed", finished_at=db.now(),
                          error="target unreachable: %d cases failed to invoke "
                                "(check the target agent's address/health)" % n_err)
            return
        db.update_run(rid, status=status, finished_at=db.now(),
                      gate_decision=summary["gate"]["decision"],
                      score=(str(summary["scores"].get("total"))
                             if summary.get("scores", {}).get("total") is not None
                             else str(summary["gate"].get("score", ""))),
                      result_dir=str(out_dir))
