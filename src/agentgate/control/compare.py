"""Cross-run comparison: are differences between two models / versions / prompts on the same case bank reliable?

Maps to design doc 10's "component scorecard (ablation A/B)" and SEAGym's transferability view:
  - reliability: per-case verdict agreement, flip details (pass<->fail), token/latency deltas
  - transferability: stratified deltas between the target suite (what the change aimed at) and
    control suites (what it did not) — an improvement counts as transferable only if it lands
    on the target suite without regressing controls (no "fix one, break ten")

Usage: agentgate compare --runs results/runA --runs runB [--runs results/runC ...] [--out compare.md]
"""
import json
from pathlib import Path
from typing import List


def _verdict(r: dict) -> str:
    """Same verdict vocabulary as single-run reports: PASS / PENDING / FAIL."""
    s = r["scores"]
    if s.get("human_review") == "pending":
        return "PENDING"
    return "PASS" if (s["deterministic_pass"] and s["rule_pass"]) else "FAIL"


def _load_run(run_dir: str) -> dict:
    p = Path(run_dir)
    results = json.loads((p / "eval_results.json").read_text(encoding="utf-8"))
    by_task = {r["task_id"]: r for r in results}
    return {"dir": run_dir,
            "verdicts": {tid: _verdict(r) for tid, r in by_task.items()},
            "tokens": {tid: int(r.get("cost", {}).get("tokens", 0))
                       for tid, r in by_task.items()},
            "wall": {tid: float(r.get("cost", {}).get("wall_time_s", 0))
                     for tid, r in by_task.items()}}


def compare_runs(run_dirs: List[str]) -> dict:
    runs = [_load_run(d) for d in run_dirs]
    common = set(runs[0]["verdicts"])
    for r in runs[1:]:
        common &= set(r["verdicts"])
    common = sorted(common)
    n = len(runs)

    agreements = []
    for i in range(n):
        for j in range(i + 1, n):
            flips = [{"task": t, "from": runs[i]["verdicts"][t],
                      "to": runs[j]["verdicts"][t]}
                     for t in common if runs[i]["verdicts"][t] != runs[j]["verdicts"][t]]
            same = len(common) - len(flips)
            agreements.append({"a": run_dirs[i], "b": run_dirs[j],
                               "agreement": round(same / len(common), 4) if common else 0.0,
                               "flips": flips})

    # the contract EvalResult has no suite field -> take it from runs_meta
    suites_src = []
    for d in run_dirs:
        meta_p = Path(d) / "runs_meta.json"
        suites_src.append(json.loads(meta_p.read_text(encoding="utf-8"))
                          if meta_p.exists() else [])
    suite_of = {}
    for idx, meta in enumerate(suites_src):
        for m in meta:
            suite_of.setdefault(m.get("task_id"), m.get("suite") or "-")
    suites = sorted({suite_of.get(t, "-") for t in common})

    suite_rows = []
    for s in suites:
        tids = [t for t in common if suite_of.get(t, "-") == s]
        row = {"suite": s, "total": len(tids)}
        for idx, r in enumerate(runs):
            v = [r["verdicts"][t] for t in tids]
            row["pass_%d" % idx] = sum(1 for x in v if x == "PASS")
            row["tok_%d" % idx] = sum(r["tokens"].get(t, 0) for t in tids)
            row["wall_%d" % idx] = round(sum(r["wall"].get(t, 0) for t in tids), 1)
        suite_rows.append(row)
    return {"runs": run_dirs, "common": len(common), "agreements": agreements,
            "suite_rows": suite_rows}


def render(summary: dict, lang: str = "zh") -> str:
    zh = lang != "en"
    runs = summary["runs"]
    n = len(runs)
    t = {
        "title": "Run 对比报告（可靠性 / 可迁移性视图）" if zh
        else "Run Comparison (reliability / transferability view)",
        "compared": "参与对比" if zh else "Compared",
        "common": "共同题数" if zh else "Common cases",
        "common_note": "判定口径 PASS/PENDING/FAIL，与单 run 报告一致" if zh
        else "verdict vocabulary PASS/PENDING/FAIL, same as single-run reports",
        "agreement": "一致性" if zh else "Agreement",
        "agree_rate": "判定一致率" if zh else "verdict agreement",
        "flips": "题翻转" if zh else "flips",
        "suite_view": "分套件视图（可迁移性：改动只应提升目标套件，不回退控制套件）" if zh
        else "Per-suite view (transferability: changes should lift the target suite "
             "without regressing control suites)",
        "suite": "套件" if zh else "Suite",
        "total": "题数" if zh else "Total",
        "note1": "判读：两组在目标套件的 pass 差 = 改进幅度；控制套件 pass 差为负 = 回退。"
        if zh else "Reading: pass delta on the target suite = improvement; "
                   "negative delta on control suites = regression.",
        "note2": "温度非零时一致率天然波动（采样噪声），对比前先固定 temperature=0。"
        if zh else "Agreement fluctuates under non-zero temperature (sampling noise); "
                   "pin temperature=0 before comparing.",
    }
    lines = ["# %s" % t["title"], ""]
    lines.append("- %s：%s" % (t["compared"],
                               " vs ".join(Path(r).name for r in runs)))
    lines.append("- %s：%d（%s）" % (t["common"], summary["common"], t["common_note"]))
    lines.append("")
    lines.append("## %s" % t["agreement"])
    for a in summary["agreements"]:
        lines.append("- %s vs %s：**%.1f%%**（%d %s）"
                     % (Path(a["a"]).name, Path(a["b"]).name,
                        a["agreement"] * 100, len(a["flips"]), t["flips"]))
        for f in a["flips"][:10]:
            lines.append("    - %s：%s → %s" % (f["task"], f["from"], f["to"]))
    lines.append("")
    lines.append("## %s" % t["suite_view"])
    header = "| %s | %s | " % (t["suite"], t["total"]) + " | ".join(
        "%s pass / tok / s" % Path(r).name for r in runs) + " |"
    lines += [header, "|---|---|" + "---|" * n]
    for row in summary["suite_rows"]:
        cells = " | ".join("%d / %d / %s" % (row["pass_%d" % i], row["tok_%d" % i],
                                             row["wall_%d" % i])
                           for i in range(n))
        lines.append("| %s | %d | %s |" % (row["suite"], row["total"], cells))
    lines.append("")
    lines.append("> %s" % t["note1"])
    lines.append("> %s" % t["note2"])
    return "\n".join(lines)
