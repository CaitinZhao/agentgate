from fastapi import FastAPI
from pydantic import BaseModel

from . import observability as O
from .agent import FinAgent
from .config import Settings

cfg = Settings()
app = FastAPI(title="fin-runtime", version="0.1.0")
_state = {"tracer": None, "agents": {}}


@app.on_event("startup")
def _startup():
    _state["tracer"] = O.init_tracing(cfg)
    _state["agent"] = FinAgent(cfg, _state["tracer"])


@app.get("/health")
def health():
    return {"status": "ok", "model": cfg.llm_model, "provider": cfg.llm_provider,
            "agent_id": cfg.agent_id}


class InvokeBody(BaseModel):
    query: str
    profile: str = "bank"      # bank | fb


@app.post("/invoke")
def invoke(body: InvokeBody):
    if body.profile not in _state["agents"]:
        _state["agents"][body.profile] = FinAgent(cfg, _state["tracer"], profile=body.profile)
    result = _state["agents"][body.profile].run(body.query)
    return {"answer_text": result.answer_text, "final_json": result.final_json,
            "trace_id": result.trace_id, "usage_total": result.usage_total,
            "audit": result.audit}
