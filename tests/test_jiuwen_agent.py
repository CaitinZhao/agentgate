"""Tests for the jiuwen sample agent: auto-skip when the SDK is missing or the LLM is unconfigured."""
import importlib.util
import os
from pathlib import Path

import pytest

pytest.importorskip("openjiuwen")

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "jiuwen_agent.py"
_spec = importlib.util.spec_from_file_location("jiwen_agent", _FIXTURE)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_constructs_without_network():
    """Construction only (no LLM call): the ReActAgentConfig chain works."""
    agent = mod.JiuwenSampleAgent(provider="OpenAI",
                                  api_base="http://127.0.0.1:9",
                                  api_key="test", model_name="test-model")
    assert agent.model_name == "test-model"
    assert agent._agent is not None


@pytest.mark.skipif(not os.environ.get("API_BASE") or not os.environ.get("API_KEY"),
                    reason="未配置 LLM 环境变量（API_BASE/API_KEY），跳过真实调用")
def test_live_invoke():
    import asyncio
    agent = mod.JiuwenSampleAgent()
    resp = asyncio.run(agent.invoke("回复两个字：连通"))
    assert resp["answer_text"]
