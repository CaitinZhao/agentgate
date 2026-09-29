"""AI prompt registry — every LLM prompt the platform ships, exposed for user overrides.

Users customize prompts in the User Center (per-user, stored in user_settings as the
JSON key `ai_prompts`); an empty text resets to the builtin default. Templates use %s
slots — the slot meaning is documented per prompt and enforced by callers; a broken
custom template (missing slots / bad format) falls back to the builtin default so a
typo can never break a run.
"""
import json
from typing import Dict, Optional

PROMPTS: Dict[str, Dict] = {
    "judge": {
        "title": "L2 裁判（建议）", "title_en": "L2 judge (suggestion)",
        "desc": "自由文本题硬校验无结论时，LLM 对照 rubric 给 PASS/FAIL 建议，并逐条给出"
                "评分点覆盖情况。槽位依次为：%s=评分要点(编号列表) %s=领域评分提示 %s=题面 "
                "%s=金标final %s=Agent答复 %s=轨迹摘要",
        "desc_en": "For free_text cases the LLM scores the answer against the rubric and "
                   "suggests PASS/FAIL with per-point coverage. Slots: %s=rubric(numbered) "
                   "%s=pack hints %s=question %s=gold final %s=agent answer %s=trajectory digest",
        "default": """你是评测系统的 L3 裁判。对照评分要点给 Agent 答复打分，只输出 JSON：
{"verdict_suggest": "PASS|FAIL|PARTIAL", "score": 0-100, "confidence": "high|medium|low",
 "rationale": "一句话理由", "citations": ["答复中支持结论的原文片段"],
 "coverage": [{"point": 1, "hit": true, "evidence": "答复中命中该点的原文或简述"}]}

评分要点（rubric，编号即 coverage.point）：
%s
领域评分提示：%s
题目：%s
金标参考（final，仅参考，不覆盖 rubric）：%s
Agent 答复：%s
轨迹摘要（供参考）：%s

规则：只依据 rubric 与答复本身评判；coverage 必须逐条覆盖上面每个编号，不确定时 hit=false
并说明缺少什么；confidence 保守；不得发明 rubric 之外的要求。""",
    },
    "ai_summary": {
        "title": "AI 报告摘要", "title_en": "AI report summary (Chinese)",
        "desc": "run 报告顶部的一段结论与改进建议。槽位依次为：%s=失败题诊断摘要 %s=报告正文",
        "default": """你是评测平台的报告撰写员。下面给出一轮评测的统计报告，请严格按以下结构输出（不要客套、不要复述原始数据、每条都要有报告中的数字或归因作支撑）：
【结论】1-2 句：总体表现 + 最突出的一个问题。
【主要问题】最多 3 条，每条一行：问题描述 + 数据证据 + 影响面（多少题/哪个维度）。
【改进动作】2-4 条，每条一行：具体动作 + 改哪一层（提示词/工具/检索/流程/环境）+ 预期收益。
失败题诊断摘要：%s

报告：
%s""",
    },
    "ai_summary_en": {
        "title": "AI report summary (English)",
        "desc": "English variant of the report summary. Slots: %s=failed-case digest %s=report body",
        "default": """You are the report writer of an evaluation platform. Below is the statistics report of one run. Follow this structure EXACTLY (no pleasantries, no raw data dumps; every line must cite numbers or attributions from the report):
[Conclusion] 1-2 sentences: overall performance + the single most prominent problem.
[Main problems] Up to 3, one line each: problem + data evidence + impact (how many cases / which dimension).
[Improvements] 2-4 items, one line each: concrete action + which layer (prompt/tools/retrieval/flow/environment) + expected benefit.
IMPORTANT: write the ENTIRE answer in English, even if the report contains Chinese fragments.
Failed-case digest: %s

Report:
%s""",
    },
    "attribute_errors_en": {
        "title": "Failure root-cause attribution (English)",
        "desc": "Per-failed-case root cause + fix in English. Input lines: case_id | question | diagnosis; output a JSON array",
        "default": """Each line below is a failed evaluation case. Output one JSON array element per line:
{"case_id": "...", "root_cause": "one-sentence root cause", "fix": "one-sentence fix suggestion"}.
IMPORTANT: write root_cause and fix in English, even if the input contains Chinese fragments.
%s""",
    },
    "attribute_errors": {
        "title": "失败题根因归因", "title_en": "Failure root-cause attribution (Chinese)",
        "desc": "对失败题逐题给根因与修复建议。输入为多行“case_id | 题面 | 诊断”，"
                "输出 JSON 数组 [{case_id, root_cause, fix}]",
        "default": """以下每行是一道失败评测题。对每题输出一行 JSON 数组元素：
{"case_id": "...", "root_cause": "一句话根因", "fix": "一句话修复建议"}。
%s""",
    },
    "draft_pack": {
        "title": "AI 起草领域包", "title_en": "AI domain-pack drafting",
        "desc": "给领域描述与样例题，起草领域包 JSON（红线与金标必须人工审定）。槽位依次为："
                "%s=示例结构JSON %s=领域描述 %s=样例题",
        "default": """请为以下评测领域起草一个领域包 JSON（字段含义：profile=环境剖面；materials=材料说明；
evidence_required=答案是否必须附证据引用；caliber_required=是否要求口径声明；
red_lines=题库级红线（违禁工具/禁用表述正则）；judge_hints=自由题评分提示；
types=按题型的判定默认）。只输出 JSON。
注意：红线与金标来源必须来自我给的领域描述，不得发明没有依据的数值类金标。
示例结构：%s

领域描述：%s

样例题（含材料示例）：
%s""",
    },
    "draft_case_gold": {
        "title": "AI 起草 gold（出题）", "title_en": "AI gold drafting (case authoring)",
        "desc": "从材料/数据集金标结构化出 gold（AI 不发明金标）。槽位依次为："
                "%s=领域评分提示 %s=题面 %s=材料/金标原文",
        "default": """为下面的评测题判定题型并起草 gold。题型只取：numeric/boolean/extractive/free_text/refusal。
规则：材料里能确定的数值→numeric（给 value/unit/tol_rel 0.01）；是否题→boolean（yes/no）；
短答案→extractive（value+aliases）；材料未覆盖→refusal 或 free_text；
自由题给 rubric 评分要点（来自材料要点，不得编造）。
只输出 JSON：{"type": "...", "gold": {"final": {...}, "checkpoints": [{"desc": "...", "signal": "tool|content", "pattern": "...", "weight": 1}], "rubric": [{"point": "...", "weight": 1}]}}
领域评分提示：%s

题面：%s

材料/金标原文：%s""",
    },
    "draft_gold_fix": {
        "title": "AI 辅助修订 gold（结果异议）", "title_en": "AI gold revision (dispute a verdict)",
        "desc": "用户对某题判定有异议时，结合题面/现行 gold/Agent 实际答复/判定理由，"
                "起草修订后的 gold。AI 只做结构化与建议，最终以用户确认为准。槽位依次为："
                "%s=题面 %s=现行gold JSON %s=Agent答复 %s=判定结果与理由 %s=材料或参考信息",
        "default": """用户对下面这道评测题的判定结果有异议，请你帮忙修订 gold（判定配置）。

题面：%s
现行 gold（JSON）：%s
Agent 实际答复：%s
判定结果与理由：%s
材料或参考信息（可能为空）：%s

要求：
1) 先判断"是 Agent 答错"还是"gold/检查点本身不合理或有遗漏"。若 Agent 确实答错，不要迎合用户改 gold，说明原因即可（gold 不动）。
2) 若 gold 确有不合理处（如答案别名缺失、容差过严、题型选错、检查点缺失），输出修订后的完整 gold。
3) 只输出 JSON：{"diagnosis": "Agent答错 | gold不合理（原因）", "explanation": "一句话说明",
   "changed": true/false, "type": "numeric|boolean|extractive|free_text|refusal",
   "gold": {"final": {...}, "checkpoints": [{"desc": "...", "signal": "tool|content", "pattern": "...", "weight": 1}], "rubric": [{"point": "...", "weight": 1}]}}
changed=false 时 gold 可为原 gold。""",
    },
}


def get_template(name: str, overrides: Optional[Dict]) -> str:
    """The effective template for a prompt name (user override > builtin default)."""
    if overrides and isinstance(overrides.get(name), str) and overrides[name].strip():
        return overrides[name]
    return PROMPTS[name]["default"] if name in PROMPTS else ""


def render(name: str, overrides: Optional[Dict], *args) -> Optional[str]:
    """Format the effective template; a broken custom template falls back to the default."""
    template = get_template(name, overrides)
    try:
        return template % args
    except (TypeError, ValueError):
        if overrides and overrides.get(name):
            try:
                return PROMPTS[name]["default"] % args
            except Exception:
                return None
    return None


def parse_user_prompts(raw: str) -> Dict[str, str]:
    """user_settings.ai_prompts (JSON string) -> dict; tolerant of bad JSON."""
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return {k: str(v) for k, v in d.items() if isinstance(v, str)} \
            if isinstance(d, dict) else {}
    except ValueError:
        return {}
