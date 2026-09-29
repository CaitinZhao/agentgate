"""Tiny mock target agent for platform end-to-end checks (no LLM, no network beyond localhost).

Serves POST /invoke with canned answers so a UI-launched run produces PASS/PENDING/FAIL
deterministically. Answers are keyed by substring match on the query.

Run: python -m uvicorn mock_agent_server:app --port 8299   (from tests/fixtures/)
"""
import re

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="mock-agent")


class InvokeBody(BaseModel):
    query: str
    profile: str = "bank"
    context: str = ""
    llm_base_url: str = ""


def _final(payload: dict) -> str:
    return "FINAL: " + re.sub(r"\s+", " ", str(payload).replace("'", '"'))


ANSWERS = [
    ("增长", _final({"value": 42, "unit": "%", "basis": "consolidated", "answer": "42% 增长"})),
    ("自由文本", "这是一段自由文本回答，无法自动判定。"),
    ("原始", _final({"value": 99, "unit": "%", "basis": "consolidated", "answer": "错误数字"})),
]


@app.get("/health")
def health():
    return {"status": "ok", "agent": "mock"}


@app.post("/invoke")
def invoke(body: InvokeBody):
    for needle, answer in ANSWERS:
        if needle in body.query:
            import json
            final = {}
            m = re.search(r"FINAL:\s*(\{.*\})", answer)
            if m:
                try:
                    final = json.loads(m.group(1))
                except ValueError:
                    final = {}
            return {"answer_text": answer, "final_json": final,
                    "trace_id": "mock-trace", "usage_total": 10, "audit": []}
    return {"answer_text": "no scripted answer", "final_json": {},
            "trace_id": "mock-trace", "usage_total": 1, "audit": []}
