"""Public-benchmark adapter — BFCL (Berkeley Function Calling Leaderboard, v4).

Source: github.com/ShishirPatil/gorilla (Apache-2.0), data under
berkeley-function-call-leaderboard/bfcl_eval/data/. Categories integrated
(single-turn only; multi-turn stateful categories are a P2 roadmap item):
  live_simple / live_multiple / live_parallel / live_parallel_multiple /
  live_irrelevance / live_relevance / simple_python(+java/js) /
  multiple / parallel / parallel_multiple / irrelevance

Case mapping (gold v2): type=free_text, pack=bfcl; the FINAL answer contract is ONE
function call {"name","arguments"} or {"calls":[...]} for parallel tasks. The dataset's
own AST-matching semantics (alternative values, "" = optional, parallel order rules)
live in analysis/native_scoring.score_bfcl, which doubles as the platform typed check.

Sampling: full BFCL v4 single-turn is ~3.6k entries; the bank ships a seeded stratified
sample per category (default caps, --full for everything). Sampling keeps a full-run of
the bank affordable while preserving the category mix; the seed and caps are recorded in
the bank README for reproducibility.

Contamination guard: observation-only, never an accept gate.
"""
import json
import random
from pathlib import Path
from typing import Dict, List, Optional

# category -> (data file stem, mode, sample cap)
CATEGORIES = {
    "live_simple": ("BFCL_v4_live_simple", "single", 60),
    "live_multiple": ("BFCL_v4_live_multiple", "multiple", 80),
    "live_parallel": ("BFCL_v4_live_parallel", "parallel", 15),
    "live_parallel_multiple": ("BFCL_v4_live_parallel_multiple", "parallel_any", 23),
    "live_irrelevance": ("BFCL_v4_live_irrelevance", "irrelevance", 40),
    "live_relevance": ("BFCL_v4_live_relevance", "irrelevance", 16),
    "simple_python": ("BFCL_v4_simple_python", "single", 60),
    "multiple": ("BFCL_v4_multiple", "multiple", 60),
    "parallel": ("BFCL_v4_parallel", "parallel", 60),
    "parallel_multiple": ("BFCL_v4_parallel_multiple", "parallel_any", 60),
    "irrelevance": ("BFCL_v4_irrelevance", "irrelevance", 30),
    "simple_java": ("BFCL_v4_simple_java", "single", 15),
    "simple_javascript": ("BFCL_v4_simple_javascript", "single", 10),
}

NL = chr(10)
DEFAULT_DATA_DIR = (Path(__file__).resolve().parents[5] / "reference" / "refs" / "code"
                    / "gorilla" / "berkeley-function-call-leaderboard" / "bfcl_eval" / "data")


def _read_jsonl(path: Path) -> List[Dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _to_functions(func_docs: List) -> List[Dict]:
    """BFCL function docs arrive as JSON strings (or dicts); normalize to dicts."""
    out = []
    for f in func_docs or []:
        if isinstance(f, str):
            try:
                f = json.loads(f)
            except ValueError:
                continue
        if isinstance(f, dict):
            out.append(f)
    return out


def _gold_for(entry: Dict, mode: str) -> Dict:
    """Dataset ground truth -> gold.final (native scorer consumes the original format)."""
    if mode == "irrelevance":
        return {"irrelevance": True}
    gt = entry.get("ground_truth") or []
    if isinstance(gt, dict):
        gt = [gt]
    possible = []
    for call in gt:                     # {func_name: {arg: [alts]}}
        for fname, args in call.items():
            possible.append({"function": fname, "arguments": args or {}})
    return {"mode": mode, "possible": possible}


def _to_case(entry: Dict, mode: str) -> Optional[Dict]:
    eid = str(entry.get("id", "")).strip()
    questions = entry.get("question") or []
    try:
        query = questions[0][0]["content"]
    except (IndexError, KeyError, TypeError):
        return None
    funcs = _to_functions(entry.get("function"))
    if mode != "irrelevance" and not funcs:
        return None
    ctx = json.dumps(funcs, ensure_ascii=False, separators=(",", ":"))
    kind = ("irrelevance_no_call" if mode == "irrelevance"
            else "tool_call_mismatch" if mode in ("single", "multiple")
            else "tool_call_sequence")
    return {
        "case_id": "bfcl-" + eid, "version": 1, "level": "L1", "as_of": "2026-09-24",
        "suite": "bfcl",
        "source": {"origin": "public_benchmark", "seed": eid,
                   "adaptation": "题面与函数文档原样接入；函数文档经 input.context 传给被测 agent；"
                                 "判定沿用 BFCL 原生 AST 等价匹配（含可选参数/并行顺序语义）",
                   "provenance": str(query)[:120]},
        "input": {"query": query, "profile": "bfcl", "context": ctx},
        "type": "free_text", "pack": "bfcl",
        "gold": {"final": _gold_for(entry, mode), "checkpoints": [], "rubric": []},
        "diagnosis_hint": {"failure_kind": kind, "target_layer": "none",
                           "expected_behavior": "只依据给定函数文档输出正确的函数调用（不实际执行）"},
    }


def build(data_dir: Optional[Path] = None, out_dir: str = "cases/bfcl",
          full: bool = False, seed: int = 42) -> Dict:
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    rng = random.Random(seed)
    cases: List[Dict] = []
    per_cat = {}
    for cat, (stem, mode, cap) in CATEGORIES.items():
        path = data_dir / ("%s.json" % stem)
        if not path.is_file():
            per_cat[cat] = "missing"
            continue
        rows = _read_jsonl(path)
        # ground truth lives in the separate possible_answer file, joined by id
        gt_path = data_dir / "possible_answer" / ("%s.json" % stem)
        gt = {}
        if gt_path.is_file():
            gt = {r.get("id"): r.get("ground_truth") for r in _read_jsonl(gt_path)}
        if not full and len(rows) > cap:
            rows = rng.sample(rows, cap)
        made = []
        for e in rows:
            e = dict(e)
            if e.get("id") in gt:
                e["ground_truth"] = gt[e["id"]]
            c = _to_case(e, mode)
            if c:
                made.append(c)
        cases.extend(made)
        per_cat[cat] = len(made)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "cases.jsonl").open("w", encoding="utf-8", newline=NL) as fh:
        for c in cases:
            fh.write(json.dumps(c, ensure_ascii=False) + NL)
    total_possible = sum(v for v in per_cat.values() if isinstance(v, int))
    (out / "README.md").write_text(
        "# BFCL 题库（%d 题，抽样 seed=%d，全量单轮 %d）%s%s"
        % (len(cases), seed, total_possible, NL, NL)
        + NL.join("- %s: %s" % (k, v) for k, v in per_cat.items())
        + NL + NL + "来源：gorilla/BFCL v4（Apache-2.0）。判定沿用原生 AST 等价匹配；"
        "多轮状态类（multi_turn_*）与执行类类别未纳入（P2 路线图）。" + NL,
        encoding="utf-8")
    return {"cases": len(cases), "out": str(out), "per_category": per_cat}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="BFCL 题库生成")
    ap.add_argument("--full", action="store_true", help="全量（不抽样）")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--out", default="cases/bfcl")
    a = ap.parse_args()
    print(json.dumps(build(a.data_dir, a.out, full=a.full), ensure_ascii=False))
