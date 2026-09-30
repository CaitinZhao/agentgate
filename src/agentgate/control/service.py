"""Evaluation orchestration: start the receiver -> run cases -> judge -> gate -> export reports (observe only, never decide).

Doc 35 wiring: analysis runs BEFORE judging (trajectory signals feed the hard layer);
each case produces (EvalResult, judge_out); the run writes scores.json (six-dimension
hexagon + diagnostic cards). Optional per-run enhancements: repeat_k stability
(pass^k, off by default) and per-user AI config (judge suggestions for free_text —
suggestions only unless auto-adopt was explicitly enabled — plus the AI report summary).
"""
import datetime
import json
import re
from typing import Dict
from pathlib import Path

from ..case.loader import load_cases
from ..case import packs as pack_reg
from ..evaluator.runner import evaluate_case
from ..result.export.json_report import export as export_json
from ..result.gates import evaluate_gate
from ..result.report import build_report
from ..run.engine import RunEngine
from ..trace.receivers.otlp_http import OTLPHTTPReceiver


def _expand_context(cases, search_roots):
    """Memory profile: expand input.context_file (relative to the cases root) into input.context text.

    Expansion happens only for selected cases right before the run (loading the full bank
    stays cheap); a missing file aborts with an error. Contexts over AGENTGATE_MAX_CONTEXT_CHARS
    (default 90000 chars, ~23k tokens, below common gateways' 32k token limit) are truncated
    head+tail with an omission marker — cases whose gold sits in the dropped middle become
    distorted; the run reports the truncated count.
    """
    import os
    max_ctx = int(os.environ.get("AGENTGATE_MAX_CONTEXT_CHARS", "90000"))
    expanded, truncated = 0, 0
    for c in cases:
        ref = c.input.get("context_file")
        if not ref:
            continue
        path = next((Path(root) / ref for root in search_roots
                     if (Path(root) / ref).is_file()), None)
        if path is None:
            raise FileNotFoundError("context_file not resolvable: %s (case=%s, search roots=%s)"
                                    % (ref, c.case_id, [str(r) for r in search_roots]))
        text = json.loads(path.read_text(encoding="utf-8"))["dialogue"]
        if len(text) > max_ctx:
            head, tail = text[:int(max_ctx * 0.6)], text[-int(max_ctx * 0.4):]
            text = head + "\n\n[CONTEXT NOTE] The middle part was omitted due to the gateway prompt length limit.\n\n" + tail
            truncated += 1
        c.input["context"] = text
        del c.input["context_file"]
        expanded += 1
    if truncated:
        print("context truncated (head+tail): %d cases" % truncated)
    return expanded


def _read_proxy_records(sink: str, t0: float, t1: float) -> list:
    """Read the llm-proxy JSONL sink and collect records into this case by call time (sequential eval, ±2s window).

    ts format 2026-09-22T10:00:00.123 (local timezone); windows compared in epoch seconds.
    """
    p = Path(sink)
    if not p.exists():
        return []
    out = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        try:
            ts = datetime.datetime.fromisoformat(rec.get("ts", "")).timestamp()
        except ValueError:
            continue
        if t0 - 2 <= ts <= t1 + 2:
            out.append(rec)
    return out


def _tool_blob(model_calls: list) -> str:
    """Concatenated tool arguments blob (checkpoint signal=tool matching)."""
    parts = []
    for mc in model_calls or []:
        for ch in (mc.get("response") or {}).get("choices", []):
            for tc in (ch.get("message") or {}).get("tool_calls") or []:
                fn = tc.get("function") or {}
                parts.append(str(fn.get("name", "")))
                parts.append(str(fn.get("arguments", "")))
    return " ".join(parts)


def _outcome_key(response, verdict) -> tuple:
    """Normalized outcome of one repeat: (verdict, normalized FINAL json, normalized
    answer tail) — "the same result" means all three agree (formatting noise ignored)."""
    def _norm(v):
        if isinstance(v, dict):
            return {k: _norm(x) for k, x in sorted(v.items())}
        if isinstance(v, list):
            return [_norm(x) for x in v]
        if isinstance(v, float):
            return round(v, 4)
        return str(v).strip().lower()
    fj = response.get("final_json") or {}
    ans = re.sub(r"\s+", " ", str(response.get("answer_text", "")).strip().lower())
    return (verdict, json.dumps(_norm(fj), ensure_ascii=False, sort_keys=True), ans[-300:])


