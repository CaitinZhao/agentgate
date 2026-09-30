"""Tests for the external-agent integration aids: response-audit -> trace fallback,
the contract probe, and the engine's trace-wait knob."""
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import pytest

from agentgate.trace.models import NormalizedTrace, TraceStep
from agentgate.trace.normalizer import merge_response_audit, normalize
from agentgate.control.probe import probe_agent
from agentgate.run.engine import RunEngine


# ---------- merge_response_audit ----------

def _empty_trace():
    return NormalizedTrace(trace_id="t", steps=[], tool_calls=[], executed_tools=[],
                           denied_tools=[], usage_tokens=0)


def test_audit_entries_become_tool_steps():
    resp = {"audit": [
        {"tool": "retrieve_report", "status": "ok", "summary": "2 hits"},
        {"tool": "export_data", "status": "denied"},
        "bare_tool",
    ], "usage_total": 1234}
    trace = merge_response_audit(_empty_trace(), resp)
    assert trace.tool_calls == ["retrieve_report", "export_data", "bare_tool"]
    assert trace.executed_tools == ["retrieve_report", "bare_tool"]
    assert trace.denied_tools == ["export_data"]
    kinds = [s.kind for s in trace.steps]
    assert kinds == ["tool", "tool", "tool"]
    assert trace.steps[0].attrs.get("audit.summary") == "2 hits"
    assert trace.usage_tokens == 1234


def test_audit_is_fallback_only():
    """OTel-derived tool steps win; audit must not double count."""
    spans = [{"name": "tool.execute", "attributes": {"tool.name": "span_tool"},
              "start_unix_nano": 1}]
    trace = merge_response_audit(normalize(spans),
                                 {"audit": [{"tool": "audit_tool"}], "usage_total": 5})
    assert trace.tool_calls == ["span_tool"]
    assert trace.usage_tokens == 5          # usage fallback still applies


def test_audit_bad_entries_ignored():
    trace = merge_response_audit(_empty_trace(), {"audit": [None, 3, {"other": "x"}, ""]})
    assert trace.tool_calls == []
    assert trace.steps == []


def test_audit_accepts_steps_alias_and_bad_types():
    trace = merge_response_audit(_empty_trace(), {"steps": {"tool": "not_a_list"}})
    assert trace.tool_calls == []
    trace = merge_response_audit(_empty_trace(), {"steps": [{"tool": "s"}]})
    assert trace.tool_calls == ["s"]
    trace = merge_response_audit(_empty_trace(), {"usage_total": "not-a-number"})
    assert trace.usage_tokens == 0


# ---------- probe_agent ----------

class _StubAgent(BaseHTTPRequestHandler):
    """Configurable contract stub: serve shape=good|warn|bad."""
    shape = "good"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"status": "ok"})
        if self.path == "/capabilities":
            if _StubAgent.shape == "nocaps":
                return self._send(404, {"detail": "not found"})
            return self._send(200, {"agent": "stub", "profiles": ["base"],
                                    "traces": _StubAgent.shape == "good"})
        self._send(404, {})

    def do_POST(self):
        assert self.path == "/invoke"
        n = int(self.headers.get("Content-Length") or 0)
        json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        if _StubAgent.shape == "bad":
            return self._send(200, {"answer_text": 42, "final_json": [], "trace_id": "",
                                    "usage_total": "many", "audit": "none"})
        if _StubAgent.shape == "warn":
            return self._send(200, {"answer_text": "pong"})
        return self._send(200, {"answer_text": "pong", "final_json": {"answer": "pong"},
                                "trace_id": "abc", "usage_total": 42,
                                "audit": [{"tool": "echo", "status": "ok"}]})


@pytest.fixture()
def stub_agent():
    srv = HTTPServer(("127.0.0.1", 0), _StubAgent)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


def test_probe_good_contract(stub_agent):
    _StubAgent.shape = "good"
    report = probe_agent(stub_agent)
    assert report["ok"] is True
    names = {c["name"]: c["status"] for c in report["checks"]}
    assert names["invoke"] == "pass"
    assert names["answer_text"] == "pass"
    assert names["final_json"] == "pass"
    assert names["audit"] == "pass"


def test_probe_warn_contract(stub_agent):
    _StubAgent.shape = "warn"
    report = probe_agent(stub_agent)
    assert report["ok"] is True                      # warns don't fail the probe
    names = {c["name"]: c["status"] for c in report["checks"]}
    assert names["final_json"] == "warn"
    assert names["audit"] == "warn"
    assert names["usage_total"] == "warn"


def test_probe_bad_contract(stub_agent):
    _StubAgent.shape = "bad"
    report = probe_agent(stub_agent)
    assert report["ok"] is False
    names = {c["name"]: c["status"] for c in report["checks"]}
    assert names["answer_text"] == "fail"
    assert names["final_json"] == "fail"


def test_probe_nocaps_contract(stub_agent):
    _StubAgent.shape = "nocaps"
    report = probe_agent(stub_agent)
    names = {c["name"]: c["status"] for c in report["checks"]}
    assert names["capabilities"] == "warn"
    assert report["ok"] is True


def test_probe_unreachable():
    report = probe_agent("http://127.0.0.1:1", timeout=2)
    assert report["ok"] is False
    assert any(c["name"] == "invoke" and c["status"] == "fail" for c in report["checks"])


# ---------- engine integration ----------

def test_engine_merges_audit_into_trace():
    class FakeTarget:
        def prepare(self, case_id):
            pass

        def invoke(self, cinput):
            return {"answer_text": "done", "final_json": {"answer": "done"},
                    "trace_id": "no-otel", "usage_total": 7,
                    "audit": [{"tool": "my_tool", "status": "ok"}]}

    from agentgate.case.models import Case
    case = Case(case_id="c1", suite="s", type="free_text",
                input={"query": "q"}, gold={})
    run = RunEngine(FakeTarget(), receiver=None).run_case(case)
    assert run.trace.tool_calls == ["my_tool"]
    assert run.trace.usage_tokens == 7
