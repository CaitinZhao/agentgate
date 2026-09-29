"""Web platform W1 tests: four-role auth matrix, bank storage + overrides, run execution
through the serial worker (offline MockTarget), cancellation, and the legacy migration.

Services (resident receiver/proxy) stay off; the worker is driven manually in-process.
"""
import time
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from agentgate.webapp import worker as worker_mod
from agentgate.webapp.app import create_app

OWNER_PW = "owner-pass-1"


@contextmanager
def platform(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGATE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OWNER_PASSWORD", OWNER_PW)
    app = create_app(start_services=False)
    with TestClient(app) as client:
        yield client


def _login(client, username, password):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def _seed_users(client):
    """owner -> member m1 -> promote admin -> admin creates member m2; also viewer v1."""
    oh = _login(client, "owner", OWNER_PW)
    r = client.post("/api/v1/auth/register", json={"username": "v1", "password": "viewer1"})
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/admin/users", headers=oh,
                    json={"username": "m1", "password": "member1", "role": "member"})
    assert r.status_code == 200, r.text
    r = client.patch("/api/v1/admin/users/m1", headers=oh, json={"role": "admin"})
    assert r.status_code == 200, r.text
    ah = _login(client, "m1", "member1")
    r = client.post("/api/v1/admin/users", headers=ah,
                    json={"username": "m2", "password": "member2", "role": "member"})
    assert r.status_code == 200, r.text
    m2h = _login(client, "m2", "member2")
    vh = _login(client, "v1", "viewer1")
    return {"owner": oh, "admin": ah, "member": m2h, "viewer": vh}


CASE_SPECS = [
    {"case_id": "demo-pass", "query": "say: fine", "type": "extractive",
     "gold": {"final": {"value": "fine"}}},
    {"case_id": "demo-pending", "query": "free text", "type": "free_text", "gold": {}},
    {"case_id": "demo-fail", "query": "numeric", "type": "numeric",
     "gold": {"final": {"value": 42, "unit": "%", "tol_rel": 0.01}}},
]


def _seed_bank(client, headers, name="demo"):
    r = client.post("/api/v1/benchmarks", headers=headers, json={
        "name": name, "display_name": "Demo", "category": "finance", "visibility": "public"})
    assert r.status_code == 200, r.text
    for s in CASE_SPECS:
        r = client.post("/api/v1/benchmarks/%s/cases" % name, headers=headers, json=s)
        assert r.status_code == 200, r.text


def test_auth_and_role_matrix(tmp_path, monkeypatch):
    with platform(tmp_path, monkeypatch) as client:
        oh = _login(client, "owner", OWNER_PW)
        assert client.get("/api/v1/auth/me", headers=oh).json()["role"] == "owner"
        # owner has no web password-change path (deploy-machine CLI only)
        r = client.patch("/api/v1/auth/password", headers=oh,
                         json={"old_password": "x", "new_password": "yyyyyy"})
        assert r.status_code == 403
        users = _seed_users(client)
        # member changes own password (old password required)
        r = client.patch("/api/v1/auth/password", headers=users["member"],
                         json={"old_password": "member2", "new_password": "member2x"})
        assert r.status_code == 200
        r = client.post("/api/v1/auth/login",
                        json={"username": "m2", "password": "member2x"})
        assert r.status_code == 200
        # wrong old password rejected
        r = client.patch("/api/v1/auth/password", headers=users["member"],
                         json={"old_password": "nope", "new_password": "zzzzzz"})
        assert r.status_code == 403
        # viewer: bank list ok, cases/runs/admin forbidden
        assert client.get("/api/v1/benchmarks", headers=users["viewer"]).status_code == 200
        assert client.get("/api/v1/benchmarks/demo/cases", headers=users["viewer"]).status_code == 403
        assert client.get("/api/v1/runs", headers=users["viewer"]).status_code == 403
        assert client.get("/api/v1/admin/users", headers=users["viewer"]).status_code == 403
        # member: cannot create public banks, cannot manage users
        r = client.post("/api/v1/benchmarks", headers=users["member"],
                        json={"name": "nope", "visibility": "public"})
        assert r.status_code == 403
        assert client.get("/api/v1/admin/users", headers=users["member"]).status_code == 403
        # admin cannot touch the owner account
        assert client.delete("/api/v1/admin/users/owner", headers=users["admin"]).status_code == 400
        assert client.patch("/api/v1/admin/users/owner", headers=users["admin"],
                            json={"new_password": "hacked1"}).status_code == 400
        # admin cannot promote (owner only)
        assert client.patch("/api/v1/admin/users/m2", headers=users["admin"],
                            json={"role": "admin"}).status_code == 403


