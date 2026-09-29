"""LoCoMo (long-conversation memory benchmark) adapter: conversations -> memory-profile contexts, QA -> case bank.

Data: reference/refs/code/LoCoMo/data/locomo10.json (10 multi-session long conversations, ~2000 QA)
Artifacts:
  cases/locomo/contexts/locomo-s<N>.json   rendered dialogue text per conversation (profile context)
  cases/locomo/cases.jsonl                 the full bank (context_file references the context files)
  cases/locomo/sample-40.jsonl             stratified 40-case sample (4 per conversation, category-balanced;
                                           used for remote smoke runs)
Judgment bucketing (aligned with the official evaluation.py, gold v2):
  category 5 adversarial  -> type=refusal, gold.final.keywords = ["not mentioned",
                             "no information available"] (the official correctness phrases)
  purely numeric answers  -> type=numeric, gold.final {value, tol_rel=0}
  all other free text     -> type=free_text + rubric (dataset answer as the single
                             scoring point; PENDING until the soft judge / human)
"""
import json
import re
from pathlib import Path

from ..models import Case, CaseSource, DiagnosisHint, Gold, RubricPoint

REFUSAL_TOKENS = ["not mentioned", "no information available"]
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _render_dialogue(sess: dict) -> str:
    """conversation dict -> readable dialogue text (session sections with timestamps)."""
    conv = sess["conversation"]
    turns_keys = sorted(
        (k for k in conv if re.fullmatch(r"session_\d+", k)),
        key=lambda k: int(k.split("_")[1]))
    out = ["对话双方：%s 与 %s" % (conv.get("speaker_a", "A"), conv.get("speaker_b", "B"))]
    for k in turns_keys:
        n = k.split("_")[1]
        ts = conv.get("%s_date_time" % k, "")
        out.append("")
        out.append("=== 会话 %s%s ===" % (n, (" | " + ts) if ts else ""))
        for t in conv[k]:
            out.append("[%s] %s" % (t.get("speaker", "?"), t.get("text", "")))
    return "\n".join(out)


def _bucket(qa: dict) -> tuple:
    """qa -> (case type, Gold) per the official bucketing."""
    ans = str(qa.get("answer", qa.get("adversarial_answer", ""))).strip()
    if int(qa.get("category", 0)) == 5:
        return "refusal", Gold(final={"keywords": list(REFUSAL_TOKENS)})
    if NUM_RE.match(ans):
        return "numeric", Gold(final={"value": float(ans), "tol_rel": 0.0, "unit": "none"})
    rubric = [RubricPoint(point="答案与金标一致：%s" % ans[:300], weight=1)] if ans else []
    return "free_text", Gold(final={}, rubric=rubric)


def build(data_path: str, out_dir: str = "cases/locomo",
          sample_per_session: int = 4) -> dict:
    data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    out = Path(out_dir)
    (out / "contexts").mkdir(parents=True, exist_ok=True)
    today = __import__("datetime").date.today().isoformat()

    cases, by_sid = [], []
    for sid, sess in enumerate(data):
        ctx_name = "locomo-s%d.json" % sid
        (out / "contexts" / ctx_name).write_text(
            json.dumps({"sample_id": sess.get("sample_id", sid),
                        "dialogue": _render_dialogue(sess)},
                       ensure_ascii=False, indent=1),
            encoding="utf-8")
        for qi, qa in enumerate(sess["qa"]):
            ev_raw = qa.get("evidence")
            if isinstance(ev_raw, str):
                try:
                    ev_raw = json.loads(ev_raw)
                except ValueError:
                    ev_raw = [ev_raw]
            ev = ", ".join(str(x) for x in (ev_raw or []))
            ctype, gold_v2 = _bucket(qa)
            cases.append(Case(
                case_id="locomo-s%d-q%04d" % (sid, qi),
                suite="locomo", level="L2", as_of=today,
                type=ctype, pack="locomo",
                source=CaseSource(origin="public_benchmark",
                                  seed="evidence:%s" % ev if ev else None,
                                  adaptation="记忆剖面：整段对话注入上下文（LoCoMo 官方数据）",
                                  provenance=str(qa.get("question", ""))[:120]),
                input={"query": qa["question"], "profile": "locomo",
                       "context_file": "locomo/contexts/%s" % ctx_name},
                gold=gold_v2,
                diagnosis_hint=DiagnosisHint(
                    failure_kind="memory_qa_mismatch", target_layer="domain",
                    expected_behavior="仅依据对话上下文作答；对话中没有的信息明说未提及，禁止编造")))
        by_sid.append(sid)

    with (out / "cases.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for c in cases:
            f.write(c.model_dump_json() + "\n")

    # stratified sampling: N cases per conversation, rotating categories (adversarial/temporal/multi-hop/single-hop balanced)
    sample = []
    for sid, sess in enumerate(data):
        by_cat = {}
        for qi, qa in enumerate(sess["qa"]):
            by_cat.setdefault(int(qa["category"]), []).append(
                "locomo-s%d-q%04d" % (sid, qi))
        picked, cats = set(), sorted(by_cat)
        while len(picked) < sample_per_session:
            for cat in cats:
                if by_cat[cat] and len(picked) < sample_per_session:
                    picked.add(by_cat[cat].pop(0))
        sample.extend(c for c in cases if c.case_id in picked)
    with (out / "sample-40.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for c in sample:
            f.write(c.model_dump_json() + "\n")

    buckets = {"adversarial": 0, "numeric": 0, "judge": 0}
    for c in cases:
        if c.type == "refusal":
            buckets["adversarial"] += 1
        elif c.type == "numeric":
            buckets["numeric"] += 1
        else:
            buckets["judge"] += 1
    return {"sessions": len(data), "cases": len(cases), "sample": len(sample),
            "buckets": buckets, "out": str(out)}
