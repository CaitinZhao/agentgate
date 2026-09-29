"""Public-benchmark adapter — Spider (text-to-SQL, Yale).

Source: github.com/taoyd/spider data (dev set; CC BY-SA 4.0). The bank ships a
documented subset: the 8 smallest top-DBs by question count (wta_1 excluded — its
SQLite alone is 105MB). ALL dev questions of the chosen DBs are included (no
question-level sampling), so the bank's full run = the whole subset.

Assets: each chosen DB's SQLite file is copied to <bank>/dbs/<db>/<db>.sqlite; the
platform's native scorer (analysis/native_scoring.score_spider) executes BOTH the gold
and the agent SQL and compares result sets (order-sensitive only when the gold SQL has
ORDER BY) — Spider's official execution-accuracy protocol. The gold SQL ships in
gold.final.sql; gold tables never reach the agent.

Agent contract: profile=spider, sql_query tool over the per-case DB; FINAL {"sql": "..."}.
"""
import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional

NL = chr(10)
DEFAULT_DATA_DIR = (Path(__file__).resolve().parents[5] / "reference" / "refs" / "data"
                    / "spider" / "spider_data")

# chosen dev DBs (question count, all except wta_1 whose sqlite is 105MB)
CHOSEN_DBS = ["world_1", "car_1", "cre_Doc_Template_Mgt", "dog_kennels", "flight_2",
              "student_transcripts_tracking", "tvshow", "network_1"]


def build(data_dir: Optional[Path] = None, out_dir: str = "cases/spider") -> Dict:
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    dev = json.loads((data_dir / "dev.json").read_text(encoding="utf-8"))
    # dev.json carries no id field; the official file order is fixed — index-based ids
    # (spider-dev0001...) are stable and reproducible across regenerations
    rows = [(i, d) for i, d in enumerate(dev) if d.get("db_id") in CHOSEN_DBS]
    # per-DB schema summary for the agent prompt (tables.sql comes from tables.json)
    tables = {t["db_id"]: t for t in
              json.loads((data_dir / "tables.json").read_text(encoding="utf-8"))}
    cases: List[Dict] = []
    for idx, d in rows:
        db = d["db_id"]
        qid = "dev%04d" % idx
        schema = _schema_text(tables.get(db) or {})
        cases.append({
            "case_id": "spider-" + qid, "version": 1, "level": "L1", "as_of": "2026-09-24",
            "suite": "spider",
            "source": {"origin": "public_benchmark", "seed": qid,
                       "adaptation": "dev 集原题接入（选定 8 库全题）；执行准确率原生口径："
                                     "agent SQL 与金标 SQL 在随库分发的 SQLite 上执行比对",
                       "provenance": str(d.get("question", ""))[:120]},
            "input": {"query": d.get("question", ""), "profile": "spider", "db": db,
                      "schema": schema},
            "type": "free_text", "pack": "spider",
            "gold": {"final": {"sql": d.get("query", ""), "db": db},
                     "checkpoints": [], "rubric": []},
            "diagnosis_hint": {"failure_kind": "sql_mismatch", "target_layer": "none",
                               "expected_behavior": "先查看 schema，再写出与金标结果集等价的 SQL"},
        })
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "cases.jsonl").open("w", encoding="utf-8", newline=NL) as fh:
        for c in cases:
            fh.write(json.dumps(c, ensure_ascii=False) + NL)
    # ship the DBs (bank assets; the platform resolves them via the bank dir)
    dbs_dir = out / "dbs"
    if dbs_dir.exists():
        shutil.rmtree(dbs_dir)
    copied = 0
    for db in CHOSEN_DBS:
        src = data_dir / "database" / db / ("%s.sqlite" % db)
        if src.is_file():
            dst = dbs_dir / db
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst / ("%s.sqlite" % db))
            copied += 1
    (out / "README.md").write_text(
        "# Spider 题库（%d 题 / %d 库，dev 子集全题）%s%s来源：Yader/Spider dev（CC BY-SA 4.0）。"
        "原生口径=执行准确率（金标 SQL 与 agent SQL 结果集比对，ORDER BY 时序敏感）。"
        "wta_1 未纳入（单库 SQLite 105MB）。%s"
        % (len(cases), copied, NL, NL, NL), encoding="utf-8")
    return {"cases": len(cases), "dbs": copied, "out": str(out)}


def _schema_text(t: Dict) -> str:
    """Compact schema text (tables + columns) for the agent's context."""
    lines = []
    for i, tn in enumerate(t.get("table_names_original", []) or []):
        cols = [c[1] for c in (t.get("column_names_original") or [])
                if c[0] == i and c[1] != "*"]
        lines.append("%s(%s)" % (tn, ", ".join(cols)))
    return NL.join(lines)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Spider 题库生成")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--out", default="cases/spider")
    a = ap.parse_args()
    print(json.dumps(build(a.data_dir, a.out), ensure_ascii=False))
