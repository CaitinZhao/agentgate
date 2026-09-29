"""Run targets: real HTTP and offline Mock implementations."""
from typing import Dict, List, Optional


class BaseTarget:
    """Given a query, returns {answer_text, final_json, trace_id, usage_total, audit}."""

    name = "base"

    def invoke(self, cinput: dict) -> Dict:
        raise NotImplementedError

    def prepare(self, case_id: str):
        """Hook before each case run (the mock target selects its script by it)."""

    def spans_for(self, case_id: str) -> List[Dict]:
        """Offline target provides scripted spans; real targets return an empty list (receiver collects)."""
        return []


class HttpTarget(BaseTarget):
    """Real target: POSTs the target agent /invoke (OTel traces collected by the agentgate receiver).

    Default timeout 600s: long-context cases (memory profiles input 20k+ tokens) can take over
    3 minutes per case. Optional llm_base_url is merged into the invoke payload when set —
    capable agents (the jiuwen sample) route that call's LLM traffic through the given
    OpenAI-compatible endpoint (the platform recording proxy); agents that ignore the extra
    field are unaffected.
    """

    name = "http"

    def __init__(self, base_url: str, timeout: float = 600.0, llm_base_url: str = ""):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.llm_base_url = llm_base_url or ""

    def invoke(self, cinput: dict) -> Dict:
        import httpx
        payload = dict(cinput)
        if self.llm_base_url:
            payload["llm_base_url"] = self.llm_base_url
        r = httpx.post(self.base_url + "/invoke", json=payload, timeout=self.timeout)
        r.raise_for_status()
        return r.json()


class MockTarget(BaseTarget):
    """Offline target: returns scripted answers and spans per case_id (for smoke tests; no network)."""

    name = "mock"

    def __init__(self, responses: Dict[str, Dict], spans: Dict[str, List[Dict]],
                 default_response: Optional[Dict] = None):
        self.responses = responses
        self.spans = spans
        self.default_response = default_response or {
            "answer_text": "mock answer", "final_json": {}, "trace_id": "",
            "usage_total": 0, "audit": [],
        }
        self._current: Dict = dict(self.default_response)

    def prepare(self, case_id: str):
        self._current = dict(self.responses.get(case_id) or self.default_response)

    def invoke(self, query: str) -> Dict:
        return dict(self._current)

    def spans_for(self, case_id: str) -> List[Dict]:
        return self.spans.get(case_id, [])
