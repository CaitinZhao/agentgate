"""Baseline report (markdown): scores + diagnosis + cost + gate decision.

Bilingual by design: build_report(results, gate, meta, lang="zh"|"en") —
the eval service writes both report.md (zh) and report-en.md (en).
"""
from typing import Dict, List

_T = {
    "zh": {"title": "FinEvalLab · L0 基线报告", "time": "时间", "target": "被测目标",
           "model": "模型", "bank": "题库版本", "cases": "题",
           "gate": "门禁判定", "fails": "失败题", "score": "总分",
           "score_note": "自动可判题 pass/total；PENDING 题不计入",
           "level": "分级", "total": "总数", "suite": "套件",
           "failures_lead": "错题集（详见 failures.jsonl）：",
           "case": "题目", "verdict": "判定", "l1": "L1 数值/内容", "l2": "L2 规则",
           "tokens": "tokens", "wall": "耗时s",
           "native": "原生口径（数据集自己的评分规则，便于与论文对比）",
           "footer": "评测只负责观测与判定；accept/reject/rollback 归演进控制平面。"},
    "en": {"title": "FinEvalLab · Baseline Report", "time": "Time", "target": "Target",
           "model": "Model", "bank": "Case bank version", "cases": "cases",
           "gate": "Gate", "fails": "failed cases", "score": "Score",
           "score_note": "pass/total over auto-judgeable cases; PENDING excluded",
           "level": "Level", "total": "Total", "suite": "Suite",
           "failures_lead": "Failed cases (see failures.jsonl): ",
           "case": "Case", "verdict": "Verdict", "l1": "L1 value/content",
           "l2": "L2 rules", "tokens": "tokens", "wall": "wall s",
           "native": "Native reading (each dataset's own metric, paper-comparable)",
           "footer": "Evaluation observes and judges only; accept/reject/rollback "
                     "belongs to the evolution control plane."},
}


def _verdict(r: Dict) -> str:
    return "PASS" if (r["scores"]["deterministic_pass"] and r["scores"]["rule_pass"]) \
        else "FAIL"


def build_report(results: List[Dict], gate: Dict, meta: Dict, lang: str = "zh") -> str:
    t = _T.get(lang, _T["zh"])
    zh = lang != "en"
    colon = "：" if zh else ": "
    parens = ("（%s）" if zh else " (%s)")
    lines = []
    lines.append("# %s" % t["title"])
    lines.append("")
    lines.append("- %s%s%s" % (t["time"], colon, meta.get("time", "")))
    lines.append("- %s%s%s%s" % (t["target"], colon, meta.get("target"),
                                parens % (t["model"] + " " + meta.get("model", "-"))))
    lines.append("- %s%s%s%s" % (t["bank"], colon, meta.get("eval_set_version"),
                                parens % ("%d %s" % (len(results), t["cases"]))))
    lines.append("- %s%s**%s**%s" % (t["gate"], colon, gate["decision"],
                 ("，%s %d" % (t["fails"], len(gate["failures"]))) if gate["failures"] else ""))
    lines.append("- %s%s%s%s" % (t["score"], colon, gate.get("score", "n/a"), parens % t["score_note"]))
    lines.append("")
    if meta.get("ai_summary"):                      # AI summary up front (user request)
        lines.append(("## AI 摘要与改进建议（%s 起草，供参考）" % meta.get("ai_model", "LLM"))
                     if zh else
                     ("## AI Summary (drafted by %s, for reference)" % meta.get("ai_model", "LLM")))
        lines.append("")
        lines.append(meta["ai_summary"])
        lines.append("")
        for n in meta.get("ai_diagnostics") or []:
            lines.append("- **%s**：%s → %s" % (n.get("case_id"), n.get("root_cause", ""),
                                                n.get("fix", "")) if zh else
                         "- **%s**: %s -> %s" % (n.get("case_id"), n.get("root_cause", ""),
                                                 n.get("fix", "")))
        lines.append("")
    lines.append("| %s | %s | PASS | FAIL | PENDING |" % (t["level"], t["total"]))
    lines.append("|---|---|---|---|---|")
    for row in meta.get("level_rows", []):
        lines.append("| %s | %s | %s | %s | %s |" % (
            row["level"], row["total"], row["pass"], row["fail"], row["pending"]))
    lines.append("")
    lines.append("| %s | %s | PASS | FAIL | PENDING |" % (t["suite"], t["total"]))
    lines.append("|---|---|---|---|---|")
    for row in meta.get("suite_rows", []):
        lines.append("| %s | %s | %s | %s | %s |" % (
            row["suite"], row["total"], row["pass"], row["fail"], row["pending"]))
    lines.append("")
    n_err = (meta.get("scores") or {}).get("target_errors") or 0
    if n_err:
        lines.append(("> ⚠ %d 题目标不可达（如连接被拒）：未测到 Agent，相关维度全部 n/a，"
                      "不计入 Agent 评分。" % n_err) if zh else
                     ("> ⚠ %d case(s) could not reach the target (e.g. connection refused): "
                      "the agent was never measured — those dimensions are n/a." % n_err))
        lines.append("")
    # failure attribution DISTRIBUTION (statistics; per-case details live in the items tab)
    kinds: Dict[str, int] = {}
    n_fail = 0
    for r in results:
        if _verdict(r) != "FAIL":
            continue
        n_fail += 1
        el = r.get("error_localization") or {}
        # diagnosis_hint.failure_kind may carry a descriptive tail; aggregate by the id
        kind = str(el.get("failure_kind") or "other").split("：")[0].split(":")[0].strip() or "other"
        kinds[kind] = kinds.get(kind, 0) + 1
    if n_fail:
        lines.append(("## 失败归因分布（%d 题失败；逐题细节见逐题结果）" % n_fail) if zh else
                     ("## Failure attribution (%d failed; per-case details in the items tab)" % n_fail))
        lines.append("")
        for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]):
            lines.append("- %s%s%d" % (k, colon, v))
        lines.append("")
    scores = meta.get("scores")
    if scores:
        from ..analysis.scores import render_section as render_scores
        lines.extend(render_scores(scores, lang=lang))
        nat = scores.get("native") or {}
        if nat:
            lines.append("## %s" % t["native"])
            lines.append("")
            lines.append("| %s | %s | %s |" % (t["suite"], t["native"], t["total"]))
            lines.append("|---|---|---|")
            for sv, v in nat.items():
                rate = v.get("rate")
                extra = " (%s/%s)" % (v.get("pass"), v.get("total"))
                lines.append("| %s | %s | %s%s |" % (sv, v.get("label", ""), rate if rate is not None else "n/a", extra))
            lines.append("")
        tok = scores.get("token_summary") or {}
        if tok.get("total"):
            lines.append(("## Token 汇总（GLM 口径：模型实际用量）" if zh else
                          "## Token summary (actual model usage)")
                         + "")
            lines.append("")
            lines.append("- %s%s**%s**" % (t["tokens"], colon,
                                           "{:,}".format(tok["total"])))
            for s, v in (tok.get("by_suite") or {}).items():
                lines.append("- %s%s%s" % (s, colon, "{:,}".format(v)))
            lines.append("")
        if meta.get("repeat_k", 1) > 1:
            lines.append(("稳定性验证：repeat_k=%d（pass^k 口径，同题全部通过才计 PASS）" % meta["repeat_k"])
                         if zh else
                         ("Stability verification: repeat_k=%d (pass^k; PASS only if every repeat passed)"
                          % meta["repeat_k"]))
            lines.append("")
    lines.append("")
    lines.append("> %s" % t["footer"])
    return chr(10).join(lines)
