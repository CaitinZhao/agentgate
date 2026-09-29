"""Local full-run orchestrator: seed every bank into a LOCAL platform data dir and
enqueue one full run per bank (target = the local sample agent on :8210).

Used when the remote deploy credentials are not available in the session — the whole
stack (platform web + worker + resident OTLP receiver + local sample agent) runs on this
machine against the real GLM5.3-Flash gateway, so the full-run token accounting is real.

Usage (same AGENTGATE_DATA_DIR as the local platform process):
  export AGENTGATE_DATA_DIR=<abs>/agentgate-platform-local/data
  python agentgate/deploy/local_fullrun.py seed            # banks + cases + assets + user
  python agentgate/deploy/local_fullrun.py enqueue [bank...]   # one full run per bank (FIFO)
  python agentgate/deploy/local_fullrun.py status          # run table overview
"""
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "agentgate" / "src"))
CASES = ROOT / "agentgate" / "cases"

AGENT_URL = os.environ.get("LOCAL_AGENT_URL", "http://127.0.0.1:8210")

# bank name -> (cases.jsonl relative path, requirements, run order priority)
BANKS = {
    "example": ("example/cases.json", {}, 1),
    "bfcl": ("bfcl/cases.jsonl", {"profile": "bfcl", "pack": "bfcl"}, 2),
    "gaia": ("gaia/cases.jsonl", {"profile": "gaia", "pack": "gaia"}, 3),
    "fineval": ("fineval/cases.jsonl", {"profile": "", "pack": "fb"}, 4),
    "injection": ("injection/cases.jsonl", {"profile": "", "pack": "injection"}, 5),
    "harmbench": ("harmbench/cases.jsonl",
                  {"profile": "harmbench", "pack": "harmbench", "tool_strict": False}, 6),
    "airbench": ("airbench/cases.jsonl", {"profile": "airbench", "pack": "airbench"}, 7),
    "spider": ("spider/cases.jsonl", {"profile": "spider", "pack": "spider", "env_scope": "judge-only"}, 8),
    "longmem": ("longmem/cases.jsonl", {"profile": "longmem", "pack": "locomo"}, 9),
    "fb-150": ("FB-150/cases.jsonl", {"profile": "fb", "pack": "fb"}, 10),
    "locomo": ("locomo/cases.jsonl", {"profile": "locomo", "pack": "locomo"}, 11),
    "tau-airline": ("tau-airline/cases.jsonl", {"profile": "tau-airline", "pack": "tau"}, 12),
    "tau-retail": ("tau-retail/cases.jsonl", {"profile": "tau-retail", "pack": "tau"}, 13),
    # agentdojo runs only on the deployed agent image (official agentdojo pkg, py3.11)
}

DISPLAY = {
    "example": ("Example 核心自建题", "self-built"),
    "bfcl": ("BFCL 函数调用（v4 抽样）", "open-benchmark"),
    "gaia": ("GAIA 通用助手（validation 文本子集）", "open-benchmark"),
    "fineval": ("FinEval 金融考试题", "open-benchmark"),
    "injection": ("注入抗性（自建语料）", "self-built"),
    "harmbench": ("HarmBench 安全红队（standard）", "open-benchmark"),
    "airbench": ("AIR-Bench 检索（qa/wiki/en dev 子集）", "open-benchmark"),
    "spider": ("Spider 文本到 SQL（8 库全题）", "open-benchmark"),
    "longmem": ("LongMemEval 跨会话记忆（oracle）", "open-benchmark"),
    "fb-150": ("FinanceBench 真实财报问答", "open-benchmark"),
    "locomo": ("LoCoMo 长对话记忆", "open-benchmark"),
    "tau-airline": ("τ-bench airline（单轮适配）", "open-benchmark"),
    "tau-retail": ("τ-bench retail（单轮适配）", "open-benchmark"),
}


def _db():
    from agentgate.webapp import db
    return db