def test_banks_and_overrides(tmp_path, monkeypatch):
    with platform(tmp_path, monkeypatch) as client:
        users = _seed_users(client)
        _seed_bank(client, users["admin"])
        # member configures "my way of running" on the public bank
        r = client.put("/api/v1/benchmarks/demo/my-overrides", headers=users["member"],
                       json={"overrides": [{"case_id": "demo-pass", "enabled": False},
                                            {"case_id": "demo-pending", "level": "L1"}]})
        assert r.status_code == 200, r.text
        r = client.get("/api/v1/benchmarks/demo/cases", headers=users["member"])
        rows = {c["case_id"]: c for c in r.json()["cases"]}
        assert rows["demo-pass"]["my_enabled"] == 0
        assert rows["demo-pending"]["my_level"] == "L1"
        assert rows["demo-pass"]["my_level"] is None
        # overrides are per user: the admin sees none
        r = client.get("/api/v1/benchmarks/demo/cases", headers=users["admin"])
        assert all(c["my_enabled"] == 1 for c in r.json()["cases"])
        # one-click restore to defaults
        r = client.delete("/api/v1/benchmarks/demo/my-overrides", headers=users["member"])
        assert r.json()["cleared"] == 2
        # offline public bank: hidden from member, visible to admin
        r = client.patch("/api/v1/benchmarks/demo", headers=users["admin"], json={"status": "offline"})
        assert r.status_code == 200
        names = [b["name"] for b in
                 client.get("/api/v1/benchmarks", headers=users["member"]).json()["benchmarks"]]
        assert "demo" not in names
        names = [b["name"] for b in
                 client.get("/api/v1/benchmarks", headers=users["admin"]).json()["benchmarks"]]
        assert "demo" in names
        # case level/status edit (admin) and private-bank lifecycle (member)
        r = client.patch("/api/v1/benchmarks/demo/cases/demo-fail",
                         headers=users["admin"], json={"level": "L0", "status": "retired"})
        assert r.status_code == 200
        client.patch("/api/v1/benchmarks/demo", headers=users["admin"], json={"status": "online"})
        r = client.post("/api/v1/benchmarks", headers=users["member"],
                        json={"name": "mybank", "visibility": "private"})
        assert r.status_code == 200, r.text
        # another member cannot even see it
        names = [b["name"] for b in
                 client.get("/api/v1/benchmarks", headers=users["viewer"]).json()["benchmarks"]]
        assert "mybank" not in names


