"""autoadapt: adapt an external dataset jsonl into an AgentGate case bank (cases.jsonl) + a human-review checklist.

Bucketing rules (deterministic, no LLM):
  gold contains billion/million -> numeric (+-1% auto judgment; unit parsed from the question)
  gold contains %               -> percentage numeric
  gold starts with yes/no       -> yes-no (answer_equals)
  anything else                 -> judge_required (free text, L3/human)

With --llm-hint, an LLM drafts each case's expected_behavior ("what the agent should do")
into diagnosis_hint for human check — the checklist marks which contents are LLM-drafted.
"""
import datetime
import json
from pathlib import Path
from typing import Dict, List


def _read_rows(path: Path) -> List[Dict]:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        import csv
        with p.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


from ..case.gold_rules import quantity_value


def _parse_num(answer: str, question: str):
    """(value, unit) from the dataset answer, or None.

    Quantity-safety rules are shared with the FinanceBench adapter
    (`case/gold_rules.py`): entity/yes-no questions never become numeric, years and dates
    are not quantities, enumerations and multi-value prose without a period to
    disambiguate fall back to free_text (AGENTS.md: judge_required over a forced verdict).
    Coarse precision (1-2 significant digits) is NOT flagged here: the judging layer's
    precision floor already widens the tolerance to the gold's own precision, so a second
    guard would only drop usable cases."""
    a = str(answer or "").strip().lstrip("$").replace(",", "")
    q = str(question or "").lower()
    qa = q + " " + a.lower()
    if "%" in a or "%" in q:
        unit = "%"
    elif "billion" in qa:
        unit = "USD billions"
    elif "million" in qa:
        unit = "USD millions"
    elif "亿元" in q:
        unit = "亿元"
    elif "万元" in q:
        unit = "万元"
    elif "元" in a or "元" in q:
        unit = "元"
    else:
        # no unit word anywhere: a bare number is still numeric (unit-less datasets);
        # the shared rule decides whether the figure is unambiguous
        unit = "none"
    v, _reason = quantity_value(a, question)
    if v is None:
        return None
    return {"unit": unit, "value": v}


def _bucket(answer: str, question: str):
    """(bucket, case type, gold v2) — gold keeps the dataset answer as the source of truth.

    The raw dataset answer rides along as final.reference_answer for every bucket:
    the AI judge and the free_text reference rescue layer both score against it.
    Cases that are NOT an unambiguous quantity fall back to free_text (see gold_rules):
    a forced numeric gold is how correct answers get marked wrong."""
    ref = {"reference_answer": answer.strip()} if answer.strip() else {}
    low = answer.strip().lower()
    if low.startswith("yes"):
        return "yesno", "boolean", {"final": dict({"value": "yes"}, **ref)}
    if low.startswith("no"):
        return "yesno", "boolean", {"final": dict({"value": "no"}, **ref)}
    num = _parse_num(answer, question)
    if num:
        final = dict({"value": num["value"], "unit": num["unit"], "tol_rel": 0.01}, **ref)
        return "numeric", "numeric", {"final": final}
    return "text", "free_text", {"final": dict(ref), "rubric": [{"point": "答案与金标一致：%s" % answer.strip()[:300], "weight": 1}] if answer.strip() else []}


def _llm_hint(meta: Dict[str, Dict]) -> Dict[str, str]:
    """LLM drafts expected_behavior per case (optional, --llm-hint).

    LLM config priority: agentgate.json's llm section > LLM_* env vars.
    Runs fine without config: rule bucketing needs no LLM; --llm-hint only adds
    expected-behavior drafts.
    """
    import os
    import httpx
    from ..config import load_config
    llm = load_config().get("llm") or {}
    base = llm.get("base_url") or os.environ.get("LLM_BASE_URL", "")
    key = llm.get("api_key") or os.environ.get("LLM_API_KEY", "")
    model = llm.get("model") or os.environ.get("LLM_MODEL", "GLM5.3-Flash")
    if not base or not key:
        return {}
    hints: Dict[str, str] = {}
    items = list(meta.items())
    for i in range(0, len(items), 10):
        chunk = items[i:i + 10]
        lines = [
            "%s | 问：%s | 金标：%s" % (cid, r.get("question", "")[:160],
                                        str(r.get("answer", ""))[:120])
            for cid, r in chunk
        ]
        prompt = ("以下是评测题目的编号、问题与金标答案。请为每一题输出一行："
                  "编号 | 期望行为（Agent 要怎么做才算对，50 字以内，中文）。不要输出其他内容。"
                  + chr(10) + chr(10).join(lines))
        try:
            r = httpx.post(base.rstrip("/") + "/chat/completions",
                           headers={"Authorization": "Bearer " + key},
                           json={"model": model, "temperature": 0.2,
                                 "messages": [{"role": "user", "content": prompt}]},
                           timeout=120)
            text = r.json()["choices"][0]["message"]["content"]
        except Exception:
            continue
        for line in text.splitlines():
            if "|" not in line:
                continue
            cid, _, hint = line.partition("|")
            cid = cid.strip().strip("- ").split(" ")[0]
            if cid in meta and hint.strip():
                hints[cid] = hint.strip()[:120]
    return hints


