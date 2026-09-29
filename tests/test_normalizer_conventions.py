"""Normalizer compatibility with openJiuwen's native OTel convention (T3 protocol-level; half of the two-way contract test)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentgate.trace.normalizer import normalize


def _oj_span(name, attrs, tid="a" * 32):
    return {"trace_id": tid, "span_id": "b" * 16, "name": name,
            "start_unix_nano": 1, "end_unix_nano": 2,
            "attributes": {"service.name": "jiuwen-sample-agent", **attrs}}


def test_openjiwen_native_convention():
    """openJiuwen native spans (gen_ai.*/openjiuwen.*) -> tools/LLM recognized correctly, usage summed."""
    spans = [
        _oj_span("tool.retrieve_report", {
            "gen_ai.tool.name": "retrieve_report", "gen_ai.operation.name": "execute_tool",
            "openjiuwen.status": "finish"}),
        _oj_span("llm.ModelClient", {
            "gen_ai.request.model": "GLM5.3-Flash",
            "gen_ai.usage.prompt_tokens": 100, "gen_ai.usage.completion_tokens": 50}),
        _oj_span("tool.export_data", {
            "gen_ai.tool.name": "export_data", "openjiuwen.status": "error",
            "openjiuwen.error": '{"message": "forbidden"}'}),
        _oj_span("prompt.default", {"openjiuwen.status": "finish"}),  # other internal spans → step
    ]
    t = normalize(spans)
    assert t.model == "GLM5.3-Flash"
    assert t.usage_tokens == 150
    assert t.executed_tools == ["retrieve_report", "export_data"]
    assert t.denied_tools == []
    assert [s.kind for s in t.steps] == ["tool", "llm", "tool", "step"]
    assert t.agent_id == "jiuwen-sample-agent"
    assert t.trace_id == "a" * 32


def test_finruntime_private_convention_kept():
    """fin-runtime private convention regression: agent.run root + llm.call + tool.execute (denied)."""
    spans = [
        {"trace_id": "c" * 32, "name": "agent.run", "start_unix_nano": 1,
         "end_unix_nano": 2, "attributes": {"agent.id": "fin-runtime", "model": "m1",
                                            "trace.id": "c" * 32, "source": "eval"}},
        {"trace_id": "c" * 32, "name": "llm.call", "start_unix_nano": 2,
         "end_unix_nano": 3, "attributes": {"llm.model": "m1", "llm.usage.total_tokens": 200}},
        {"trace_id": "c" * 32, "name": "tool.execute", "start_unix_nano": 3,
         "end_unix_nano": 4, "attributes": {"tool.name": "export_data", "tool.status": "denied"}},
    ]
    t = normalize(spans)
    assert t.model == "m1"
    assert t.usage_tokens == 200
    assert t.executed_tools == []
    assert t.denied_tools == ["export_data"]
    assert t.agent_id == "fin-runtime"