def test_run_execution_end_to_end(tmp_path, monkeypatch):
    with platform(tmp_path, monkeypatch) as client:
        users = _seed_users(client)
        _seed_bank(client, users["admin"])
        from agentgate.run.targets.base import MockTarget
        responses = {
            "demo-pass": {"answer_text": "fine", "final_json": {},
                          "trace_id": "", "usage_total": 3, "audit": []},
            "demo-pending": {"answer_text": "some free text", "final_json": {},
                             "trace_id": "", "usage_total": 5, "audit": []},
            "demo-fail": {"answer_text": "wrong number", "final_json": {"value": 99},
                          "trace_id": "", "usage_total": 7, "audit": []},
        }
        monkeypatch.setattr(worker_mod, "make_target",
                            lambda url, llm_base_url="": MockTarget(
                                responses=responses, spans={k: [] for k in responses}))
        w = worker_mod.Worker(poll_seconds=0.1,
                              proxy_sink=str(tmp_path / "sink.jsonl"))
        w.start()
        try:
            r = client.post("/api/v1/runs", headers=users["admin"], json={
                "task_name": "周五回归", "banks": [{"bank": "demo", "levels": []}],
                "target_url": "http://mock:9999"})
            assert r.status_code == 200, r.text
            rid = r.json()["id"]
            assert r.json()["name"].startswith("周五回归-")     # unique name = input + suffix
            assert r.json()["total_cases"] == 3
            view = _wait_finished(client, rid, users["admin"])
            assert view["status"] == "succeeded", view
            items = {i["case_id"]: i for i in view["items"]}
            assert items["demo-pass"]["verdict"] == "PASS"
            assert items["demo-pending"]["verdict"] == "PENDING"
            assert items["demo-fail"]["verdict"] == "FAIL"
            assert view["done_cases"] == 3
            assert view["gate_decision"] == "FAIL"
            # bilingual reports + artifacts archived and served
            assert client.get("/api/v1/runs/%s/report" % rid,
                              headers=users["admin"]).status_code == 200
            r = client.get("/api/v1/runs/%s/report?lang=en" % rid, headers=users["admin"])
            assert r.status_code == 200 and len(r.text) > 50
            assert client.get("/api/v1/runs/%s/artifacts/eval_results.json" % rid,
                              headers=users["admin"]).status_code == 200
            # six-dimension scores (doc 35 C1): hexagon + per-case + cards; report carries them
            sc = client.get("/api/v1/runs/%s/scores" % rid, headers=users["admin"])
            assert sc.status_code == 200, sc.text
            scores = sc.json()
            assert scores["run_scores"]["success"] == pytest.approx(50.0)   # 1 PASS / 1 FAIL
            assert scores["run_scores"]["safety"] == 100.0
            assert scores["run_scores"]["cost"] is not None                 # usage_total recorded
            assert scores["counts"] == {"pass": 1, "fail": 1, "pending": 1,
                                        "skipped": 0, "total": 3}
            assert set(scores["cards"]) >= {"success", "quality", "reliability", "safety"}
            assert "六维概览" in client.get("/api/v1/runs/%s/report" % rid,
                                            headers=users["admin"]).text
            # bank trend (C6): this run shows up with its hexagon
            tr = client.get("/api/v1/benchmarks/demo/trend", headers=users["admin"])
            assert tr.status_code == 200 and len(tr.json()["trend"]) == 1
            # traversal and non-whitelisted artifacts rejected (httpx normalizes ".."
            # before routing, so the block may surface as 404 or as the handler's 400)
            sc = client.get("/api/v1/runs/%s/artifacts/../agentgate.db" % rid,
                            headers=users["admin"]).status_code
            assert sc in (400, 404) and sc != 200
            # members see only their own runs (list and detail)
            assert client.get("/api/v1/runs", headers=users["member"]).json()["runs"] == []
            assert client.get("/api/v1/runs/%s" % rid,
                              headers=users["member"]).status_code == 404
            r = client.get("/api/v1/runs", headers=users["admin"], params={"name": "周五"})
            assert len(r.json()["runs"]) == 1
            # level filter narrows the case set: retire one case first, then run L2-only
            r = client.patch("/api/v1/benchmarks/demo/cases/demo-pass",
                             headers=users["admin"], json={"status": "retired"})
            assert r.status_code == 200
            r = client.post("/api/v1/runs", headers=users["admin"], json={
                "task_name": "L2-only", "banks": [{"bank": "demo", "levels": ["L2"]}],
                "target_url": "http://mock:9999"})
            assert r.status_code == 200 and r.json()["total_cases"] == 2
        finally:
            w.stop()


def _wait_finished(client, rid, headers, timeout_s=30):
    deadline = time.time() + timeout_s
    view = None
    while time.time() < deadline:
        view = client.get("/api/v1/runs/%s" % rid, headers=headers).json()
        if view.get("status") in ("succeeded", "failed", "cancelled"):
            return view
        time.sleep(0.2)
    raise AssertionError("run did not finish in time: %s" % view)


def test_cancel_queued_run(tmp_path, monkeypatch):
    with platform(tmp_path, monkeypatch) as client:
        users = _seed_users(client)
        _seed_bank(client, users["admin"])
        r = client.post("/api/v1/runs", headers=users["admin"], json={
            "task_name": "tocancel", "banks": [{"bank": "demo"}],
            "target_url": "http://mock:9999"})
        rid = r.json()["id"]
        r = client.delete("/api/v1/runs/%s" % rid, headers=users["admin"])
        assert r.status_code == 200
        assert client.get("/api/v1/runs/%s" % rid,
                          headers=users["admin"]).json()["status"] == "cancelled"
        # member cannot cancel someone else's run
        r = client.post("/api/v1/runs", headers=users["admin"], json={
            "task_name": "keep", "banks": [{"bank": "demo"}],
            "target_url": "http://mock:9999"})
        rid2 = r.json()["id"]
        assert client.delete("/api/v1/runs/%s" % rid2,
                             headers=users["member"]).status_code == 404


