"""Public-benchmark adapter — AIR-Bench 24.05 (qa / wiki / en, dev split).

Source: hf AIR-Bench/qa_wiki_en + AIR-Bench/qrels-qa_wiki_en-dev (CC BY-NC-SA 4.0,
evaluation use only — observation-only per the contamination guard).

The full wiki corpus is ~21M documents; the bank ships a DOCUMENTED SUBSET: the qrels
documents (positives + the dataset's own hard negatives, relevance=0) of the sampled
queries, padded with randomly sampled corpus documents for search realism. The subset
corpus is shipped as a bank asset (corpus.jsonl); the agent searches it through a
doc_search tool and answers with a ranked top-10 of doc ids.

Native metric: nDCG@10 (primary) + recall@5, computed against the original qrels —
comparable with AIR-Bench's protocol up to the documented corpus-subset deviation
(the leaderboard number over the full corpus is expected to be lower than ours).
Platform typed gate: nDCG@10 >= 0.5 counts as the deterministic hard pass.
"""
import json
import random
from pathlib import Path
from typing import Dict, List, Optional

NL = chr(10)
DEFAULT_DATA_DIR = (Path(__file__).resolve().parents[5] / "reference" / "refs" / "data"
                    / "airbench")
N_QUERIES = 120
N_PAD_DOCS = 5000


def _read_jsonl(path: Path) -> List[Dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build(data_dir: Optional[Path] = None, out_dir: str = "cases/airbench",
          n_queries: int = N_QUERIES, n_pad: int = N_PAD_DOCS, seed: int = 42) -> Dict:
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    queries = _read_jsonl(data_dir / "dev_queries.jsonl")
    qrels_rows = _read_jsonl(data_dir / "dev_qrels.jsonl")
    qrels: Dict[str, Dict[str, float]] = {}
    for r in qrels_rows:
        qrels.setdefault(str(r["qid"]), {})[str(r["docid"])] = float(r.get("relevance") or 0)
    cand = [q for q in queries
            if any(v > 0 for v in qrels.get(str(q["id"]), {}).values())]
    rng = random.Random(seed)
    sampled = rng.sample(cand, min(n_queries, len(cand)))

    needed: Dict[str, float] = {}
    for q in sampled:
        for docid, rel in qrels[str(q["id"])].items():
            needed[docid] = max(needed.get(docid, 0.0), rel)

    # corpus subset: qrels docs + random padding (skipped gracefully when the
    # full corpus file is unavailable — the subset stays documented in the README)
    corpus_src = data_dir / "corpus_wiki_en.jsonl"
    docs: Dict[str, Dict] = {}
    pad_ids: List[str] = []
    if corpus_src.is_file():
        # reservoir sampling for padding while picking the needed docs in one pass;
        # resume-glitched bytes are tolerated (errors=replace) — the subset stays a
        # documented sample, a few damaged lines are skipped
        n_seen = 0
        with corpus_src.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                did = str(d.get("id", ""))
                n_seen += 1
                if did in needed:
                    docs[did] = d
                else:
                    if len(pad_ids) < n_pad:
                        pad_ids.append(did)
                        docs[did] = d
                    else:
                        j = rng.randint(0, n_seen - 1)
                        if j < n_pad:
                            old = pad_ids[j]
                            docs.pop(old, None)
                            pad_ids[j] = did
                            docs[did] = d
    cases: List[Dict] = []
    for q in sampled:
        qid = str(q["id"])
        qrel = {k: v for k, v in qrels[qid].items()}
        cases.append({
            "case_id": "airbench-" + qid, "version": 1, "level": "L1",
            "as_of": "2026-09-24", "suite": "airbench",
            "source": {"origin": "public_benchmark", "seed": qid,
                       "adaptation": "AIR-Bench 24.05 qa/wiki/en dev 查询；子集语料="
                                     "qrels 文档（含官方 hard negatives）+ 随机补样 %d；"
                                     "原生口径=nDCG@10/recall@5（对原 qrels）" % n_pad,
                       "provenance": str(q.get("text", ""))[:120]},
            "input": {"query": str(q.get("text", "")), "profile": "airbench"},
            "type": "free_text", "pack": "airbench",
            "gold": {"final": {"qrels": qrel}, "checkpoints": [], "rubric": []},
            "diagnosis_hint": {"failure_kind": "ranking_mismatch", "target_layer": "none",
                               "expected_behavior": "检索并按相关度输出 top-10 文档 id"},
        })
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "cases.jsonl").open("w", encoding="utf-8", newline=NL) as fh:
        for c in cases:
            fh.write(json.dumps(c, ensure_ascii=False) + NL)
    corpus_out = out / "corpus.jsonl"
    with corpus_out.open("w", encoding="utf-8", newline=NL) as fh:
        for did in list(needed) + pad_ids:
            d = docs.get(did)
            if d is not None:
                fh.write(json.dumps({"id": str(d.get("id")),
                                     "text": str(d.get("text", ""))[:4000]},
                                    ensure_ascii=False) + NL)
    (out / "README.md").write_text(
        "# AIR-Bench 题库（qa/wiki/en dev 子集，%d 题，语料 %d 段）%s%s"
        "来源：AIR-Bench 24.05（CC BY-NC-SA 4.0，仅评估用途）。原生口径=nDCG@10/recall@5；"
        "语料为 qrels 文档 + 随机补样的子集（官方全语料数字预期更低，对比时注意口径）。%s"
        % (len(cases), len(needed) + len(pad_ids), NL, NL, NL), encoding="utf-8")
    return {"cases": len(cases), "corpus_docs": len(docs),
            "with_corpus_file": corpus_src.is_file(), "out": str(out)}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="AIR-Bench 题库生成")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--out", default="cases/airbench")
    ap.add_argument("--n-queries", type=int, default=N_QUERIES)
    ap.add_argument("--n-pad", type=int, default=N_PAD_DOCS)
    a = ap.parse_args()
    print(json.dumps(build(a.data_dir, a.out, a.n_queries, a.n_pad), ensure_ascii=False))
