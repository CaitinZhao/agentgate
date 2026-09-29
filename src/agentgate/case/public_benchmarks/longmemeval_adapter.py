"""LongMemEval (long-conversation memory benchmark) adapter: the oracle subset -> memory-profile case bank.

Data: reference/refs/data/longmemeval/longmemeval_oracle.json (500 cases; each carries its
evidence sessions haystack_sessions - oracle = the minimal necessary context, p50 ~26k characters)
Artifacts (isomorphic to LoCoMo):
  cases/longmem/contexts/<case_id>.json  per-case session context text
  cases/longmem/cases.jsonl              the full 500-case bank
  cases/longmem/sample-40.jsonl          40-case sample stratified by question type
Judgment bucketing (gold v2): purely numeric answers -> type=numeric, gold.final exact
match (87 cases); all other free text -> type=free_text with the dataset answer as the
rubric scoring point (PENDING until the soft judge / human).
The oracle set has no abstention type; date-like golds (e.g. 2023/04/10) go to PENDING to
avoid misjudgment.
"""
import json
import re
from pathlib import Path

from ..models import Case, CaseSource, DiagnosisHint, Gold, RubricPoint

NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _render_sessions(q: dict) -> str:
    """haystack_sessions -> readable dialogue text (session sections with dates)."""
    dates = q.get("haystack_dates", [])
    out = []
    for si, sess in enumerate(q.get("haystack_sessions", [])):
        ts = dates[si] if si < len(dates) else ""
        out.append("")
        out.append("=== 会话 %d%s ===" % (si + 1, (" | " + ts) if ts else ""))
        for m in sess:
            out.append("[%s] %s" % (m.get("role", "?"), m.get("content", "")))
    return "\n".join(out)


def _bucket(qa: dict) -> tuple:
    """qa -> (case type, Gold)."""
    ans = str(qa.get("answer", "")).strip()
    if NUM_RE.match(ans):
        return "numeric", Gold(final={"value": float(ans), "tol_rel": 0.0, "unit": "none"})
    rubric = [RubricPoint(point="答案与金标一致：%s" % ans[:300], weight=1)] if ans else []
    return "free_text", Gold(final={}, rubric=rubric)


def build(data_path: str, out_dir: str = "cases/longmem",
          sample_size: int = 40) -> dict:
    data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    out = Path(out_dir)
    (out / "contexts").mkdir(parents=True, exist_ok=True)
    today = __import__("datetime").date.today().isoformat()

    cases = []
    for qi, q in enumerate(data):
        cid = "longmem-" + str(q.get("question_id", qi)).replace("_", "-")
        (out / "contexts" / (cid + ".json")).write_text(
            json.dumps({"question_id": q.get("question_id"), "dialogue": _render_sessions(q)},
                       ensure_ascii=False, indent=1),
            encoding="utf-8")
        ctype, gold_v2 = _bucket(q)
        cases.append(Case(
            case_id=cid,
            suite="longmem", level="L2", as_of=today, type=ctype, pack="longmem",
            source=CaseSource(origin="public_benchmark", seed=str(q.get("question_id")),
                              adaptation="记忆剖面：oracle 证据会话注入上下文（LongMemEval 官方数据）",
                              provenance="type=%s | %s" % (q.get("question_type"),
                                                           str(q.get("question", ""))[:100])),
            input={"query": q["question"], "profile": "longmem",
                   "context_file": "longmem/contexts/%s.json" % cid},
            gold=gold_v2,
            diagnosis_hint=DiagnosisHint(
                failure_kind="memory_qa_mismatch", target_layer="domain",
                expected_behavior="仅依据给定会话回答；会话中没有的信息明说未提及，禁止编造")))

    with (out / "cases.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for c in cases:
            f.write(c.model_dump_json() + "\n")

    # stratified sampling by question type
    by_type = {}
    for c, q in zip(cases, data):
        by_type.setdefault(q.get("question_type", "?"), []).append(c.case_id)
    picked = set()
    while len(picked) < min(sample_size, len(cases)):
        for t in sorted(by_type):
            if by_type[t] and len(picked) < sample_size:
                picked.add(by_type[t].pop(0))
    with (out / "sample-40.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for c in cases:
            if c.case_id in picked:
                f.write(c.model_dump_json() + "\n")

    buckets = {"numeric": sum(1 for c in cases if c.type == "numeric"),
               "judge": sum(1 for c in cases if c.type == "free_text")}
    return {"cases": len(cases), "sample": len(picked), "buckets": buckets,
            "out": str(out)}
