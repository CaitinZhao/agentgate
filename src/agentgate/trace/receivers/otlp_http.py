"""Local OTLP/HTTP receiver (P0 version): receives protobuf spans exported by the target and buffers them.

Protocol: POST /v1/traces with an ExportTraceServiceRequest (protobuf) body;
JSON (application/json) also accepted. Production can swap in an external collector, same interface.
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List


def _any_value(v) -> object:
    field = v.WhichOneof("value")
    if field == "array_value":
        return [_any_value(x) for x in v.array_value.values]
    if field == "kvlist_value":
        return _kvmap(v.kvlist_value.values)
    return getattr(v, field, None) if field else None


def _kvmap(kvlist) -> Dict:
    out: Dict = {}
    for kv in kvlist:
        val = _any_value(kv.value)
        if val is not None:
            out[kv.key] = val
    return out


def _proto_body_to_spans(body: bytes) -> List[Dict]:
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )
    req = ExportTraceServiceRequest.FromString(body)
    spans: List[Dict] = []
    for rs in req.resource_spans:
        res_attrs = _kvmap(rs.resource.attributes)
        for ss in rs.scope_spans:
            for sp in ss.spans:
                spans.append({
                    "trace_id": sp.trace_id.hex(),
                    "span_id": sp.span_id.hex(),
                    "name": sp.name,
                    "start_unix_nano": int(sp.start_time_unix_nano),
                    "end_unix_nano": int(sp.end_time_unix_nano),
                    "attributes": {**res_attrs, **_kvmap(sp.attributes)},
                })
    return spans


class OTLPHTTPReceiver:
    """In-process OTLP/HTTP receiver: started/stopped around CLI runs; no standalone collector process needed."""

    def __init__(self, port: int = 4318):
        self.port = port
        self.spans: List[Dict] = []
        self._lock = threading.Lock()
        self._httpd = None
        self._thread = None

    # -- lifecycle ---------------------------------------------------------
    def start(self, bind: str = "0.0.0.0"):
        recv = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    if "json" in (self.headers.get("Content-Type") or ""):
                        import json
                        spans = json.loads(body.decode("utf-8")).get("spans", [])
                    else:
                        spans = _proto_body_to_spans(body)
                except Exception as e:  # return 200 even on decode errors so the agent side never breaks
                    spans = []
                    recv._log_decode_error(e)
                with recv._lock:
                    recv.spans.extend(spans)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):  # silent
                pass

        # Bind 0.0.0.0 by default: containerized targets export via the bridge address
        # (e.g. 172.17.0.1); binding 127.0.0.1 receives nothing. Never expose this port publicly.
        # If the port is taken (parallel debug instances), back off and retry — runs are long.
        # errno 98/99 = Linux EADDRINUSE; 10048 = Windows WSAEADDRINUSE.
        import time as _time
        deadline = _time.time() + 600
        while True:
            try:
                self._httpd = ThreadingHTTPServer((bind, self.port), Handler)
                break
            except OSError as e:
                if e.errno not in (98, 99, 10048) or _time.time() > deadline:
                    raise
                _time.sleep(5)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def _log_decode_error(self, e):
        self.decode_errors = getattr(self, "decode_errors", 0) + 1
        self.last_decode_error = str(e)

    # -- query -------------------------------------------------------------
    def _wait_locked(self, trace_id: str, timeout: float) -> List[Dict]:
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                found = [s for s in self.spans if s.get("trace_id") == trace_id]
            if found:
                return found
            time.sleep(0.2)
        return []

    def get_spans(self, trace_id: str, timeout: float = 15.0) -> List[Dict]:
        """Wait for and return spans by trace_id (agents export asynchronously, so wait briefly)."""
        if not trace_id:
            return []
        return self._wait_locked(trace_id, timeout)
