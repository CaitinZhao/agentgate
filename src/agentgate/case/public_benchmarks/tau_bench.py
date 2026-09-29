"""tau-bench / tau2-bench (tool-calling agent benchmark) adapter: task extraction -> trajectory-type case bank.

Data: reference/refs/code/tau-bench/tau_bench/envs/{airline,retail}/tasks*.py
(airline 50 + retail 115 = 165 cases; each has an instruction + actions [write-action assertions])

Artifacts:
  cases/tau-airline/cases.jsonl + sample-10.jsonl
  cases/tau-retail/cases.jsonl  + sample-10.jsonl

Judgment (trajectory type, gold v2):
  deduped write-action names from actions -> tool-signal checkpoints (weighted, partial
  credit; advisory by default — tool_strict lives in the tau domain pack)
  pure-query tasks (no write actions) -> type=free_text (PENDING until judge/human)

Honest boundary: **the dedicated environment profile comes later (P1)** - full tau-bench needs
the domain tool set (an env state machine + users/flights/orders data) and a multi-turn user
simulator; this adapter lands the data layer only.
"""
import ast
import json
import re
from pathlib import Path

from ..models import Case, CaseSource, DiagnosisHint, Gold, Checkpoint

_SKIP_PREFIX = ("get_", "search_", "list_", "find_", "calculate")


def _load_tasks(p: Path) -> list:
    tree = ast.parse(p.read_text(encoding="utf-8"))
    out = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                v = ast.literal_eval(node.value)
            except Exception:
                continue
            if isinstance(v, list) and v and isinstance(v[0], dict) and "instruction" in v[0]:
                out.extend(v)
    return out


def _write_actions(task: dict) -> list:
    """Dedupe write-action names from actions (order preserved). get_*/search_*/list_*/find_*/calculate
    are exploration means, not enforced; write actions (book/update/cancel/modify/...) are the task
    goal and become required tools."""
    names = []
    for a in task.get("actions", []):
        n = str(a.get("name", ""))
        if n.startswith(_SKIP_PREFIX):
            continue
        if n not in names:
            names.append(n)
    return names


def build(env: str, repo_root: str, out_dir: str = None) -> dict:
    """env: airline | retail; extract tasks from tasks*.py and write the bank."""
    root = Path(repo_root)
    files = sorted(root.glob("tau_bench/envs/%s/tasks*.py" % env))
    files = [f for f in files if "test_" not in f.name]
    tasks = []
    for f in files:
        tasks.extend(_load_tasks(f))

    suite = "tau-%s" % env
    out = Path(out_dir or ("cases/%s" % suite))
    out.mkdir(parents=True, exist_ok=True)
    today = __import__("datetime").date.today().isoformat()

    cases = []
    for ti, task in enumerate(tasks):
        writes = _write_actions(task)
        cid = "tau-%s-%03d" % (env, ti)
        actions_desc = "; ".join(sorted({a["name"] for a in task.get("actions", [])}))
        cases.append(Case(
            case_id=cid,
            suite=suite, level="L2", as_of=today,
            source=CaseSource(origin="public_benchmark",
                              seed="tau-bench:%s#%s" % (env, task.get("annotator", ti)),
                              adaptation="单轮适配变体（P1 前置数据层）：instruction 直接作 query，"
                                         "写操作断言转 required_tools；多轮用户模拟器与环境状态机待做",
                              provenance="user_id=%s | actions=%s | %s" % (
                                  task.get("user_id", ""), actions_desc,
                                  str(task.get("instruction", ""))[:80])),
            input={"query": task["instruction"], "profile": "tau-%s" % env},
            type=("state" if writes else "free_text"), pack="tau",
            gold=(Gold(final={}, checkpoints=[Checkpoint(
                      desc="完成写操作 %s" % w, signal="tool", pattern=w, weight=1)
                  for w in writes]) if writes else Gold(final={})),
            
            diagnosis_hint=DiagnosisHint(
                failure_kind="required_tool_missing" if writes else "judge_required",
                target_layer="domain",
                expected_behavior="按用户约束完成领域操作：%s" % (actions_desc or "见 instruction"))))

    with (out / "cases.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for c in cases:
            f.write(c.model_dump_json() + "\n")

    # sampling: prefer cases with write-action assertions
    with_tools = [c for c in cases if c.type == "state"]
    without = [c for c in cases if c.type == "free_text"]
    sample = (with_tools[:8] + without[:2])[:10]
    with (out / "sample-10.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for c in sample:
            f.write(c.model_dump_json() + "\n")

    return {"suite": suite, "tasks": len(cases),
            "with_required_tools": len(with_tools),
            "judge_required": len(without), "sample": len(sample), "out": str(out)}