def seed():
    db = _db()
    from agentgate.webapp import auth, banks
    from agentgate.case.store import upsert_cases
    from agentgate.case.loader import load_cases
    db.connect().close()
    if not db.list_users(roles=["admin"]):
        auth.register("qa-robot", "qa-robot-pass-1", "QA Robot")
        con = db.connect()
        con.execute("UPDATE users SET role='admin' WHERE username='qa-robot'")
        con.commit()
        con.close()
        print("    用户 qa-robot (admin) 已创建")
    user = db.get_user_by_name("qa-robot")
    for name, (rel, requirements, _prio) in BANKS.items():
        src = CASES / rel
        if not src.is_file():
            print("    [跳过] %s：%s 不存在（先 build_banks）" % (name, src))
            continue
        if db.get_benchmark(name):
            print("    [已有] %s" % name)
            continue
        display, category = DISPLAY.get(name, (name, "open-benchmark"))
        db.create_benchmark(name=name, visibility="public", owner_id=user["id"],
                            display_name=display, category=category, default_level="L2",
                            description_zh="开源/自建题库（本地全量跑测）",
                            source_note="local-fullrun",
                            requirements=json.dumps(requirements, ensure_ascii=False))
        path = banks.bank_db_path(db.get_benchmark(name), user["username"])
        banks.create_bank_db(path)
        cases = load_cases(src)
        # bank name must match the suite used for grouping; keep the original suite
        for c in cases:
            if not c.suite:
                c.suite = name
        upsert_cases(str(path), cases)
        # bank-side assets (spider DBs) resolved by the native scorer via the bank dir
        assets_src = src.parent / "dbs"
        if assets_src.is_dir():
            dst = Path(path).parent / "dbs"
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(assets_src, dst)
            print("    资产 dbs/ → %s" % dst)
        print("    [完成] %s：%d 题" % (name, len(cases)))


def enqueue(names=None):
    db = _db()
    user = db.get_user_by_name("qa-robot")
    if not user:
        print("先跑 seed")
        return
    items = sorted(BANKS.items(), key=lambda kv: kv[1][2])
    if names:
        items = [(k, v) for k, v in items if k in names]
    for name, (_rel, requirements, _prio) in items:
        if not db.get_benchmark(name):
            print("    [跳过] %s 未灌库" % name)
            continue
        run = db.create_run(name="全量-%s" % name, user_name="qa-robot",
                            created_by=user["id"], bank_filter=[{"bank": name, "levels": []}],
                            target_url=AGENT_URL, proxy_enabled=False, receiver_port=4318)
        print("    [排队] %s -> run %s" % (name, run["id"]))


def clean():
    """Drop runs whose items contain connection-refused garbage (agent-down windows),
    or runs with fewer cases than their bank's real count (e.g. collapsed imports)."""
    db = _db()
    con = db.connect()
    con.row_factory = __import__("sqlite3").Row
    bad = []
    for r in con.execute("SELECT id, name, status, total_cases FROM runs "
                         "WHERE status != 'running'").fetchall():
        rows = con.execute(
            "SELECT count(*) n, sum(CASE WHEN asi LIKE '%10061%' OR asi LIKE '%积极拒绝%' "
            "THEN 1 ELSE 0 END) refused FROM run_items WHERE run_id=?", (r["id"],)).fetchone()
        n = db.get_benchmark(r["name"].replace("全量-", "")) and             sum(1 for _ in con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table'")) or 0
        if rows["refused"]:
            bad.append(r["id"])
    for rid in bad:
        con.execute("DELETE FROM run_items WHERE run_id=?", (rid,))
        con.execute("DELETE FROM runs WHERE id=?", (rid,))
    con.commit()
    con.close()
    print("dropped garbage runs:", bad)


def status():
    db = _db()
    con = db.connect()
    con.row_factory = __import__("sqlite3").Row
    try:
        rows = con.execute("SELECT id, name, status, total_cases, done_cases, score, "
                           "gate_decision, created_at FROM runs ORDER BY created_at").fetchall()
    finally:
        con.close()
    for r in rows:
        print("%s  %-22s %-9s %s/%s  score=%s  %s" % (
            r["id"], r["name"], r["status"], r["done_cases"], r["total_cases"],
            r["score"], r["gate_decision"] or ""))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "seed":
        seed()
    elif cmd == "clean":
        clean()
    elif cmd == "enqueue":
        enqueue(set(sys.argv[2:]) or None)
    else:
        status()
