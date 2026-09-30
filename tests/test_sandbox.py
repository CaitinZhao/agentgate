"""P1 sandbox tests: providers, final-state verification, judging integration,
the exec HTTP API, and the case schema end-to-end through run_case_set."""
import shutil
from typing import Dict

import pytest

from agentgate.case.loader import load_cases
from agentgate.control.service import run_case_set
from agentgate.evaluator import judging
from agentgate.evaluator.runner import evaluate_case
from agentgate.run.engine import RunEngine
from agentgate.run.targets.base import BaseTarget
from agentgate.trace.normalizer import normalize
from agentgate.sandbox import registry as sb_registry
from agentgate.sandbox.base import looks_like_env_assertion, sandbox_spec
from agentgate.sandbox.docker_impl import DockerSandboxProvider
from agentgate.sandbox.fake import FakeSandboxProvider
from agentgate.sandbox.provider import from_config
from agentgate.sandbox.subprocess_impl import SubprocessSandboxProvider

SPEC = {
    "image": "python:3.11-slim",
    "setup": [{"cmd": "mkdir -p /app/out"},
              {"write_file": {"path": "/app/in/task.txt", "content": "hello"}}],
}
ASSERTIONS = [
    {"read_file": "/app/out/report.json", "json_path": "status", "equals": "done"},
    {"exec": "test -f /app/out/task.bak", "exit_code": 0},
]


E2E_SPEC = {
    "image": "python:3.11-slim",
    "setup": [{"cmd": "mkdir -p in out"},
              {"write_file": {"path": "in/task.txt", "content": "hello"}}],
}
E2E_ASSERTIONS = [
    {"read_file": "out/report.json", "json_path": "status", "equals": "done"},
    {"exec": "test -f out/task.bak", "exit_code": 0},
]


def _state_case(assertions=None, sandbox_spec=SPEC):
    from agentgate.case.models import Case, Gold
    final = {"assertions": ASSERTIONS if assertions is None else assertions}
    if sandbox_spec is not None:
        final["sandbox"] = sandbox_spec
    return Case(case_id="sb-1", suite="sandbox-demo", type="state",
                input={"query": "do it"}, gold=Gold(final=final))


def _response(final=None):
    return {"answer_text": "done", "final_json": final or {}, "trace_id": "t",
            "usage_total": 0, "audit": []}


# ---------- provider basics ----------

def test_subprocess_provider_roundtrip():
    p = SubprocessSandboxProvider()
    h = p.create({"setup": [{"write_file": {"path": "in/task.txt", "content": "hello"}}]})
    try:
        out = h.exec("cat in/task.txt")
        assert out["exit_code"] == 0 and out["stdout"].strip() == "hello"
        h.write_file("out/report.json", '{"status": "done"}')
        assert '"status": "done"' in h.read_file("out/report.json")
    finally:
        p.close(h)


def test_fake_provider_scripts_exec():
    p = FakeSandboxProvider(exec_results=[{"exit_code": 1, "stdout": "", "stderr": "boom"}])
    h = p.create(SPEC)
    out = h.exec("whatever")
    assert out["exit_code"] == 1 and "boom" in out["stderr"]
    p.close(h)
    assert h.closed


def test_from_config():
    assert from_config(None) is None
    assert from_config({"provider": "off"}) is None
    assert from_config({"provider": "docker"}).name == "docker"
    assert from_config({"provider": "subprocess"}).name == "subprocess"
    with pytest.raises(ValueError):
        from_config({"provider": "teleport"})


def test_docker_provider_available_or_skipped():
    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    p = DockerSandboxProvider()
    try:
        h = p.create(SPEC)
    except RuntimeError as e:
        if "failed to connect" in str(e) or "Cannot connect" in str(e):
            pytest.skip("docker daemon not running")
        raise
    try:
        out = h.exec("echo ok")
        assert out["exit_code"] == 0 and "ok" in out["stdout"]
        h.write_file("/tmp/probe.txt", "ping")
        assert h.read_file("/tmp/probe.txt") == "ping"
    finally:
        p.close(h)


# ---------- verify + judging integration ----------

def _fake_after_agent():
    """The sandbox as the judge should see it after a correct agent."""
    return FakeSandboxProvider(files={
        "/app/out/report.json": '{"status": "done", "task": "monthly"}',
        "/app/out/task.bak": "hello",
    }).create(SPEC)


def test_state_case_passes_when_final_state_matches():
    case = _state_case()
    out = judging.judge_case(case, _response(), trace=normalize([]), pack={},
                             sandbox=_fake_after_agent())
    assert out["verdict"] == "PASS"


def test_state_case_fails_when_final_state_differs():
    case = _state_case()
    sb = FakeSandboxProvider(files={
        "/app/out/report.json": '{"status": "partial"}',
        "/app/out/task.bak": "hello",
    }).create(SPEC)
    out = judging.judge_case(case, _response(), trace=normalize([]), pack={}, sandbox=sb)
    assert out["verdict"] == "FAIL"
    missed = [c for c in out["checks"] if not c["ok"]]
    assert missed and "state assertion failed" in missed[0]["reason"]


