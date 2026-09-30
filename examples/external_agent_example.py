"""Minimal external agent implementing the full AgentGate invoke contract — the
copy-paste starting point for integrating a real agent.

  python examples/external_agent_example.py --port 8220
  agentgate probe http://127.0.0.1:8220        # contract self-check before a real run
  # then create a run with target_url = http://127.0.0.1:8220

This example answers queries by echoing them back and reports one tool call per
invoke via the response "audit" array — enough for the platform to judge tool-type
checkpoints, red lines and the trajectory summary WITHOUT any OTel setup. Replace
`_answer()` with your agent's real logic (LLM call, RAG, tool loop, ...).

Contract recap (see docs/agent-integration.md):
  GET  /capabilities  -> {"agent", "profiles", "traces", "llm_base_url_supported"}
  POST /invoke        -> {"answer_text", "final_json", "trace_id", "usage_total", "audit"}
  - final_json keys the judge reads: value/answer (typed compare), refused (safety),
    evidence/basis (format quality). Missing -> answer_text text compare only.
  - audit entries: {"tool": name, "status": "ok|denied|error", "summary": str} (a bare
    string also works). Consumed as the tool sequence when no OTel spans arrive.
  - llm_base_url (request field): when the platform enables message-level recording it
    routes your LLM traffic through this URL temporarily — honor it if you can; ignoring
    it only degrades the cost dimension.
  - To report traces instead of audit, set "traces": true in /capabilities and export
    spans to OTEL_EXPORTER_OTLP_ENDPOINT (see docs/trace-recording.md).
"""
import argparse
import json
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler

AGENT_ID = "external-example"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"status": "ok", "agent": AGENT_ID})
        if self.path == "/capabilities":
            return self._json(200, {"agent": AGENT_ID,
                                    "profiles": ["base"],
                                    "traces": False,       # this example reports audit, not OTel
                                    "llm_base_url_supported": False})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/invoke":
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except ValueError:
            return self._json(400, {"error": "body is not valid JSON"})
        query = str(body.get("query") or "")
        t0 = time.time()
        answer, tool_summary = self._answer(query, body)
        # usage_total: token cost of THIS case (cost dimension data source)
        usage = max(1, len(query) // 4 + len(answer) // 4)
        return self._json(200, {
            "answer_text": answer,
            "final_json": {"answer": answer,
                           "evidence": "echo agent; %.2fs" % (time.time() - t0)},
            "trace_id": uuid.uuid4().hex,
            "usage_total": usage,
            # the tool sequence the judge sees when no OTel spans are exported
            "audit": [{"tool": "echo", "status": "ok", "summary": tool_summary}],
        })

    def _answer(self, query: str, invoke_body: dict):
        """Replace with the real agent logic. The example just echoes."""
        return ("[external-example] received: %s" % query[:500],
                "echoed %d chars" % len(query))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8220)
    args = ap.parse_args()
    print("%s on http://%s:%d  (probe it: agentgate probe http://%s:%d)"
          % (AGENT_ID, args.host, args.port, args.host, args.port), flush=True)
    HTTPServer((args.host, args.port), Handler).serve_forever()
