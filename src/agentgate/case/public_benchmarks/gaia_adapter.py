"""Public-benchmark adapter — GAIA (validation, text-only subset).

GAIA (Meta AI / Hugging Face, Apache-2.0) is gated on the Hub; the bank builds from a
mirror of the *text-only filtered* validation split (127 questions with official final
answers, no attachments). Every question ships (honest full bank); `needs_web` is a
heuristic flag from the annotator steps — a web-less agent is EXPECTED to fail those,
which is the point of the measurement, not a judging error.

Native metric: the official exact-match scorer (re-implemented in
analysis/native_scoring.gaia_exact_match). Platform typed check: same EM (deterministic).
"""
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

NL = chr(10)
DEFAULT_PARQUET = (Path(__file__).resolve().parents[5] / "reference" / "refs" / "data"
                   / "gaia" / "gaia_text_only.parquet")

_WEB_KW = re.compile(
    r"arxiv|google|youtube|browse|web page|online search|search (?:the |online|for)|"
    r"visit|wikipedia|github|http|internet|website", re.I)


def _needs_web(annotator) -> bool:
    steps = annotator.get("Steps", "") if isinstance(annotator, dict) else str(annotator)
    return bool(_WEB_KW.search(str(steps)))


def build(parquet: Optional[Path] = None, out_dir: str = "cases/gaia") -> Dict:
    parquet = Path(parquet or DEFAULT_PARQUET)
    df = pd.read_parquet(parquet)
    cases: List[Dict] = []
    n_web = 0
    for _, row in df.iterrows():
        qid = str(row["task_id"])
        query = str(row["Question"]).strip()
        answer = str(row["Final answer"]).strip()
        web = bool(_needs_web(row["Annotator Metadata"]))
        n_web += int(web)
        cases.append({
            "case_id": "gaia-" + qid[:8], "version": 1,
            "level": "L%d" % (1 if str(row["Level"]) == "1" else 2),
            "as_of": "2026-09-24", "suite": "gaia",
            "source": {"origin": "public_benchmark", "seed": qid,
                       "adaptation": "validation 集文本子集原题接入（官方金标）；"
                                     "原生口径=官方 EM 归一化精确匹配；needs_web 仅为观测标注",
                       "provenance": query[:120]},
            "input": {"query": query, "profile": "gaia", "needs_web": web},
            "type": "free_text", "pack": "gaia",
            "gold": {"final": {"answer": answer}, "checkpoints": [], "rubric": []},
            "diagnosis_hint": {"failure_kind": "em_mismatch", "target_layer": "none",
                               "expected_behavior": "给出与官方金标一致的简洁最终答案"},
        })
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "cases.jsonl").open("w", encoding="utf-8", newline=NL) as fh:
        for c in cases:
            fh.write(json.dumps(c, ensure_ascii=False) + NL)
    (out / "README.md").write_text(
        "# GAIA 题库（validation 文本子集，%d 题，其中启发式标注需联网 %d 题）%s%s"
        "来源：gaia-benchmark/GAIA（Apache-2.0，Hub 门控，本库由 text-only 镜像构建）。"
        "判定=官方 EM 归一化精确匹配；需联网题对无网 agent 预期失败，属真实能力缺口信号。%s"
        % (len(cases), n_web, NL, NL, NL), encoding="utf-8")
    return {"cases": len(cases), "needs_web": n_web, "out": str(out)}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="GAIA 题库生成")
    ap.add_argument("--parquet", default=None)
    ap.add_argument("--out", default="cases/gaia")
    a = ap.parse_args()
    print(json.dumps(build(a.parquet, a.out), ensure_ascii=False))
