"""Public-benchmark adapter — HarmBench (standard text behaviors).

Source: github.com/centerforaisafety/harmbench (Apache-2.0), behavior_datasets CSV.
Only the `standard` functional category ships (contextual needs context passages,
copyright needs model-specific continuations — both out of scope for an agent harness).

Native metric: attack success rate (ASR) = share of behaviors the agent COMPLIES with.
Compliance is decided by (1) deterministic refusal patterns, else (2) the LLM judge's
suggestion (recorded `by=llm-judge`), else undecided (n/a) — HarmBench's official
Llama-13B classifier is not redistributable here, so the judge model is a documented
deviation. Lower ASR = better defense; the platform safety dimension still applies.

These are SECURITY test cases: they exist to measure refusal behavior, never to be a
training source. Contamination guard: observation-only.
"""
import csv
import json
from pathlib import Path
from typing import Dict, List, Optional

NL = chr(10)
DEFAULT_DATA_DIR = (Path(__file__).resolve().parents[5] / "reference" / "refs" / "code"
                    / "harmbench" / "data" / "behavior_datasets")
SOURCES = ["harmbench_behaviors_text_test.csv", "harmbench_behaviors_text_val.csv"]


def _read_behaviors(path: Path) -> List[Dict]:
    with path.open(encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r.get("FunctionalCategory") == "standard"]


def build(data_dir: Optional[Path] = None, out_dir: str = "cases/harmbench") -> Dict:
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    cases: List[Dict] = []
    for fname in SOURCES:
        for r in _read_behaviors(data_dir / fname):
            bid = r["BehaviorID"]
            split = "test" if "test" in fname else "val"
            cases.append({
                "case_id": "harmbench-%s-%s" % (split, bid), "version": 1, "level": "L2",
                "as_of": "2026-09-24", "suite": "harmbench",
                "source": {"origin": "public_benchmark", "seed": bid,
                           "adaptation": "standard 行为原题接入（安全红队）；原生口径=ASR"
                                         "（顺从率），拒答模式 + LLM judge 两级判定，"
                                         "judge 模型与官方 Llama-13B 分类器不同属已记录偏差",
                           "provenance": str(r.get("Behavior", ""))[:120]},
                "input": {"query": r["Behavior"], "profile": "harmbench",
                          "semantic_category": r.get("SemanticCategory", "")},
                "type": "free_text", "pack": "harmbench",
                "gold": {"final": {}, "checkpoints": [], "rubric": []},
                "diagnosis_hint": {"failure_kind": "safety_refusal_expected",
                                   "target_layer": "none",
                                   "expected_behavior": "拒绝有害请求并说明边界（native ASR 口径"
                                                        "pass=False=防御成功）"},
            })
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "cases.jsonl").open("w", encoding="utf-8", newline=NL) as fh:
        for c in cases:
            fh.write(json.dumps(c, ensure_ascii=False) + NL)
    (out / "README.md").write_text(
        "# HarmBench 题库（standard 文本行为，%d 题）%s%s来源：HarmBench（Apache-2.0）。"
        "原生口径=攻击成功率 ASR（越低越好）；仅用于安全观测，绝不作为训练来源或 accept 判据。%s"
        % (len(cases), NL, NL, NL), encoding="utf-8")
    return {"cases": len(cases), "out": str(out)}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="HarmBench 题库生成")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--out", default="cases/harmbench")
    a = ap.parse_args()
    print(json.dumps(build(a.data_dir, a.out), ensure_ascii=False))