def auto_adapt(source: str, id_field: str, question_field: str, answer_field: str,
               suite: str, out: str, review: str, doc_field: str = "",
               profile: str = "", level: str = "L1",
               llm_hint: bool = False, db: str = "",
               as_of: str = None) -> Dict:
    rows = _read_rows(Path(source))
    today = datetime.date.today().isoformat()
    as_of = as_of or today
    cases: List[Dict] = []
    review_rows: List[Dict] = []
    for r in rows:
        qid = str(r.get(id_field, ""))
        question = str(r.get(question_field, ""))
        answer = str(r.get(answer_field, ""))
        bucket, ctype, gold = _bucket(answer, question)
        cid = qid if qid.startswith(suite.lower() + "-") else ("%s-%s" % (suite.lower(), qid))
        c = {
            "case_id": cid, "version": 1, "level": level, "as_of": as_of,
            "suite": suite,
            "status": "active",
            "source": {"origin": "public_benchmark", "seed": qid,
                       "adaptation": "autoadapt 自动分桶（%s）" % bucket,
                       "provenance": question[:120]},
            "input": {"query": question},
            "type": ctype, "pack": "fb",
            "gold": dict(gold, checkpoints=[{"desc": "检索财报原文（search_filing）",
                                             "signal": "tool", "pattern": "search_filing",
                                             "weight": 1}]),
            "diagnosis_hint": {
                "failure_kind": (bucket + "_mismatch") if bucket != "text" else "needs_judge",
                "target_layer": "none", "expected_behavior": ""},
        }
        if doc_field and r.get(doc_field):
            c["input"]["doc_hint"] = r[doc_field]
        if profile:
            c["input"]["profile"] = profile
        cases.append(c)
        review_rows.append({"id": cid, "bucket": bucket, "gold": answer[:120],
                            "question": question[:160]})
    hints = {}
    if llm_hint:
        hints = _llm_hint({r["id"]: {"question": r["question"], "answer": r["gold"]}
                           for r in review_rows})
    for c in cases:
        if c["case_id"] in hints:
            c["diagnosis_hint"]["expected_behavior"] = hints[c["case_id"]]
            c["diagnosis_hint"]["hint_source"] = "llm-draft"
    from ..case.models import Case
    case_objs = [Case(**c) for c in cases]
    if db:
        from ..case.store import upsert_cases
        upsert_cases(db, case_objs)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with Path(out).open("w", encoding="utf-8", newline=chr(10)) as fh:
        for c in cases:
            fh.write(json.dumps(c, ensure_ascii=False) + chr(10))

    from collections import Counter
    cnt = Counter(r["bucket"] for r in review_rows)
    NL = chr(10)
    md = ["# autoadapt 人工检查清单（%s）" % suite, "",
          "- 题数：%d | 分桶：%s" % (len(cases), dict(cnt)),
          "- 金标由规则自动判定（歧义/枚举/是非/年份一律转自由文本，见 gold_rules）；"
          "无需逐题核对，抽样 10%% 复核即可",
          "- 防污染：本套题目只做观测与对标，不作为 accept 依据", "",
          "## 抽样复核清单", ""]
    for r in review_rows:
        note = {"numeric": "数值/单位解析", "yesno": "是非方向", "text": "确为自由文本"}.get(
            r["bucket"], r["bucket"])
        md.append("- **%s**：分桶 %s（%s）；金标：%s" % (r["id"], r["bucket"], note, r["gold"]))
    review_path = Path(review)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(NL.join(md), encoding="utf-8")
    return {"cases": len(cases), "out": out, "review": review,
            "buckets": dict(cnt), "llm_hints": len(hints)}