def _native_for(case, response, trace, pack: Dict, search_roots, judge_suggest,
                judge_out=None):
    """Per-case native (dataset-original) reading. bfcl/gaia/spider/airbench/agentdojo
    were already computed by the judging hard layer (judge_out["native"]) — reuse;
    harmbench needs the judge suggestion, so it is computed here."""
    if judge_out and isinstance(judge_out.get("native"), dict):
        return judge_out["native"]
    try:
        from ..analysis import native_scoring
        if native_scoring.resolve_family(pack.get("pack_id", ""), case) == "harmbench":
            return native_scoring.score_harmbench(case, response, trace, pack,
                                                  judge_suggest=judge_suggest)
    except Exception:
        pass
    return None


def _error_result(case, exc: Exception, wall: float, error_kind: str = "target"):
    """Synthetic EvalResult for a case whose invoke/judge blew up.

    One broken case (agent crash, timeout, gateway error) must not hang or kill the whole
    run — it lands as a single FAIL and the run continues (the contract documented in
    docs/agent-integration FAQ). error_kind distinguishes "the target was never reached"
    (connection refused etc.) from "the agent answered but judging crashed" — both mean
    NOTHING was measured about the agent, so the six-dim scorer reports n/a (never fake
    100/30), and a run where every case errored is marked failed, not scored."""
    from agent_contracts import AgentVersion, Attribution, Cost, EvalResult, Scores
    er = EvalResult(
        task_id=case.case_id,
        as_of=case.as_of or datetime.date.today().isoformat(),
        eval_set_version=case.eval_set_version or ((case.suite or "misc") + "-v0.1"),
        agent_version=AgentVersion(profile_version="unknown", component_snapshot=None),
        scores=Scores(deterministic_pass=False, rule_pass=True, human_review=None),
        cost=Cost(tokens=0, wall_time_s=round(wall, 3), external_calls=0),
        evidence=[], trace_linked=False,
        ASI="target invoke error: %s" % str(exc)[:300],
        attribution=Attribution(category="target_error", target_layer="none", confidence=1.0),
        evaluator_results=[])
    judge_out = {"verdict": "FAIL", "type": case.effective_type(), "checks": [],
                 "checkpoints": [], "partial_credit": None, "quality": {},
                 "process": {"skip_answer": False, "loops": [], "broken_chain": 0,
                             "denied": [], "model_errors": 0, "n_tool_calls": 0,
                             "n_model_calls": 0},
                 "safety": {"score": 100.0, "level": "ok", "items": []},
                 "error": str(exc)[:200]}
    judge_out["target_error" if error_kind == "target" else "judge_error"] = True
    return er, judge_out


