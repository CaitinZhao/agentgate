"""Run execution view: aggregate one finished run's artifacts into page-ready data.

Why this exists (borrowed from the report/analysis redesign prototype): the platform
archived everything needed to answer "where did the time go, what did it cost, what went
wrong" but only ever rendered per-case prose. Answers that require AGGREGATION across
cases were therefore unavailable anywhere — the prototype showed them (LLM 98.8% of wall
time, loop cases averaging 3.8x the tokens of clean ones, the gap between the two
readings) and they are the numbers that actually direct an optimization.

Sources are the artifacts already on disk — scores.json / runs_meta.json / gate.json /
spans_raw.json / llm_calls_raw.json. Everything degrades to None rather than guessing
(the same honesty rule as the rest of the platform): a metric whose evidence is absent
reads as "not measured", never as zero.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional

_DIM_ORDER = ["success", "quality", "reliability", "stability", "efficiency", "cost", "safety"]

_VERDICT_KEY = {"PASS": "pass", "FAIL": "fail", "PENDING": "pending", "SKIPPED": "skipped"}


def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _span_kind(span: Dict) -> str:
    name = str(span.get("name", ""))
    attrs = span.get("attributes", {}) or {}
    if name.startswith("llm.") or attrs.get("llm.model") or attrs.get("gen_ai.request.model"):
        return "llm"
    if name.startswith("tool.") or attrs.get("tool.name"):
        return "tool"
    return "step"


def _tool_name(span: Dict) -> str:
    attrs = span.get("attributes", {}) or {}
    for key in ("tool.name", "gen_ai.tool.name", "name"):
        if attrs.get(key):
            return str(attrs[key])
    name = str(span.get("name", ""))
    return name.split(".")[-1] if "." in name else name


def _case_execution(case_id: str, spans: List[Dict]) -> Dict:
    """Where one case's wall time went, plus its call counts and loop groups."""
    ordered = sorted(spans or [], key=lambda s: s.get("start_unix_nano", 0))
    if not ordered:
        return {"case_id": case_id, "wall_s": None, "llm_s": None, "tool_s": None,
                "wait_s": None, "llm_calls": 0, "tool_calls": 0, "tool_sequence": [],
                "loop_groups": [], "longest_tool_s": None, "lane": []}
    base = ordered[0].get("start_unix_nano", 0) or 0
    llm_ms = tool_ms = longest_tool_ms = 0.0
    n_llm = n_tool = 0
    tool_seq: List[str] = []
    lane: List[List] = []
    for s in ordered:
        kind = _span_kind(s)
        start = ((s.get("start_unix_nano", base) or base) - base) / 1e6
        end = s.get("end_unix_nano", s.get("start_unix_nano", base))
        dur = max((end or base) - (s.get("start_unix_nano", base) or base), 0) / 1e6
        attrs = s.get("attributes", {}) or {}
        tokens = int(attrs.get("llm.usage.total_tokens", 0) or 0)
        lane.append([kind, _tool_name(s) if kind == "tool" else str(s.get("name", "")),
                     round(start, 1), round(dur, 1), tokens])
        if kind == "llm":
            llm_ms += dur
            n_llm += 1
        elif kind == "tool":
            tool_ms += dur
            n_tool += 1
            longest_tool_ms = max(longest_tool_ms, dur)
            tool_seq.append(_tool_name(s))
    wall_ms = max((s.get("end_unix_nano", base) or base) for s in ordered) / 1e6 - base / 1e6
    wait_ms = max(wall_ms - llm_ms - tool_ms, 0.0)
    return {"case_id": case_id,
            "wall_s": round(wall_ms / 1000, 1),
            "llm_s": round(llm_ms / 1000, 1),
            "tool_s": round(tool_ms / 1000, 1),
            "wait_s": round(wait_ms / 1000, 1),
            "llm_calls": n_llm, "tool_calls": n_tool,
            "longest_tool_s": round(longest_tool_ms / 1000, 1),
            "tool_sequence": tool_seq, "loop_groups": _loop_groups(tool_seq),
            "lane": lane}


def _loop_groups(seq: List[str]) -> List[Dict]:
    """Consecutive runs of >=3 identical tool calls (the same criterion the judge uses)."""
    groups, i = [], 0
    while i < len(seq):
        j = i
        while j + 1 < len(seq) and seq[j + 1] == seq[i]:
            j += 1
        if j - i + 1 >= 3:
            groups.append({"tool": seq[i], "count": j - i + 1, "start": i})
        i = j + 1
    return groups


