"""Operator-in-the-loop relay: turn ANY interactive answerer (a human, an LLM chat
session, a code agent) into an AgentGate target agent — no OTel, no SDK, no code changes
on the platform side.

Setup:
  python tools/agent_relay.py --port 8210 --spool ./spool
  # then create a run with target_url = http://127.0.0.1:8210

Flow: POST /invoke parks the case payload at <spool>/pending/<stem>.json and long-polls
<spool>/answers/<stem>.json (up to --wait seconds; keep it under the platform's 600s
invoke timeout). The operator reads the pending file, writes the answer file in the
invoke response schema ({"answer_text", "final_json", "trace_id", "usage_total",
"audit"}), and the relay returns it verbatim.

Answering from the shell, for example:
  cat spool/pending/<stem>.json                 # read query + context
  cat > spool/answers/<stem>.json <<'EOF'
  {"answer_text": "...", "final_json": {"answer": "..."}, "trace_id": "<stem>",
   "usage_total": 0, "audit": [{"tool": "my_tool", "status": "ok", "summary": "..."}]}
  EOF

The audit entries become the tool sequence the judge sees (tool checkpoints, red-line
scan, trajectory "what it did") — report what the operator actually did, honestly.
No /capabilities endpoint: the platform treats the relay as an unknown agent and
skips nothing.
"""
import argparse
import json
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

ARGS = None  # populated in main()


class RelayHandler(BaseHTTPRequestHandler):
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
            return self._json(200, {"status": "ok", "agent": "agentgate-relay",
                                    "spool": str(ARGS.spool)})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/invoke":
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except ValueError:
            return self._json(400, {"error": "body is not valid JSON"})
        stem = "%s_%s" % (time.strftime("%H%M%S"),
                          str(body.get("case_id") or uuid.uuid4().hex[:6]).replace("/", "_"))
        (ARGS.pending / (stem + ".json")).write_text(
            json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        print("[relay] pending case -> %s (waiting up to %ss for spool/answers/%s.json)"
              % (stem, ARGS.wait, stem), flush=True)
        answer_path = ARGS.answers / (stem + ".json")
        started = time.time()
        while time.time() - started < ARGS.wait:
            if answer_path.exists():
                try:
                    return self._json(200, json.loads(answer_path.read_text(encoding="utf-8")))
                except ValueError:
                    pass                     # half-written file: keep polling
            time.sleep(1)
        return self._json(200, {"answer_text": "relay timeout: operator did not answer "
                                             "within %ds" % ARGS.wait,
                                "final_json": {}, "trace_id": stem, "usage_total": 0,
                                "audit": []})


def main():
    global ARGS
    ap = argparse.ArgumentParser(description="AgentGate operator-in-the-loop relay")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8210)
    ap.add_argument("--spool", default="./spool", help="pending/answers spool directory")
    ap.add_argument("--wait", type=int, default=540,
                    help="seconds to wait for an answer file (keep < 600, the invoke timeout)")
    ARGS = ap.parse_args()
    spool = Path(ARGS.spool).resolve()
    ARGS.spool = spool
    ARGS.pending = spool / "pending"
    ARGS.answers = spool / "answers"
    ARGS.pending.mkdir(parents=True, exist_ok=True)
    ARGS.answers.mkdir(parents=True, exist_ok=True)
    print("agentgate-relay on http://%s:%d  spool=%s" % (ARGS.host, ARGS.port, spool),
          flush=True)
    HTTPServer((ARGS.host, ARGS.port), RelayHandler).serve_forever()


if __name__ == "__main__":
    main()
