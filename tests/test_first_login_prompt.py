"""First-login AI-assist prompt flag + empty-field defaults."""
import os
import tempfile

from fastapi.testclient import TestClient

from agentgate.webapp.app import create_app


def _client():
    os.environ["AGENTGATE_DATA_DIR"] = tempfile.mkdtemp()
    os.environ["OWNER_PASSWORD"] = "test-owner-1"
    app = create_app(start_services=False)
    return TestClient(app)


def test_ai_prompt_dismissed_roundtrip():
    with _client() as c:
        tok = c.post("/api/v1/auth/login",
                     json={"username": "owner", "password": "test-owner-1"}).json()["token"]
        H = {"Authorization": "Bearer " + tok}
        me = c.get("/api/v1/auth/me", headers=H).json()
        assert me["ai_configured"] is False and me["ai_prompt_dismissed"] is False
        # user chooses "later" -> the prompt never nags again
        assert c.post("/api/v1/auth/me/ai-prompt-dismissed", headers=H).json()["ok"] is True
        assert c.get("/api/v1/auth/me", headers=H).json()["ai_prompt_dismissed"] is True


def test_run_with_empty_task_name_uses_default():
    with _client() as c:
        tok = c.post("/api/v1/auth/login",
                     json={"username": "owner", "password": "test-owner-1"}).json()["token"]
        H = {"Authorization": "Bearer " + tok}
        # the run creation gets past task_name validation (fails later on banks: none
        # exist in this fresh data dir) — proving the empty name no longer 400s
        r = c.post("/api/v1/runs", headers=H,
                   json={"task_name": "", "banks": [], "target_url": "http://x"})
        assert "task_name" not in (r.json().get("detail") or "")