def test_state_case_env_assertions_without_sandbox_stay_pending():
    """No provider configured -> env assertions cannot be judged -> honest PENDING."""
    case = _state_case()
    out = judging.judge_case(case, _response(), trace=normalize([]), pack={}, sandbox=None)
    assert out["verdict"] == "PENDING"


def test_state_case_legacy_path_assertions_still_work():
    """Assertions without read_file/exec keep their final_json path-walk meaning."""
    case = _state_case(assertions=[{"path": "answer", "equals": 42}], sandbox_spec=SPEC)
    out = judging.judge_case(case, _response({"answer": 42}), trace=normalize([]), pack={}, sandbox=None)
    assert out["verdict"] == "PASS"


def test_spec_helpers():
    case = _state_case()
    assert sandbox_spec(case) == SPEC
    assert len([a for a in case.gold.final["assertions"] if looks_like_env_assertion(a)]) == 2


# ---------- exec HTTP API ----------

def test_exec_api_token_gating():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agentgate.webapp.routes import sandbox_routes

    app = FastAPI()
    app.include_router(sandbox_routes.router)
    client = TestClient(app)

    r = client.post("/api/v1/sandbox/exec", json={"token": "nope", "cmd": "echo hi"})
    assert r.status_code == 404

    token = sb_registry.register(_fake_after_agent())
    try:
        r = client.post("/api/v1/sandbox/exec", json={"token": token, "cmd": "cat /app/out/report.json"})
        assert r.status_code == 200 and r.json()["exit_code"] == 0
        r = client.post("/api/v1/sandbox/exec", json={"token": token, "cmd": ""})
        assert r.status_code == 400
    finally:
        sb_registry.unregister(token)
    assert client.post("/api/v1/sandbox/exec",
                       json={"token": token, "cmd": "echo"}).status_code == 404


# ---------- end-to-end through run_case_set ----------

class _SandboxAgentTarget(BaseTarget):
    """A target agent that uses the exec endpoint (as a real external agent would)."""
    name = "sandbox-agent"

    def __init__(self):
        self.seen_context = ""
        self.debug = []

    def invoke(self, cinput: dict) -> Dict:
        import json as _json
        import re as _re
        import urllib.request
        self.seen_context = str(cinput.get("context") or "")
        m = _re.search(r"POST (\S+/api/v1/sandbox/exec).*?\"token\": \"(\w+)\"",
                       self.seen_context, _re.S)
        if not m:
            return {"answer_text": "no sandbox info", "final_json": {},
                    "trace_id": "t", "usage_total": 0, "audit": []}
        url, token = m.group(1), m.group(2)

        def call(cmd):
            req = urllib.request.Request(url, data=_json.dumps({"token": token, "cmd": cmd}).encode(),
                                         headers={"Content-Type": "application/json"})
            out = _json.loads(urllib.request.urlopen(req, timeout=15).read())
            self.debug.append((cmd, out))
            return out

        assert call("cat in/task.txt")["stdout"].strip() == "hello"
        call("mkdir -p out")
        call("cat > out/report.json <<'EOF'\n{\"status\": \"done\"}\nEOF")
        call("cp in/task.txt out/task.bak")
        return {"answer_text": "done", "final_json": {"answer": "done"},
                "trace_id": "t", "usage_total": 3, "audit": [{"tool": "exec", "status": "ok"}]}


def test_run_case_set_sandbox_end_to_end(tmp_path):
    """Real HTTP path: a uvicorn thread serves the exec API; the agent answers via it."""
    import threading
    import time as _time

    import uvicorn
    from fastapi import FastAPI
    from agentgate.webapp.routes import sandbox_routes

    app = FastAPI()
    app.include_router(sandbox_routes.router)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0,
                                           log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(50):
        if server.started:
            break
        _time.sleep(0.1)
    port = server.servers[0].sockets[0].getsockname()[1]

    try:
        provider = SubprocessSandboxProvider()
        case = _state_case(assertions=E2E_ASSERTIONS, sandbox_spec=E2E_SPEC)
        target = _SandboxAgentTarget()
        summary = run_case_set(None, target, str(tmp_path / "out"), receiver=None,
                               cases_list=[case], sandbox_provider=provider,
                               sandbox_exec_url="http://127.0.0.1:%d" % port)
        results = summary["results"]
        assert results and results[0]["scores"]["deterministic_pass"] is True
        assert "POST" in target.seen_context and "sandbox/exec" in target.seen_context
    finally:
        server.should_exit = True
        th.join(timeout=5)
    # tokens from the closed sandbox must not resolve anymore
    assert sb_registry.resolve("anything") is None


class _StubTarget(BaseTarget):
    name = "stub"

    def invoke(self, cinput: dict) -> Dict:
        return {"answer_text": "done", "final_json": {}, "trace_id": "t",
                "usage_total": 0, "audit": []}


def test_run_case_set_without_provider_keeps_pending(tmp_path):
    case = _state_case()
    summary = run_case_set(None, _StubTarget(), str(tmp_path / "out"), receiver=None,
                           cases_list=[case], sandbox_provider=None)
    assert summary["results"][0]["scores"]["human_review"] == "pending"
