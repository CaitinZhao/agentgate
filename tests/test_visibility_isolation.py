"""User-perspective isolation tests: bank visibility (public vs private), per-user
edit drafts and run overrides, per-user AI config, and run ownership."""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from agentgate.webapp.app import create_app


@pytest.fixture(scope="module")
def client():
    os.environ["AGENTGATE_DATA_DIR"] = tempfile.mkdtemp()
    os.environ["OWNER_PASSWORD"] = "test-owner-1"
    app = create_app(start_services=False)
    with TestClient(app) as c:
        yield c


def _login(c, username, password):
    return c.post("/api/v1/auth/login",
                  json={"username": username, "password": password}).json()["token"]


@pytest.fixture(scope="module")
def users(client):
    H = lambda tok: {"Authorization": "Bearer " + tok}
    owner_tok = _login(client, "owner", "test-owner-1")
    # owner imports two members (self-registration only creates viewers)
    for name, role in (("alice", "member"), ("bob", "member"), ("carol", "admin")):
        r = client.post("/api/v1/admin/users", headers=H(owner_tok),
                        json={"username": name, "password": "pw-123456",
                              "role": role, "display_name": name})
        assert r.status_code == 200, r.text
    return {"owner": H(owner_tok),
            "alice": H(_login(client, "alice", "pw-123456")),
            "bob": H(_login(client, "bob", "pw-123456")),
            "carol": H(_login(client, "carol", "pw-123456"))}


def _names(client, H):
    r = client.get("/api/v1/benchmarks", headers=H).json()
    return {b["name"] for b in r["benchmarks"]}


@pytest.fixture(scope="module")
def banks(client, users):
    # alice: one private bank with a case; owner: one public bank
    assert client.post("/api/v1/benchmarks", headers=users["alice"],
                       json={"name": "priv-alice", "visibility": "private",
                             "display_name": "Alice 私有库",
                             "default_level": "L0"}).status_code == 200
    case = {"case_id": "p-1", "query": "1+1=?", "type": "numeric",
            "gold": {"final": {"value": 2}}}
    assert client.post("/api/v1/benchmarks/priv-alice/cases", headers=users["alice"],
                       json=case).status_code == 200
    assert client.post("/api/v1/benchmarks", headers=users["owner"],
                       json={"name": "pub-shared", "visibility": "public",
                             "display_name": "公共库", "category": "self-built",
                             "default_level": "L0"}).status_code == 200
    assert client.post("/api/v1/benchmarks/pub-shared/cases", headers=users["owner"],
                       json={**case, "case_id": "pub-1"}).status_code == 200


# ---------- bank visibility ----------

def test_private_bank_visible_only_to_owner_and_admin(client, users, banks):
    names_a, names_b, names_o = (_names(client, users[k]) for k in ("alice", "bob", "owner"))
    assert "priv-alice" in names_a and "priv-alice" in names_o
    assert "priv-alice" not in names_b
    assert "pub-shared" in names_a and "pub-shared" in names_b


def test_private_bank_hidden_from_others_at_every_endpoint(client, users, banks):
    for path in ("/api/v1/benchmarks/priv-alice",
                 "/api/v1/benchmarks/priv-alice/cases",
                 "/api/v1/benchmarks/priv-alice/overview",
                 "/api/v1/benchmarks/priv-alice/edit-draft",
                 "/api/v1/benchmarks/priv-alice/my-overrides"):
        r = client.get(path, headers=users["bob"])
        assert r.status_code == 404, (path, r.status_code)
    # writing is equally hidden
    r = client.patch("/api/v1/benchmarks/priv-alice", headers=users["bob"],
                     json={"display_name": "hijack"})
    assert r.status_code == 404
    r = client.post("/api/v1/benchmarks/priv-alice/cases", headers=users["bob"],
                    json={"case_id": "x", "query": "x"})
    assert r.status_code == 404


def test_public_bank_visible_to_everyone(client, users, banks):
    for k in ("alice", "bob", "owner"):
        r = client.get("/api/v1/benchmarks/pub-shared/cases", headers=users[k])
        assert r.status_code == 200 and len(r.json()["cases"]) == 1


# ---------- per-user isolation on a shared public bank ----------

