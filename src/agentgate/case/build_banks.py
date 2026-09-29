"""Case bank tools: external-dataset bank generation + validation.

Usage:
  python -m agentgate.case.build_banks --fb                    # rebuild FB-150 from FinanceBench seeds
  python -m agentgate.case.build_banks --validate cases/FB-150

In-house banks don't need this tool: edit cases/<suite>/cases.json directly
(the data file is the bank; the loader reads it as-is).
"""
import argparse
import json
from pathlib import Path

from .public_benchmarks.financebench_adapter import write_full


def validate(cases_dir):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from .loader import load_cases
    cases = load_cases(cases_dir)
    bad = 0
    for c in cases:
        for k in ("case_id", "input", "gold", "diagnosis_hint"):
            if not getattr(c, k):
                print("  [BAD] %s missing field %s" % (c.case_id, k))
                bad += 1
    print("validated %d cases, %d bad" % (len(cases), bad))
    return bad == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fb", action="store_true", help="rebuild FB-150 cases.jsonl from FinanceBench seeds")
    ap.add_argument("--locomo", action="store_true",
                    help="build the locomo bank from LoCoMo data (cases/locomo: full + sample-40)")
    ap.add_argument("--locomo-data", default=None,
                    help="path to locomo10.json (default reference/refs/code/LoCoMo/data/locomo10.json)")
    ap.add_argument("--longmem", action="store_true",
                    help="build the long-memory bank from LongMemEval oracle (cases/longmem: full + sample-40)")
    ap.add_argument("--longmem-data", default=None,
                    help="path to longmemeval_oracle.json (default reference/refs/data/longmemeval/)")
    ap.add_argument("--tau", action="store_true",
                    help="build airline/retail banks from τ-bench tasks (cases/tau-*: full + sample-10)")
    ap.add_argument("--tau-repo", default=None,
                    help="path to the tau-bench repo (default reference/refs/code/tau-bench)")
    ap.add_argument("--bfcl", action="store_true",
                    help="build the BFCL bank (cases/bfcl, seeded stratified sample)")
    ap.add_argument("--bfcl-data", default=None,
                    help="path to the BFCL data dir (default reference/refs/code/gorilla/...)")
    ap.add_argument("--spider", action="store_true",
                    help="build the Spider bank (cases/spider: 8 dev DBs, all their questions)")
    ap.add_argument("--spider-data", default=None,
                    help="path to the spider_data dir (default reference/refs/data/spider/spider_data)")
    ap.add_argument("--gaia", action="store_true",
                    help="build the GAIA bank (cases/gaia: validation text-only subset)")
    ap.add_argument("--gaia-data", default=None,
                    help="path to the GAIA validation parquet (default reference/refs/data/gaia/)")
    ap.add_argument("--airbench", action="store_true",
                    help="build the AIR-Bench bank (cases/airbench: qa/wiki/en dev subset)")
    ap.add_argument("--airbench-data", default=None,
                    help="path to the AIR-Bench data dir (default reference/refs/data/airbench)")
    ap.add_argument("--agentdojo", action="store_true",
                    help="build the AgentDojo bank (cases/agentdojo: v1_2_2 banking+workspace)")
    ap.add_argument("--agentdojo-repo", default=None,
                    help="path to the agentdojo repo (default reference/refs/code/agentdojo)")
    ap.add_argument("--harmbench", action="store_true",
                    help="build the HarmBench bank (cases/harmbench: standard behaviors)")
    ap.add_argument("--harmbench-data", default=None,
                    help="path to the HarmBench behavior_datasets dir")
    ap.add_argument("--db-import", default=None, help="import a json/jsonl case file into the SQLite bank")
    ap.add_argument("--db", default="cases/cases.db", help="SQLite case bank path")
    ap.add_argument("--validate", default=None, help="validate a bank (directory/jsonl/db)")
    args = ap.parse_args()
    if args.db_import:
        from .loader import load_cases
        from .store import upsert_cases
        n = upsert_cases(args.db, load_cases(args.db_import))
        print("upserted %d cases -> %s" % (n, args.db))
    if args.fb:
        print("wrote", write_full("cases/FB-150"))
    if args.locomo:
        from .public_benchmarks.locomo_adapter import build as build_locomo
        default_data = (Path(__file__).resolve().parents[4] / "reference" / "refs" / "code"
                        / "LoCoMo" / "data" / "locomo10.json")
        data_path = args.locomo_data or str(default_data)
        print("locomo:", build_locomo(data_path))
    if args.longmem:
        from .public_benchmarks.longmemeval_adapter import build as build_longmem
        default_data = (Path(__file__).resolve().parents[4] / "reference" / "refs" / "data"
                        / "longmemeval" / "longmemeval_oracle.json")
        data_path = args.longmem_data or str(default_data)
        print("longmem:", build_longmem(data_path))
    if args.tau:
        from .public_benchmarks.tau_bench import build as build_tau
        repo = (args.tau_repo or str(Path(__file__).resolve().parents[4] / "reference"
                                     / "refs" / "code" / "tau-bench"))
        for env in ("airline", "retail"):
            print("tau:", build_tau(env, repo))
    if getattr(args, "bfcl", False):
        from .public_benchmarks.bfcl_adapter import build as build_bfcl
        print("bfcl:", build_bfcl(getattr(args, "bfcl_data", None) or None))
    if getattr(args, "spider", False):
        from .public_benchmarks.spider_adapter import build as build_spider
        print("spider:", build_spider(getattr(args, "spider_data", None) or None))
    if getattr(args, "gaia", False):
        from .public_benchmarks.gaia_adapter import build as build_gaia
        print("gaia:", build_gaia(getattr(args, "gaia_data", None) or None))
    if getattr(args, "airbench", False):
        from .public_benchmarks.airbench_adapter import build as build_airbench
        print("airbench:", build_airbench(getattr(args, "airbench_data", None) or None))
    if getattr(args, "agentdojo", False):
        from .public_benchmarks.agentdojo_adapter import build as build_agentdojo
        print("agentdojo:", build_agentdojo(getattr(args, "agentdojo_repo", None) or None))
    if getattr(args, "harmbench", False):
        from .public_benchmarks.harmbench_adapter import build as build_harmbench
        print("harmbench:", build_harmbench(getattr(args, "harmbench_data", None) or None))
    if args.validate:
        ok = validate(args.validate)
        raise SystemExit(0 if ok else 1)
    if not (args.fb or args.locomo or args.longmem or args.tau
            or args.db_import or args.validate
            or args.bfcl or args.spider or args.gaia or args.airbench
            or args.agentdojo or args.harmbench):
        ap.print_help()


if __name__ == "__main__":
    main()
