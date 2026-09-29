"""AgentGate REST (minimal surface): health check + trigger one case-bank evaluation."""
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

from ..control.service import run_case_set
from ..run.targets.base import HttpTarget

app = FastAPI(title="AgentGate", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


class RunBody(BaseModel):
    cases_dir: str = "cases/example"
    target: str = "http://127.0.0.1:8100"
    out_dir: Optional[str] = None
    receiver_port: int = 4318


@app.post("/api/v1/runs")
def create_run(body: RunBody):
    import datetime
    out = body.out_dir or ("results/run-%s" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    summary = run_case_set(body.cases_dir, HttpTarget(body.target), out,
                           receiver_port=body.receiver_port)
    return summary
