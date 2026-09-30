"""Run engine: invoke the target per case -> wait for/collect the trace -> normalize."""
import time
from typing import Optional

from pydantic import BaseModel

from ..case.models import Case
from ..trace.normalizer import normalize
from ..trace.receivers.otlp_http import OTLPHTTPReceiver


class CaseRun(BaseModel):
    class Config:
        arbitrary_types_allowed = True

    case: Case
    response: dict
    trace: object
    wall_time_s: float
    raw_spans: list = []
    model_calls: list = []          # message-level llm-proxy records (empty without the proxy)


class RunEngine:
    def __init__(self, target, receiver: Optional[OTLPHTTPReceiver] = None,
                 trace_wait_s: float = 15.0):
        self.target = target
        self.receiver = receiver
        # how long to wait for asynchronously-exported OTel spans after the invoke returns;
        # the worker derives it from the agent's /capabilities "traces" flag — agents that
        # do not export spans must not pay the full window on every case
        self.trace_wait_s = max(1.0, float(trace_wait_s))

    def run_case(self, case: Case) -> CaseRun:
        self.target.prepare(case.case_id)
        t0 = time.time()
        resp = self.target.invoke(case.input)
        wall = time.time() - t0
        if isinstance(getattr(self.target, "spans_for", None), object) and hasattr(self.target, "spans_for"):
            spans = self.target.spans_for(case.case_id)
        else:
            spans = []
        if self.receiver is not None and resp.get("trace_id"):
            spans = self.receiver.get_spans(resp["trace_id"], timeout=self.trace_wait_s) or spans
        trace = normalize(spans)
        from ..trace.normalizer import merge_response_audit
        trace = merge_response_audit(trace, resp)
        return CaseRun(case=case, response=resp, trace=trace, wall_time_s=wall,
                       raw_spans=spans)
