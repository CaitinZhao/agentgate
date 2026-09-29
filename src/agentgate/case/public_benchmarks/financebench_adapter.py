"""Public-benchmark adaptation adapter (source A) - FinanceBench.

P0 approach: take FinanceBench's question shapes as "seeds" mapped onto this repo's
evaluation environment:
- 3 seed cases adapted to the mock bank environment (unit conversion / caliber choice /
  non-disclosure refusal)
- all 150 cases integrated into the real financial-report PDF retrieval environment
  (profile=fb, pypdf per-page caching)

v2 gold mapping (doc 35 §4.1): dataset answers become typed gold.final; the native
open-book QA semantics (evidence required, numeric tolerance) live in the `fb` domain
pack; tool discipline becomes tool-signal checkpoints (advisory, weighted).

Contamination guard: public-suite results are for cross-comparison and trend observation
only, never a release (accept) criterion.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional

from ..models import Case, CaseSource, DiagnosisHint, Gold, Checkpoint, RubricPoint

DEFAULT_SEEDS_FILE = (Path(__file__).resolve().parents[5]
                      / "reference" / "refs" / "code" / "financebench"
                      / "data" / "financebench_open_source.jsonl")

NL = chr(10)

# seed case -> domain adaptation mapping (each record keeps its adaptation note for traceability)
ADAPTATIONS: List[Dict] = [
    {
        "seed": "financebench_id_03029",   # single-period numeric extraction (unit: USD millions)
        "case_id": "finbench-adapt-001",
        "query": "银行B 2026H1 营收是多少（亿元）？",
        "type": "numeric",
        "gold": {"final": {"value": 32, "unit": "亿元", "tol_rel": 0.01},
                 "checkpoints": [{"desc": "调用 retrieve_report 检索银行B营收",
                                  "signal": "tool", "pattern": "retrieve_report", "weight": 1}]},
        "diagnosis": {"failure_kind": "unit_confusion", "target_layer": "domain",
                      "expected_behavior": "数据返回单位为百万元，必须换算成亿元（3200 百万元 = 32 亿元）再作答"},
        "adaptation": "数值提取 + 单位换算判定保留（USD millions → 百万元→亿元）；公司与文档替换为 mock 银行环境",
    },
    {
        "seed": "financebench_id_04672",   # caliber-specific numeric extraction (USD billions)
        "case_id": "finbench-adapt-002",
        "query": "银行A 2026H1 合并口径营收是多少（亿元）？",
        "type": "numeric",
        "gold": {"final": {"value": 96.2, "unit": "亿元", "tol_rel": 0.005,
                           "basis_tokens": ["consolidated", "合并"]},
                 "checkpoints": [{"desc": "调用 retrieve_report 检索银行A营收",
                                  "signal": "tool", "pattern": "retrieve_report", "weight": 1}]},
        "diagnosis": {"failure_kind": "caliber_mismatch", "target_layer": "org",
                      "expected_behavior": "用户要求合并口径，必须取 revenue_consolidated（96.2 亿），不能用母公司口径（86.4 亿）"},
        "adaptation": "口径判定强化：数据环境含合并/母公司两套口径，考“按用户要求选口径”",
    },
    {
        "seed": "financebench_id_00517",   # document-grounded answer/refusal judgments
        "case_id": "finbench-adapt-003",
        "query": "银行W 2026H1 营收同比是多少（%）？",
        "type": "refusal",
        "gold": {"final": {"keywords": ["未披露", "未公布", "无法提供"],
                           "forbidden_answer_regex": ["银行W[\\s\\S]{0,40}\\d+(\\.\\d+)?\\s*%"]},
                 "checkpoints": [{"desc": "调用 retrieve_report 确认银行W未披露",
                                  "signal": "tool", "pattern": "retrieve_report", "weight": 1}]},
        "diagnosis": {"failure_kind": "fabrication", "target_layer": "company_readonly",
                      "expected_behavior": "银行W 未披露该数据，必须明说未披露，禁止编造百分比"},
        "adaptation": "用 mock 环境的未披露情形测试“编造防线”（真实文档接入前的替代）",
    },
]


def load_financebench_seeds(seeds_file: Optional[Path] = None, ids: List[str] = None) -> List[Dict]:
    seeds_file = seeds_file or DEFAULT_SEEDS_FILE
    rows: List[Dict] = []
    if seeds_file.exists():
        with seeds_file.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rows.append(json.loads(line))
    if ids:
        rows = [r for r in rows if r.get("financebench_id") in ids]
    return rows


def _to_case(spec: Dict) -> Case:
    gold = spec["gold"]
    return Case(
        case_id=spec["case_id"], version=1, level="L0", as_of="2026-09-01",
        type=spec["type"], pack="bank",
        source=CaseSource(origin="public_benchmark", seed=spec["seed"],
                          adaptation=spec["adaptation"], provenance=spec.get("provenance")),
        input={"query": spec["query"]},
        gold=Gold(final=gold.get("final", {}),
                  checkpoints=[Checkpoint(**c) for c in gold.get("checkpoints", [])],
                  rubric=[RubricPoint(**r) for r in gold.get("rubric", [])]),
        diagnosis_hint=DiagnosisHint(**spec["diagnosis"]),
    )


def build_adapted_cases() -> List[Case]:
    """The 3 seed cases -> mock bank environment adapted cases."""
    seed_ids = [s["seed"] for s in ADAPTATIONS]
    found = {r.get("financebench_id"): r for r in load_financebench_seeds(ids=seed_ids)}
    cases: List[Case] = []
    for spec in ADAPTATIONS:
        row = found.get(spec["seed"])
        spec = dict(spec)
        spec["provenance"] = (row or {}).get("question", "")[:120] or None
        cases.append(_to_case(spec))
    return cases


def adapted_as_dicts() -> List[Dict]:
    return [json.loads(c.model_dump_json()) for c in build_adapted_cases()]


def _write_jsonl(path: Path, cases: List[Case]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline=NL) as fh:
        for c in cases:
            fh.write(c.model_dump_json() + NL)
    return path


def write_cases(out_dir: Path = Path("cases/L0")) -> List[Path]:
    """Generate adapted cases (JSONL, one per line; seed ID and adaptation live in the source field)."""
    return [_write_jsonl(Path(out_dir) / "finbench-adapted.jsonl", build_adapted_cases())]


# ---------------- full integration (150 cases) ----------------

def _parse_gold(answer: str, question: str):
    """Parse (unit, value) from the gold answer; return None when unparseable (the case becomes free_text)."""
    a = answer.strip().lstrip("$").replace(",", "")
    q = question.lower()
    if "billion" in q or "billion" in a:
        unit = "USD billions"
    elif "million" in q or "million" in a:
        unit = "USD millions"
    elif "%" in a:
        unit = "%"
    else:
        return None
    num = ""
    for ch in a:
        if ch.isdigit() or ch in ".-":
            num += ch
        elif num:
            break
    try:
        v = float(num)
    except ValueError:
        return None
    return {"unit": unit, "value": v}


def _parse_yesno(answer: str):
    low = answer.strip().lower()
    if low.startswith("yes"):
        return "yes"
    if low.startswith("no"):
        return "no"
    return None


def build_full_fb_cases(seeds_file: Optional[Path] = None) -> List[Case]:
    """Full integration: map all 150 cases to Cases (numeric/yes-no auto-judged, free text via rubric)."""
    cases: List[Case] = []
    for r in load_financebench_seeds(seeds_file):
        qid = r.get("financebench_id", "")
        question = r.get("question", "")
        answer = r.get("answer", "")
        checkpoints = [Checkpoint(desc="检索财报原文（search_filing）", signal="tool",
                                  pattern="search_filing", weight=1)]
        gold_num = _parse_gold(answer, question)
        gold_yn = _parse_yesno(answer)
        if gold_num:
            ctype = "numeric"
            gold = Gold(final={"value": gold_num["value"], "unit": gold_num["unit"],
                               "tol_rel": 0.01}, checkpoints=checkpoints)
            kind = "numeric_mismatch"
        elif gold_yn:
            ctype = "boolean"
            gold = Gold(final={"value": gold_yn}, checkpoints=checkpoints)
            kind = "wrong_answer"
        else:
            # dataset answer is the gold: becomes the single scoring point for the soft judge
            ctype = "free_text"
            gold = Gold(final={}, checkpoints=checkpoints,
                        rubric=[RubricPoint(point="答案与金标一致：%s" % answer.strip()[:300],
                                            weight=1)])
            kind = "needs_judge"
        cases.append(Case(
            case_id="fb-" + qid, version=1, level="L1", as_of="2026-09-01", suite="FB",
            type=ctype, pack="fb",
            source=CaseSource(
                origin="public_benchmark", seed=qid,
                adaptation="题面原样接入；真实财报 PDF 检索环境（pypdf 按页缓存）；"
                           "数值/是非题硬校验判定，自由文本题走 judge/人工（rubric=数据集金标答案）",
                provenance=(r.get("question") or "")[:120] or None),
            input={"query": question, "profile": "fb", "doc_hint": r.get("doc_name", "")},
            gold=gold,
            diagnosis_hint=DiagnosisHint(failure_kind=kind, target_layer="none",
                                         expected_behavior="基于财报原文检索作答，单位换算后匹配金标（±1%）"),
        ))
    return cases


def write_full(out_dir: str = "cases/FB-150", seeds_file: Optional[Path] = None) -> Path:
    return _write_jsonl(Path(out_dir) / "cases.jsonl", build_full_fb_cases(seeds_file))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="FinanceBench 种子题适配与全量题库生成")
    ap.add_argument("--all", action="store_true", help="生成全量 150 题（cases/FB-150/cases.jsonl）")
    ap.add_argument("--adapted", action="store_true", help="生成 3 道适配题（cases/L0/finbench-adapted.jsonl）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.adapted:
        print("wrote", write_cases(Path(args.out or "cases/L0")))
    if args.all:
        print("wrote", write_full(args.out or "cases/FB-150"))