def test_settings_owner_only(tmp_path, monkeypatch):
    with platform(tmp_path, monkeypatch) as client:
        users = _seed_users(client)
        assert client.get("/api/v1/admin/settings", headers=users["admin"]).status_code == 403
        r = client.put("/api/v1/admin/settings", headers=users["owner"],
                       json={"proxy_upstream": "https://gw.example.com/v1",
                             "report_retention_days": 15, "registration_open": False})
        assert r.status_code == 200, r.text
        assert r.json()["proxy_upstream"].endswith("/v1")
        from agentgate.trace import llm_proxy
        assert llm_proxy.STATE["upstream"] == "https://gw.example.com/v1"   # applied live
        assert client.post("/api/v1/auth/register",
                           json={"username": "late", "password": "latereg"})
        assert client.post("/api/v1/auth/register",
                           json={"username": "late2", "password": "latereg"}).status_code == 403


def test_requirement_skip_and_tool_strict(tmp_path, monkeypatch):
    """Fairness contract: a bank requiring an unsupported profile gets its cases SKIPPED
    (never FAIL); tool_strict=false drops required_tools from judging."""
    from agentgate.run.targets.base import MockTarget
    with platform(tmp_path, monkeypatch) as client:
        users = _seed_users(client)
        _seed_bank(client, users["admin"])
        # declare requirements: profile "fb" (the mock agent below does not support it)
        r = client.patch("/api/v1/benchmarks/demo", headers=users["admin"],
                         json={"requirements": {"profile": "fb", "tool_strict": True,
                                                "materials": "test corpus"}})
        assert r.status_code == 200 and r.json()["requirements"]["profile"] == "fb"
        # capabilities probe says the agent only supports "bank"
        import agentgate.webapp.worker as w
        monkeypatch.setattr(w, "agent_capabilities",
                            lambda url, timeout=5.0: {"profiles": ["bank"]})
        monkeypatch.setattr(w, "make_target",
                            lambda url, llm_base_url="": MockTarget(responses={}, spans={}))
        worker = w.Worker(poll_seconds=0.1)
        worker.start()
        try:
            r = client.post("/api/v1/runs", headers=users["admin"], json={
                "task_name": "skip-check", "banks": [{"bank": "demo"}],
                "target_url": "http://mock:1"})
            rid = r.json()["id"]
            view = _wait_finished(client, rid, users["admin"])
            # nothing runnable -> run finishes immediately with SKIPPED gate and skip items
            assert view["status"] == "succeeded" and view["gate_decision"] == "SKIPPED(3)"
            assert all(i["verdict"] == "SKIPPED" for i in view["items"])
            # no capabilities exposed -> permissive, cases run as usual
            monkeypatch.setattr(w, "agent_capabilities", lambda url, timeout=5.0: None)
            responses = {c["case_id"]: {"answer_text": "fine", "final_json": {},
                                        "trace_id": "", "usage_total": 1, "audit": []}
                         for c in CASE_SPECS}
            monkeypatch.setattr(w, "make_target",
                                lambda url, llm_base_url="": MockTarget(
                                    responses=responses, spans={k: [] for k in responses}))
            r = client.post("/api/v1/runs", headers=users["admin"], json={
                "task_name": "permissive", "banks": [{"bank": "demo"}],
                "target_url": "http://mock:1"})
            view = _wait_finished(client, r.json()["id"], users["admin"])
            assert view["status"] == "succeeded" and view["done_cases"] == 3
            # tool_strict=false: required_tools dropped -> outcome-only judging
            r = client.patch("/api/v1/benchmarks/demo", headers=users["admin"],
                             json={"requirements": {"profile": "", "tool_strict": False}})
            assert r.status_code == 200
        finally:
            worker.stop()

