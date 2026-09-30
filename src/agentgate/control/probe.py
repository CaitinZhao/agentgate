"""Pre-flight contract probe for a target agent endpoint.

Answers "will my agent integrate?" WITHOUT burning a real run: one /health touch,
one /capabilities touch, one synthetic /invoke round-trip, each validated against
the invoke contract (answer_text / final_json / trace_id / usage_total / audit).

Surfaced three ways so integrators can pick their door:
  - CLI:   `agentgate probe http://agent:8200`
  - API:   POST /api/v1/probe-agent {"target_url": ...}
  - UI:    the connection-test button next to the target field on the run-create page

A check is pass / warn / fail: fail = the run would break or misjudge; warn = the run
completes but a dimension or signal degrades; missing optional endpoints never fail
the probe (the worker is permissive about them too).
"""
import time
from typing import Dict, List


def _add(checks: List[Dict], name: str, status: str, detail: str) -> None:
    checks.append({"name": name, "status": status, "detail": detail})


def probe_agent(base_url: str, timeout: float = 60.0) -> Dict:
    base = (base_url or "").rstrip("/")
    checks: List[Dict] = []
    if not base:
        return {"url": base, "ok": False,
                "checks": [{"name": "url", "status": "fail", "detail": "target url is empty"}],
                "caps": None}
    import httpx

    # 1. /health (optional) — reachability signal only
    try:
        r = httpx.get(base + "/health", timeout=5.0)
        ok = r.status_code == 200
        _add(checks, "health", "pass" if ok else "warn",
             "HTTP %d%s" % (r.status_code, "" if ok else " (non-200; endpoint is optional)"))
    except Exception as e:
        _add(checks, "health", "warn",
             "GET /health failed: %s (endpoint is optional — continuing)" % str(e)[:120])

    # 2. /capabilities (recommended) — profile handshake + span-wait window
    caps = None
    try:
        r = httpx.get(base + "/capabilities", timeout=5.0)
        caps = r.json() if r.status_code == 200 else None
    except Exception:
        caps = None
    if isinstance(caps, dict) and caps:
        profiles = [str(p) for p in (caps.get("profiles") or [])]
        detail = "profiles: %s" % (", ".join(profiles[:12]) + ("…" if len(profiles) > 12 else "")
                                   if profiles else "(none)")
        if caps.get("traces") or caps.get("otel_export"):
            detail += "; traces=true — the platform waits up to 15s for exported spans"
        _add(checks, "capabilities", "pass" if profiles else "warn", detail)
        if not profiles:
            _add(checks, "capabilities-profiles", "warn",
                 "no profiles declared: banks whose pack requires a profile will be SKIPPED")
    else:
        _add(checks, "capabilities", "warn",
             "GET /capabilities unavailable — treated as an unknown agent (no skips), but "
             "banks requiring a profile cannot handshake; declare \"traces\": true so the "
             "platform waits for your exported spans")

    # 3. /invoke round-trip against the contract
    payload = {"query": '[agentgate-probe] Contract self-check: reply with answer_text="pong" '
                        'and final_json={"answer":"pong"}.',
               "case_id": "agentgate-probe", "probe": True}
    latency = -1.0
    resp = None
    try:
        t0 = time.time()
        r = httpx.post(base + "/invoke", json=payload, timeout=timeout)
        latency = time.time() - t0
        if r.status_code != 200:
            _add(checks, "invoke", "fail",
                 "HTTP %d: %s" % (r.status_code, r.text[:160]))
        else:
            resp = r.json()
            _add(checks, "invoke", "pass", "HTTP 200 in %.1fs" % latency)
    except Exception as e:
        _add(checks, "invoke", "fail", "POST /invoke failed: %s" % str(e)[:160])

    if isinstance(resp, dict):
        at = resp.get("answer_text")
        if isinstance(at, str) and at.strip():
            _add(checks, "answer_text", "pass", "%d chars" % len(at))
        else:
            _add(checks, "answer_text", "fail",
                 "missing or empty (contract requires a non-empty string)")

        fj = resp.get("final_json")
        if isinstance(fj, dict):
            _add(checks, "final_json", "pass",
                 "object with keys: %s" % (", ".join(list(fj.keys())[:8]) or "(empty)"))
        elif fj is None:
            _add(checks, "final_json", "warn",
                 "missing — free-text judging falls back to answer_text, but numeric/yes-no "
                 "cases will FAIL without it")
        else:
            _add(checks, "final_json", "fail", "must be an object, got %s" % type(fj).__name__)

        tid = resp.get("trace_id")
        if isinstance(tid, str) and tid.strip():
            _add(checks, "trace_id", "pass", "present")
        else:
            _add(checks, "trace_id", "warn",
                 "missing — trace correlation, broken-chain detection and component "
                 "attribution degrade")

        usage = resp.get("usage_total")
        if isinstance(usage, (int, float)) and not isinstance(usage, bool):
            _add(checks, "usage_total", "pass", str(usage))
        else:
            _add(checks, "usage_total", "warn",
                 "missing or not a number — the cost dimension will be n/a")

        audit = resp.get("audit")
        if isinstance(audit, list) and audit:
            _add(checks, "audit", "pass",
                 "%d entr%s — tool usage is judgeable without OTel" % (len(audit),
                                                                       "y" if len(audit) == 1 else "ies"))
        elif isinstance(audit, list):
            _add(checks, "audit", "warn",
                 "empty array — tool-type checkpoints need either OTel spans "
                 "(OTEL_EXPORTER_OTLP_ENDPOINT) or non-empty audit entries")
        else:
            _add(checks, "audit", "warn",
                 "missing — tool-type checkpoints need either OTel spans or audit entries "
                 '(e.g. [{"tool": "search", "status": "ok"}])')
    elif resp is not None:
        _add(checks, "invoke", "fail", "response is not a JSON object: %s" % type(resp).__name__)

    ok = all(c["status"] != "fail" for c in checks)
    return {"url": base, "ok": ok, "checks": checks, "caps": caps}
