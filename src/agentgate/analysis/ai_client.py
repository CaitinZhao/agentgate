"""Optional LLM enhancements (doc 34 ★★+ items, consumed by doc 35 C5).

Everything here is opt-in: the caller passes a per-user AI config
{base_url, api_key, model, prompts?} taken from the user's User-Center settings.
Without a config these functions are never called — the platform stays fully
deterministic. `prompts` is the user's prompt overrides ({name: template}); all
templates live in analysis/prompts.py and every one of them is user-editable.

Iron rule (35 §4.1.1 / 全景 §六): the LLM structures and drafts; it never decides
the gold answer. Judge output is a SUGGESTION (default: verdict unchanged; adoption
only when the user enabled high-confidence auto-adopt, recorded in judge.jsonl).
"""
import json
import re
from typing import Dict, List, Optional

from . import prompts as prompt_reg


def chat(cfg: Dict, messages: List[Dict], timeout: int = 120,
         temperature: float = 0.2) -> Optional[Dict]:
    """One OpenAI-compatible chat completion. Returns {content, usage, model} or None."""
    if not cfg or not cfg.get("base_url") or not cfg.get("api_key") or not cfg.get("model"):
        return None
    import time as _time
    for attempt in range(2):          # one retry: transient gateway blips are common
        try:
            import httpx
            r = httpx.post(
                cfg["base_url"].rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + cfg["api_key"]},
                json={"model": cfg["model"], "temperature": temperature, "messages": messages},
                timeout=timeout)
            r.raise_for_status()
            d = r.json()
            return {"content": (d.get("choices") or [{}])[0].get("message", {}).get("content", ""),
                    "usage": d.get("usage") or {}, "model": d.get("model", cfg["model"])}
        except Exception:
            if attempt == 0:
                _time.sleep(3)
    return None


def _parse_json_block(text: str) -> Optional[Dict]:
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def _overrides(cfg: Dict) -> Optional[Dict]:
    return cfg.get("prompts") if isinstance(cfg.get("prompts"), dict) else None


# ---------------- L2 soft judge (suggestion only) ----------------

def judge_suggestion(cfg: Dict, case, pack: Dict, response: Dict,
                     judge_out: Dict) -> Optional[Dict]:
    """Layer-2 soft judge for INDETERMINATE cases (free_text etc.). SUGGESTION ONLY."""
    g = case.gold
    rubric = "；".join("%d) %s（权重%s）" % (i + 1, p.point, p.weight)
                        for i, p in enumerate(g.rubric)) or "（无 rubric，按题面判断）"
    hints = "；".join(pack.get("judge_hints") or [])
    cps = "；".join(c.desc for c in g.checkpoints)
    traj = "工具调用 %s 次" % ((judge_out.get("process") or {}).get("n_tool_calls", 0))
    text = prompt_reg.render("judge", _overrides(cfg),
                             rubric, hints, str(case.input.get("query", ""))[:400],
                             json.dumps(g.final, ensure_ascii=False)[:400],
                             str(response.get("answer_text", ""))[:1500], traj)
    if not text:
        return None
    r = chat(cfg, [{"role": "user", "content": text}], temperature=0.0)
    if not r:
        return None
    out = _parse_json_block(r["content"])
    if not out or out.get("verdict_suggest") not in ("PASS", "FAIL", "PARTIAL"):
        return None
    coverage = []
    for i, p in enumerate(g.rubric):
        c = next((c for c in (out.get("coverage") or [])
                  if isinstance(c, dict) and str(c.get("point")) == str(i + 1)), None)
        coverage.append({"point": p.point, "weight": p.weight,
                         "hit": bool(c and c.get("hit")),
                         "evidence": str(c.get("evidence", ""))[:200] if c else ""})
    return {"verdict_suggest": out["verdict_suggest"],
            "score": out.get("score"), "confidence": out.get("confidence", "low"),
            "rationale": str(out.get("rationale", ""))[:500],
            "citations": [str(c)[:200] for c in (out.get("citations") or [])[:3]],
            "coverage": coverage,
            "covered": sum(1 for c in coverage if c["hit"]),
            "model": r["model"], "usage": r["usage"], "kind": "llm-judge"}


# ---------------- AI report summary (34 §三) ----------------

def summarize_report(cfg: Dict, report_text: str, failed_cases: List[Dict],
                     lang: str = "zh") -> Optional[str]:
    """Executive summary + top improvement actions for the run report (zh/en prompts)."""
    fails = "；".join("%s:%s" % (f.get("case_id"), str(f.get("asi", ""))[:120])
                      for f in failed_cases[:10])
    name = "ai_summary_en" if lang == "en" else "ai_summary"
    text = prompt_reg.render(name, _overrides(cfg), fails or "无", report_text[:8000])
    if not text:
        return None
    r = chat(cfg, [{"role": "user", "content": text}], temperature=0.3, timeout=300)
    return r["content"].strip() if r and r.get("content", "").strip() else None


def attribute_errors(cfg: Dict, failed_cases: List[Dict],
                     lang: str = "zh") -> Optional[List[Dict]]:
    """Per-failed-case root-cause note (34 §三 error attribution), capped at 10 cases."""
    if not failed_cases:
        return None
    rows = "\n".join("%s | 题面：%s | 诊断：%s" % (
        f.get("case_id"), str(f.get("query", ""))[:120], str(f.get("asi", ""))[:200])
        for f in failed_cases[:10])
    name = "attribute_errors_en" if lang == "en" else "attribute_errors"
    text = prompt_reg.render(name, _overrides(cfg), rows)
    if not text:
        return None
    r = chat(cfg, [{"role": "user", "content": text}], temperature=0.2, timeout=300)
    if not r:
        return None
    m = re.search(r"\[[\s\S]*\]", r["content"])
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
        return [x for x in out if isinstance(x, dict) and x.get("case_id")]
    except ValueError:
        return None