def build(run_dir, scores: Optional[Dict] = None) -> Dict:
    """Aggregate one archived run into the execution view.

    `scores` may be passed in by the caller (the web layer already has it) to avoid a
    second read; everything else comes from the run directory.
    """
    rd = Path(run_dir)
    sc = scores if scores is not None else _json(rd / "scores.json", {})
    meta = _json(rd / "runs_meta.json", [])
    if isinstance(meta, dict):
        meta = meta.get("runs") or []
    spans_raw = _json(rd / "spans_raw.json", [])
    llm_raw = _json(rd / "llm_calls_raw.json", [])
    per_case = sc.get("per_case") or []
    cards = sc.get("cards") or {}
    counts = sc.get("counts") or {}

    tokens_by_case = {m.get("task_id"): (m.get("usage_tokens") or 0) for m in meta}
    spans_by_case = {d.get("task_id"): (d.get("spans") or []) for d in spans_raw}
    lm_by_case = {d.get("task_id"): (d.get("calls") or []) for d in llm_raw}

    # ---------- per-case execution (time split / loops / lanes) ----------
    exec_rows = []
    for row in per_case:
        cid = row.get("case_id")
        ex = _case_execution(cid, spans_by_case.get(cid) or [])
        prov = "otel"
        if not spans_by_case.get(cid):
            # the response-audit fallback: counts only, no timing (say so, do not fake it)
            calls = lm_by_case.get(cid) or []
            ex["tool_calls"] = ex["tool_calls"] or len(
                [c for c in calls if isinstance(c, dict)])
            prov = "audit" if calls else "none"
        ex["tokens"] = row.get("tokens") or tokens_by_case.get(cid) or 0
        ex["verdict"] = row.get("verdict")
        ex["timing_source"] = prov
        exec_rows.append(ex)

    timed = [r for r in exec_rows if r.get("timing_source") == "otel"]
    llm_total = round(sum(r["llm_s"] for r in timed), 1) if timed else None
    tool_total = round(sum(r["tool_s"] for r in timed), 1) if timed else None
    wait_total = round(sum(r["wait_s"] for r in timed), 1) if timed else None
    denom = (llm_total or 0) + (tool_total or 0) + (wait_total or 0)

    # ---------- loop cost (the number that directs optimization) ----------
    loop_rows = [r for r in exec_rows if r.get("loop_groups")]
    clean_rows = [r for r in exec_rows if not r.get("loop_groups")]
    loop_tok = ([r["tokens"] for r in loop_rows if r.get("tokens")] or None)
    clean_tok = ([r["tokens"] for r in clean_rows if r.get("tokens")] or None)

    # ---------- score aggregates ----------
    run_scores = sc.get("run_scores") or {}
    score_dims = []
    for d in _DIM_ORDER:
        v = run_scores.get(d)
        item = {"key": d, "score": v}
        if v is None:
            if d == "stability":
                item["reason"] = "stability_k=1"
            elif d == "cost":
                item["reason"] = "no usage recorded"
            else:
                item["reason"] = "no judged case"
        score_dims.append(item)

    # how many signals each dimension carries. Prefer the scorer's full totals: the card
    # list is truncated for display (first 8), so counting it under-reports.
    signal_count = dict(sc.get("signal_totals") or {})
    if not signal_count:
        for d in _DIM_ORDER:
            signal_count[d] = len((cards.get(d) or {}).get("signals") or [])
    top_signal = {}
    for d, card in cards.items():
        sigs = card.get("signals") or []
        if sigs:
            top_signal[d] = sigs[0].get("note") or ""

    # ---------- two readings (platform hexagon vs dataset-native) ----------
    judged = counts.get("judged")
    if judged is None:
        judged = sum(1 for c in per_case if c.get("verdict") in ("PASS", "FAIL"))
    native = sc.get("native") or {}
    native_rows = []
    for suite, v in native.items():
        native_rows.append({"suite": suite, **v})
    platform_rate = (round(100.0 * counts.get("pass", 0) / judged, 1)
                     if judged else None)
    native_rate = None
    if len(native_rows) == 1 and native_rows[0].get("rate") is not None:
        native_rate = native_rows[0]["rate"]

    gaps = []
    if not llm_raw:
        gaps.append("no_llm_calls")
    if not (rd / "judge.jsonl").is_file():
        gaps.append("no_judge")
    if not native:
        gaps.append("no_native")
    if not spans_raw:
        gaps.append("no_spans")

    return {
        "counts": {**counts, "judged": judged},
        "metrics": {
            "total_score": sc.get("total"),
            "pass_rate": platform_rate,
            "native_rate": native_rate,
            "two_reading_gap": (round(native_rate - platform_rate, 1)
                                if native_rate is not None and platform_rate is not None
                                else None),
            "no_score_note": ("no judgeable case" if sc.get("scored") is False else None),
            "tokens_total": (sc.get("token_summary") or {}).get("total"),
            "llm_s": llm_total, "tool_s": tool_total, "wait_s": wait_total,
            "llm_share": (round(100.0 * llm_total / denom, 1) if denom else None),
            "avg_wall_s": (round(sum(r["wall_s"] for r in timed) / len(timed), 1)
                           if timed else None),
        },
        "score_dims": score_dims,
        "signal_count": signal_count,
        "top_signal": top_signal,
        "loop": {
            "cases": len(loop_rows),
            "groups": sum(len(r["loop_groups"]) for r in loop_rows),
            "longest": max((max(g["count"] for g in r["loop_groups"]) for r in loop_rows),
                           default=None),
            "token_avg_loop": (round(sum(loop_tok) / len(loop_tok)) if loop_tok else None),
            "token_avg_clean": (round(sum(clean_tok) / len(clean_tok)) if clean_tok else None),
        },
        "slow_tools": sorted(
            ({"case_id": r["case_id"], "tool_s": r["longest_tool_s"]}
             for r in exec_rows if (r.get("longest_tool_s") or 0) > 5.0),
            key=lambda x: -x["tool_s"])[:8],
        "cases": exec_rows,
        "native": native_rows,
        "data_gaps": gaps,
        "timings_measured": len(timed),
    }