def test_edit_drafts_are_per_user(client, users, banks):
    # members cannot manage a public bank at all (correct deny)
    r = client.put("/api/v1/benchmarks/pub-shared/edit-draft", headers=users["alice"],
                   json={"draft": {"marker": "alice-draft"}})
    assert r.status_code == 403
    # owner and admin both manage the public bank; their drafts are independent
    client.put("/api/v1/benchmarks/pub-shared/edit-draft", headers=users["owner"],
               json={"draft": {"marker": "owner-draft"}})
    draft_c = client.get("/api/v1/benchmarks/pub-shared/edit-draft",
                         headers=users["carol"]).json()["draft"]
    assert not draft_c, "carol must not see the owner's draft"
    client.put("/api/v1/benchmarks/pub-shared/edit-draft", headers=users["carol"],
               json={"draft": {"marker": "carol-draft"}})
    draft_o = client.get("/api/v1/benchmarks/pub-shared/edit-draft",
                         headers=users["owner"]).json()["draft"]
    assert draft_o.get("marker") == "owner-draft"       # owner's draft untouched


def test_run_overrides_are_per_user(client, users, banks):
    body = {"overrides": [{"case_id": "pub-1", "enabled": True, "level": "L1"}]}
    r = client.put("/api/v1/benchmarks/pub-shared/my-overrides", headers=users["alice"],
                   json=body)
    assert r.status_code == 200, r.text
    ov_b = client.get("/api/v1/benchmarks/pub-shared/my-overrides",
                      headers=users["bob"]).json()
    assert not (ov_b.get("overrides") or []), "bob must not see alice's overrides"


def test_ai_settings_are_per_user(client, users):
    r = client.put("/api/v1/auth/me/ai-settings", headers=users["alice"],
                   json={"base_url": "http://gw.example/v1", "api_key": "k-alice",
                         "model": "test-model"})
    assert r.json()["ai_configured"] is True
    me_a = client.get("/api/v1/auth/me", headers=users["alice"]).json()
    me_b = client.get("/api/v1/auth/me", headers=users["bob"]).json()
    assert me_a["ai_configured"] is True and me_b["ai_configured"] is False
    assert me_b["ai_prompt_dismissed"] is False         # bob still gets the first-login prompt


# ---------- run ownership ----------

def test_runs_are_private_with_admin_override(client, users, banks):
    r = client.post("/api/v1/runs", headers=users["alice"],
                    json={"task_name": "alice-run", "banks": [{"bank": "priv-alice", "levels": []}],
                          "target_url": "http://127.0.0.1:1", "proxy_enabled": False,
                          "ai_assist": False})
    assert r.status_code == 200, r.text
    run_id = r.json()["id"]

    runs_b = client.get("/api/v1/runs", headers=users["bob"]).json()
    assert run_id not in [x["id"] for x in runs_b.get("runs", runs_b if isinstance(runs_b, list) else [])] \
        if isinstance(runs_b, list) else all(x["id"] != run_id for x in runs_b["runs"])
    assert client.get("/api/v1/runs/" + run_id, headers=users["bob"]).status_code == 404
    assert client.get("/api/v1/runs/" + run_id, headers=users["owner"]).status_code == 200
    assert client.get("/api/v1/runs/" + run_id, headers=users["alice"]).status_code == 200
    # bob cannot judge/review/report alice's run either
    assert client.post(f"/api/v1/runs/{run_id}/ai-judge-batch", headers=users["bob"],
                       json={"limit": 5}).status_code == 404
    assert client.post(f"/api/v1/runs/{run_id}/review", headers=users["bob"],
                       json={"case_id": "p-1", "verdict": "PASS"}).status_code == 404
    assert client.get(f"/api/v1/runs/{run_id}/report", headers=users["bob"]).status_code == 404


def test_member_cannot_run_offline_public_bank(client, users, banks):
    client.patch("/api/v1/benchmarks/pub-shared", headers=users["owner"],
                 json={"status": "offline"})
    try:
        r = client.get("/api/v1/benchmarks/pub-shared", headers=users["bob"])
        assert r.status_code == 404                    # offline public hidden from members
        assert client.get("/api/v1/benchmarks/pub-shared",
                          headers=users["owner"]).status_code == 200
        r = client.post("/api/v1/runs", headers=users["alice"],
                        json={"task_name": "x", "banks": [{"bank": "pub-shared", "levels": []}],
                              "target_url": "http://127.0.0.1:1"})
        assert r.status_code == 404
    finally:
        client.patch("/api/v1/benchmarks/pub-shared", headers=users["owner"],
                     json={"status": "online"})
