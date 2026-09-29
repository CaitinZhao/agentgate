"""Minimal sample target agent based on openJiuwen (agent-core), base version for evaluation.

Role:
  - the base-version target of the evaluation pipeline: validates the full agentgate chain
    (case bank / judgments / gate / reports)
  - also the reference for "third-party agent integration" (an invoke interface shaped like
    the /invoke contract)

Dependency: pip install -U openjiuwen (related tests auto-skip when missing)
LLM config: env vars MODEL_PROVIDER / API_BASE / API_KEY / MODEL_NAME
(same as the openJiuwen official examples; fin-runtime/.env gateway config also works)
"""
import json
import os
from typing import Dict

SYSTEM_PROMPT = (
    "你是金融评测的 base 被测 Agent。请直接回答用户问题。\n"
    "【输出格式】最终答复以一行 FINAL: {..json..} 结尾，"
    "json 包含 value（数值或 null）、unit、basis、answer 键。\n"
)


class JiuwenSampleAgent:
    """Minimal openJiuwen ReActAgent wrapper whose output matches the /invoke response contract."""

    name = "jiuwen-sample"

    def __init__(self, provider=None, api_base=None, api_key=None,
                 model_name=None, max_iterations: int = 5,
                 system_prompt: str = None):
        from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
        from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig

        provider = provider or os.environ.get("MODEL_PROVIDER", "OpenAI")
        api_base = api_base or os.environ.get("API_BASE", "")
        api_key = api_key or os.environ.get("API_KEY", "")
        model_name = model_name or os.environ.get("MODEL_NAME", "")
        self.model_name = model_name

        card = AgentCard(id="jiuwen-sample-agent",
                         name="JiuwenSampleAgent",
                         description="评测 base 版被测 Agent（openJiuwen ReActAgent）")
        config = ReActAgentConfig(model_config_obj=ModelRequestConfig(
            model_name=model_name, temperature=0.0))   # reproducible evaluation: pin the sampling temperature
        config.configure_model_client(provider=provider, api_key=api_key,
                                      api_base=api_base, model_name=model_name)
        config.configure_prompt_template([
            {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
            {"role": "user", "content": "{{query}}"},
        ])
        config.configure_max_iterations(max_iterations)
        self._agent = ReActAgent(card=card)
        self._agent.configure(config)

    async def invoke(self, query: str) -> Dict:
        """Run one Q&A asynchronously; returns a response shaped like the /invoke contract."""
        result = await self._agent.invoke({"query": query})
        answer = str(result.get("output", result))
        final = {}
        for line in answer.splitlines():
            if line.strip().startswith("FINAL:"):
                try:
                    final = json.loads(line.strip()[len("FINAL:"):].strip())
                except ValueError:
                    final = {}
        return {"answer_text": answer, "final_json": final,
                "trace_id": "", "usage_total": 0, "audit": []}

    def to_response(self, raw: Dict) -> Dict:
        return {"answer_text": raw.get("answer_text", ""),
                "final_json": raw.get("final_json", {}),
                "trace_id": raw.get("trace_id", ""),
                "usage_total": raw.get("usage_total", 0), "audit": []}


def dump_response(answer_text: str) -> Dict:
    """Pack an answer text into an evaluation response (final_json parsed from the FINAL line)."""
    a = JiuwenSampleAgent.__dict__  # placeholder to avoid a circular import
    final = {}
    for line in answer_text.splitlines():
        if line.strip().startswith("FINAL:"):
            try:
                final = json.loads(line.strip()[len("FINAL:"):].strip())
            except ValueError:
                final = {}
    return {"answer_text": answer_text, "final_json": final,
            "trace_id": "", "usage_total": 0, "audit": []}