# ---------------- domain pack / gold drafting (35 §4.1.1 / §4.2.1) ----------------

def draft_domain_pack(cfg: Dict, description: str, sample_cases: str) -> Optional[Dict]:
    """LLM drafts a pack JSON; the human reviews every item before it is saved
    (authored_by becomes llm-draft+human-reviewed only after review)."""
    example = json.dumps({
        "pack_id": "your-domain", "version": "0.1.0", "profile": "剖面名或空",
        "materials": "材料说明", "tool_strict": True, "evidence_required": True,
        "caliber_required": False,
        "red_lines": {"forbidden_tools": [], "forbidden_answer_regex": []},
        "judge_hints": ["必须引用条款原文"],
        "types": {"numeric": {"default_keys": ["value", "unit", "evidence", "answer"]},
                  "free_text": {"judge_hints": ["引用原文", "数值须与检索一致"]}},
        "authored_by": "llm-draft", "source": "llm-draft"}, ensure_ascii=False)
    text = prompt_reg.render("draft_pack", _overrides(cfg),
                             example, description, sample_cases[:3000])
    if not text:
        return None
    r = chat(cfg, [{"role": "user", "content": text}], temperature=0.3, timeout=180)
    if not r:
        return None
    out = _parse_json_block(r["content"])
    if out and out.get("pack_id"):
        out.setdefault("authored_by", "llm-draft")
        out.setdefault("source", "llm-draft")
        return out
    return None


def draft_case_gold(cfg: Dict, query: str, material: str, pack_hints: str) -> Optional[Dict]:
    """Auto-type the case + draft structured gold from the given material/dataset answer.

    Iron rule: the material (or the dataset answer) is the gold source — the LLM only
    structures it. The author confirms each item in the UI before saving."""
    text = prompt_reg.render("draft_case_gold", _overrides(cfg),
                             pack_hints or "无", query, material[:4000])
    if not text:
        return None
    r = chat(cfg, [{"role": "user", "content": text}], temperature=0.1, timeout=180)
    if not r:
        return None
    out = _parse_json_block(r["content"])
    if out and out.get("type") in ("numeric", "boolean", "extractive", "free_text", "refusal"):
        out.setdefault("gold", {})
        out["gold"].setdefault("final", {})
        out["gold"].setdefault("checkpoints", [])
        out["gold"].setdefault("rubric", [])
        return out
    return None


# ---------------- rubric-point drafting (decompose a gold answer into checkable points) ----------------

def draft_rubric(cfg: Dict, query: str, answer: str) -> Optional[Dict]:
    """Decompose a gold answer into 3-6 independently checkable rubric points (AI drafts,
    the author confirms each point in the form). Iron rule: points come ONLY from the
    question and the gold answer — the LLM never invents new facts."""
    prompt = ("把下面的评测题标准答案拆解为可核对的评分点（3-6 条），供 AI judge/人工逐条比对。"
              "规则：只依据题面与标准答案本身；每条独立可判（数值/事实/关键表述/约束条件）；"
              "不得发明答案之外的要点；数值类要点写明期望值与单位。"
              "只输出 JSON：{\"rubric\": [{\"point\": \"一句话评分点\", \"weight\": 1}]}\n\n"
              "题面：%s\n\n标准答案：%s" % (str(query)[:600], str(answer)[:1200]))
    r = chat(cfg, [{"role": "user", "content": prompt}], temperature=0.1, timeout=180)
    if not r:
        return None
    out = _parse_json_block(r["content"])
    if not out or not isinstance(out.get("rubric"), list) or not out["rubric"]:
        return None
    rubric = [{"point": str(x.get("point", ""))[:200], "weight": max(1, int(x.get("weight") or 1))}
              for x in out["rubric"] if isinstance(x, dict) and x.get("point")]
    return {"rubric": rubric} if rubric else None


# ---------------- gold revision on dispute (user disagrees with a verdict) ----------------

def draft_gold_fix(cfg: Dict, query: str, current_gold: Dict, agent_answer: str,
                   verdict_note: str, material: str = "") -> Optional[Dict]:
    """The user disputes a case's verdict: the LLM reviews query + current gold + the
    agent's actual answer + the judging rationale, and drafts a REVISED gold. The iron
    rule still applies — the draft explicitly distinguishes "agent is wrong" (keep gold)
    from "gold is unreasonable" (propose a fix), and the user confirms in the UI."""
    text = prompt_reg.render("draft_gold_fix", _overrides(cfg),
                             str(query)[:600], json.dumps(current_gold, ensure_ascii=False)[:1500],
                             str(agent_answer)[:1500], str(verdict_note)[:400],
                             str(material or "")[:1500])
    if not text:
        return None
    r = chat(cfg, [{"role": "user", "content": text}], temperature=0.1, timeout=180)
    if not r:
        return None
    out = _parse_json_block(r["content"])
    if not out or "diagnosis" not in out:
        return None
    out.setdefault("changed", False)
    out.setdefault("gold", current_gold)
    if out.get("changed") and out.get("type") not in \
            ("numeric", "boolean", "extractive", "free_text", "refusal", "auto"):
        out["changed"] = False
    return out