def run_case_set(cases_dir, target, out_dir, receiver_port: int = 4318,
                 case_ids=None, limit: int = None, levels=None,
                 cases_file: str = None, cases_list=None, receiver=None,
                 on_case_done=None, cancel_check=None, search_roots=None,
                 proxy_sink: str = None, data_root: Path = None,
                 repeat_k: int = 1, ai_cfg: Dict = None,
                 trace_wait_s: float = 15.0) -> Dict:
    """Run a set of cases: invoke -> judge -> gate -> six-dimension scores -> bilingual reports.

    Case source (first match wins): cases_list (pre-resolved Case objects — the web worker
    passes these after applying overrides/level filters and packing) > cases_file
    (json/jsonl/db, trial before import) > cases_dir (SQLite bank / directory / suite name).
    Optional hooks: receiver (resident OTLP receiver), on_case_done (per-case progress),
    cancel_check (between cases), search_roots (context_file roots), proxy_sink (llm-proxy
    JSONL path), data_root (user pack lookup), repeat_k (stability repeats, default 1),
    ai_cfg (per-user LLM config {base_url, api_key, model, auto_adopt} — None = AI off),
    trace_wait_s (per-case wait for asynchronously exported spans — the worker derives it
    from the agent's /capabilities "traces" flag).
    """
    if cases_list is not None:
        cases = list(cases_list)
    elif cases_file:
        cases = load_cases(cases_file)
    else:
        from pathlib import Path as _P
        p = _P(cases_dir)
        if p.suffix == ".db" or p.exists():
            cases = load_cases(p, status="active")          # case bank / directory / file
        else:
            # suite name: scan the cases/ tree (each bank dir carries its own cases.jsonl)
            root = _P("cases")
            pool: list = []
            for f in sorted(root.rglob("*.jsonl")) + sorted(root.rglob("*.json")):
                try:
                    pool.extend(load_cases(f))
                except Exception:
                    continue
            cases = [c for c in pool if c.suite == cases_dir]
    if case_ids:
        want = set(x.strip() for x in case_ids if x.strip())
        cases = [c for c in cases if c.case_id in want]
    if levels:
        want = set(x.strip().upper() for x in levels if x.strip())
        cases = [c for c in cases if c.level.upper() in want]
    if limit:
        cases = cases[:limit]
    n_ctx = _expand_context(cases, search_roots or [Path("cases"), Path(".")])
    if n_ctx:
        print("context expanded: %d cases" % n_ctx)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    from ..run.targets.base import HttpTarget
    owns_receiver = False
    if receiver is None and isinstance(target, HttpTarget):
        receiver = OTLPHTTPReceiver(receiver_port)
        receiver.start()
        owns_receiver = True

    engine = RunEngine(target, receiver, trace_wait_s=trace_wait_s)
    results, runs_meta, raw_spans, model_calls_all, analysis_rows, judge_rows = \
        [], [], [], [], [], []
    answers_rows = []                                # full answers (AI gold-fix on dispute)
    case_rows = []                                   # six-dimension scorer input
    import time as _time
    import os as _os
    proxy_sink = proxy_sink or _os.environ.get("AGENTGATE_PROXY_SINK",
                                               "results/llm_calls/current.jsonl")
    repeat_k = max(1, int(repeat_k or 1))
    cancelled = False
    try:
        for case in cases:
            if cancel_check is not None and cancel_check():
                cancelled = True          # stop here; partial results still get reports below
                break
            pack = pack_reg.resolve_pack(case, data_root=data_root)
            t0 = _time.time()
            analysis: Dict = {}
            reps = []                    # per-repeat: (CaseRun|None, wall, mcalls)
            try:
                for rep in range(repeat_k):
                    r = engine.run_case(case)
                    wall = r.wall_time_s
                    mcalls = _read_proxy_records(proxy_sink, t0, t0 + (_time.time() - t0))
                    r.model_calls = mcalls
                    reps.append((r, wall, mcalls))
                    if rep + 1 < repeat_k and cancel_check is not None and cancel_check():
                        break
            except Exception as e:        # isolate the broken case; the run continues
                wall = _time.time() - t0
                er, judge_out = _error_result(case, e, wall, error_kind="target")
                reps = [(None, wall, [])]
                r0 = None
            else:
                r0 = reps[0][0]
                try:
                    from ..analysis.trajectory import analyze_case
                    analysis = analyze_case(case, r0.response, r0.trace, r0.model_calls)
                except Exception as _e:
                    analysis = {"error": str(_e)}
                tool_blob = _tool_blob(r0.model_calls)
                try:
                    er, judge_out = evaluate_case(
                        case, r0.response, r0.trace, reps[0][1], pack=pack,
                        analysis=analysis, raw_spans=r0.raw_spans, tool_blob=tool_blob,
                        assets_roots=search_roots)
                except Exception as e:
                    er, judge_out = _error_result(case, e, reps[0][1], error_kind="judge")
                    analysis = {}

            # stability (repeat_k > 1): RESULT-CONSISTENCY reading — the same question is
            # run k times and the stability score is how many repeats reproduce run 1's
            # exact outcome (verdict + FINAL json + answer tail). Orthogonal to success:
            # consistently-wrong is stable-but-failing, which the hexagon separates.
            stability = None      # 0-100
            if repeat_k > 1 and r0 is not None:
                verdicts = [judge_out.get("verdict")]
                outcomes = [_outcome_key(r0.response, verdicts[0])]
                for ri, _w, _mc in reps[1:]:
                    if ri is None:
                        verdicts.append("ERROR")
                        outcomes.append(("ERROR", "", ""))
                        continue
                    try:
                        _a = analyze_case(case, ri.response, ri.trace, ri.model_calls)
                    except Exception:
                        _a = {}
                    try:
                        _er, _jo = evaluate_case(
                            case, ri.response, ri.trace, _w, pack=pack, analysis=_a,
                            raw_spans=ri.raw_spans, tool_blob=_tool_blob(ri.model_calls),
                            assets_roots=search_roots)
                        verdicts.append(_jo.get("verdict"))
                    except Exception:
                        verdicts.append("ERROR")
                    outcomes.append(_outcome_key(ri.response, verdicts[-1]))
                match = sum(1 for o in outcomes[1:] if o == outcomes[0])
                stability = round(100.0 * match / max(len(outcomes) - 1, 1), 1)
                detail = ", ".join("run%d %s" % (i + 1, v) for i, v in enumerate(verdicts))
                same = "outputs identical" if match == len(outcomes) - 1 else "outputs differ"
                judge_out["stability_detail"] = "%s；%s" % (detail, same)
                er.ASI = (er.ASI + "\n[stability] repeat_k=%d: %s；%s → 稳定 %.0f" %
                          (repeat_k, detail, same, stability)).strip()
            er.run_id = out.name
            if r0 is not None:
                mcalls_all = [m for (_, _, ms) in reps for m in ms]
                if mcalls_all:
                    r0.model_calls = mcalls_all
                    model_calls_all.append({"task_id": case.case_id, "calls": mcalls_all})
            d = er.model_dump()
            d["task_id"] = er.task_id
            verdict = "FAIL" if not (er.scores.deterministic_pass and er.scores.rule_pass) else \
                ("PENDING" if er.scores.human_review == "pending" else "PASS")

            # ---- layer-2 soft judge (suggestion only; doc 35 §5.1) ----
            suggestion = None
            if ai_cfg and verdict == "PENDING" and judge_out.get("verdict") == "PENDING" \
                    and r0 is not None:
                try:
                    from agent_contracts import Evidence
                    from ..analysis import ai_client
                    sug = ai_client.judge_suggestion(ai_cfg, case, pack, r0.response, judge_out)
                    if sug:
                        adopted = bool(ai_cfg.get("auto_adopt")) and \
                            sug.get("confidence") == "high" and \
                            sug["verdict_suggest"] in ("PASS", "FAIL")
                        sug.update({"case_id": case.case_id, "adopted": adopted})
                        judge_rows.append(sug)
                        suggestion = sug
                        for row in case_rows:
                            if row["case_id"] == case.case_id:
                                row["judge_suggest"] = sug
                        er.scores.judge_score = sug.get("score")
                        er.evidence.append(Evidence(
                            claim="judge_suggestion", source_id=str(sug.get("model", "")),
                            location=json.dumps(
                                {k: sug.get(k) for k in ("verdict_suggest", "score",
                                                         "confidence", "rationale", "adopted")},
                                ensure_ascii=False)[:600]))
                        if adopted:
                            er.scores.deterministic_pass = sug["verdict_suggest"] == "PASS"
                            er.scores.human_review = None
                            verdict = "PASS" if adopted and sug["verdict_suggest"] == "PASS" else "FAIL"
                except Exception:
                    pass

            tokens = er.cost.tokens or sum(
                ((m.get("response") or {}).get("usage") or {}).get("total_tokens", 0) or 0
                for m in (r0.model_calls if r0 is not None else [])) \
                or int((r0.response.get("usage_total") if r0 is not None else 0) or 0)
            if on_case_done is not None:
                on_case_done(case_id=case.case_id, bank=case.suite or "", level=case.level,
                             verdict=verdict, tokens=tokens, wall_time_s=er.cost.wall_time_s,
                             asi=(d.get("ASI") or ""))
            results.append(d)
            if r0 is None:                 # error path: nothing to analyze or archive
                raw_spans.append({"task_id": case.case_id, "trace_id": "", "spans": []})
                runs_meta.append({
                    "task_id": case.case_id, "level": case.level, "suite": case.suite or "",
                    "trace_id": "", "model": "error", "answer_preview": "target invoke error",
                    "tool_calls": [], "denied_tools": [], "spans": 0})
                case_rows.append({"case_id": case.case_id, "verdict": "FAIL", "judge_out": judge_out,
                                  "analysis": {}, "tokens": 0, "wall_s": er.cost.wall_time_s,
                                  "asi": er.ASI, "stability": None, "suite": case.suite or "",
                                  "native": None})
                continue
            # message-level trajectory analysis (four questions); rendered per language
            if not analysis:
                try:
                    from ..analysis.trajectory import analyze_case as _az
                    analysis = _az(case, r0.response, r0.trace, r0.model_calls)
                except Exception as _e:   # analysis failure never breaks the judging main path
                    analysis = {"error": str(_e)}
            if analysis.get("n_model_calls") or analysis.get("n_tool_calls"):
                analysis_rows.append({"case_id": case.case_id, "analysis": analysis})
            raw_spans.append({"task_id": case.case_id, "trace_id": r0.response.get("trace_id", ""),
                               "spans": getattr(r0, "raw_spans", [])})
            runs_meta.append({
                "task_id": case.case_id, "level": case.level,
                "suite": case.suite or "", "trace_id": r0.response.get("trace_id", ""),
                "model": r0.trace.model, "answer_preview": r0.response.get("answer_text", "")[:200],
                "tool_calls": r0.trace.tool_calls, "denied_tools": r0.trace.denied_tools,
                "spans": len(r0.trace.steps),
            })
            answers_rows.append({"case_id": case.case_id,
                                 "answer_text": r0.response.get("answer_text", ""),
                                 "final_json": r0.response.get("final_json") or {}})
            case_rows.append({"case_id": case.case_id, "verdict": verdict,
                              "judge_out": judge_out, "analysis": analysis,
                              "tokens": tokens, "wall_s": er.cost.wall_time_s / max(len(reps), 1),
                              "asi": er.ASI, "stability": stability,
                              "query": str(case.input.get("query", ""))[:200],
                              "origin": case.source.origin,
                              "suite": case.suite or "",
                              "native": _native_for(case, r0.response if r0 else {}, r0.trace if r0 else None,
                                                    pack, search_roots, suggestion,
                                                    judge_out=judge_out)})
    finally:
        if receiver is not None and owns_receiver:
            receiver.stop()

    gate = evaluate_gate(results)
    byl = {}
    for m2 in runs_meta:
        byl.setdefault(m2.get("level", "-"), []).append(m2["task_id"])
    bys = {}
    for m2 in runs_meta:
        bys.setdefault(m2.get("suite", "-"), []).append(m2["task_id"])
    suite_rows = []
    for sv, tids in sorted(bys.items()):
        tset = set(tids)
        res_sv = [r for r in results if r["task_id"] in tset]
        f_cnt = sum(1 for r in res_sv if r["task_id"] in gate["failures"])
        p_cnt = sum(1 for r in res_sv if r["scores"].get("human_review") == "pending"
                    and r["task_id"] not in gate["failures"])
        suite_rows.append({"suite": sv, "total": len(res_sv), "fail": f_cnt,
                           "pending": p_cnt, "pass": len(res_sv) - f_cnt - p_cnt})
    level_rows = []
    for lv, tids in sorted(byl.items()):
        tset = set(tids)
        res_lv = [r for r in results if r["task_id"] in tset]
        f_cnt = sum(1 for r in res_lv if r["task_id"] in gate["failures"])
        p_cnt = sum(1 for r in res_lv if r["scores"].get("human_review") == "pending"
                    and r["task_id"] not in gate["failures"])
        level_rows.append({"level": lv, "total": len(res_lv), "fail": f_cnt,
                           "pending": p_cnt, "pass": len(res_lv) - f_cnt - p_cnt})
    (out / "spans_raw.json").write_text(
        json.dumps(raw_spans, ensure_ascii=False), encoding="utf-8")
    if model_calls_all:
        (out / "llm_calls_raw.json").write_text(
            json.dumps(model_calls_all, ensure_ascii=False), encoding="utf-8")
    if judge_rows:
        with (out / "judge.jsonl").open("w", encoding="utf-8") as fh:
            for j in judge_rows:
                fh.write(json.dumps(j, ensure_ascii=False) + "\n")
    if answers_rows:
        with (out / "answers.jsonl").open("w", encoding="utf-8") as fh:
            for a in answers_rows:
                fh.write(json.dumps(a, ensure_ascii=False) + "\n")

    # ---- six-dimension scores (doc 35 §2) + gate + meta ----
    from ..analysis.scores import score_run, render_section
    scores = score_run(case_rows)
    (out / "scores.json").write_text(
        json.dumps(scores, ensure_ascii=False, indent=1), encoding="utf-8")
    n_target_err = sum(1 for row in case_rows
                       if (row.get("judge_out") or {}).get("target_error"))
    meta = {"time": datetime.datetime.now().isoformat(timespec="seconds"),
            "target_errors": n_target_err,
            "level_rows": level_rows, "suite_rows": suite_rows,
            "analysis_rows": analysis_rows,
            "target": getattr(target, "base_url", target.name),
            "model": (runs_meta[0].get("model") if runs_meta else "-"),
            "eval_set_version": (results[0]["eval_set_version"] if results else "example-v0.1"),
            "repeat_k": repeat_k, "scores": scores,
            "ai_used": bool(ai_cfg) or bool(judge_rows)}
    paths = export_json(str(out), results, runs_meta, gate)
    if analysis_rows:
        # message-level trajectory analysis as archival artifacts (the run-detail tab serves them)
        from ..analysis.trajectory import render_section
        texts = {"zh": ["# 轨迹分析", ""], "en": ["# Trajectory Analysis", ""]}
        for row in analysis_rows:
            for lang in ("zh", "en"):
                texts[lang].extend(render_section(row["case_id"], row["analysis"], lang=lang))
                texts[lang].append("")
        (out / "analysis.md").write_text(chr(10).join(texts["zh"]), encoding="utf-8")
        (out / "analysis-en.md").write_text(chr(10).join(texts["en"]), encoding="utf-8")

    # ---- optional AI report summary (doc 34; per-user config, off by default) ----
    for lang in ("zh", "en"):
        report_text = build_report(results, gate, meta, lang=lang)
        (out / ("report.md" if lang == "zh" else "report-en.md")).write_text(
            report_text, encoding="utf-8")
    if ai_cfg:
        try:
            from ..analysis import ai_client
            failed = [{"case_id": row["case_id"], "asi": row.get("asi", ""),
                       "query": row.get("query", "")}
                      for row in case_rows if row.get("verdict") == "FAIL"]
            summary = ai_client.summarize_report(ai_cfg, (out / "report.md").read_text(encoding="utf-8"),
                                                 failed, lang="zh")
            summary_en = ai_client.summarize_report(ai_cfg, (out / "report-en.md").read_text(encoding="utf-8"),
                                                    failed, lang="en")
            if summary or summary_en:
                meta["ai_summary"] = summary or summary_en
                meta["ai_model"] = ai_cfg.get("model", "LLM")
                notes = ai_client.attribute_errors(ai_cfg, failed, lang="zh") or []
                notes_en = ai_client.attribute_errors(ai_cfg, failed, lang="en") or []
                if notes:
                    meta["ai_diagnostics"] = notes
                for lang, suffix in (("zh", ""), ("en", "-en")):
                    p = out / ("report%s.md" % suffix)
                    body = p.read_text(encoding="utf-8")
                    if lang == "zh":
                        if not summary:
                            continue
                        tail = ("\n\n## AI 摘要与改进建议（%s 起草，供参考）\n\n%s\n\n"
                                % (ai_cfg.get("model", "LLM"), summary))
                        if notes:
                            tail += ("### 失败题根因（AI 起草）\n\n" + "\n".join(
                                "- **%s**：%s → %s" % (n.get("case_id"), n.get("root_cause", ""),
                                                       n.get("fix", "")) for n in notes) + "\n")
                    else:
                        if not summary_en:
                            continue
                        tail = ("\n\n## AI Summary (drafted by %s, for reference)\n\n%s\n\n"
                                % (ai_cfg.get("model", "LLM"), summary_en))
                        if notes_en:
                            tail += ("### Failure root causes (AI-drafted)\n\n" + "\n".join(
                                "- **%s**: %s → %s" % (n.get("case_id"), n.get("root_cause", ""),
                                                       n.get("fix", "")) for n in notes_en) + "\n")
                    p.write_text(body + tail, encoding="utf-8")
        except Exception as _e:
            # surface the failure on the run instead of only the server console —
            # silent AI-assist breakdowns read as "feature missing" in the UI
            meta["ai_assist_error"] = str(_e)[:300]
            print("ai summary skipped: %s" % _e)
    return {"gate": gate, "results": results, "meta": meta, "cancelled": cancelled,
            "scores": scores,
            "paths": {**paths, "report": str(out / "report.md"),
                      "report_en": str(out / "report-en.md"),
                      "scores": str(out / "scores.json")}}
