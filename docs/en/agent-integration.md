<div align="right">

English（this page） | [简体中文](../zh/agent-integration.md)

</div>

# Agent Integration

This document is for developers of evaluated agents: start with the bundled sample agent,
then port the same contract to yours. Why the platform collects trajectories over two
channels: [Trace recording](trace-recording.md).

## 1) Run the sample agent (jiuwen)

```bash
docker compose --profile agent up -d --build      # deployment doc, mode B
curl -s http://127.0.0.1:8200/health
curl -s http://127.0.0.1:8200/capabilities
# {"agent":"jiuwen-sample-agent","profiles":["base","bank","locomo","longmem","fb","tau-airline","tau-retail"],...}
```

Model credentials live in `tests/fixtures/.env` (deployment doc step 2). Target URL in the
platform: `http://jiuwen-agent:8200` (compose network) or `http://127.0.0.1:8200` (same host).

Reference implementations (copy from these): `tests/fixtures/jiuwen_server.py` (the three
endpoints, tool registration, span wrapping), `tests/fixtures/jiuwen_agent.py` (ReAct loop on
the openJiuwen SDK), `tests/fixtures/fin_runtime/` (domain tools, red-line blocking, trace
export wrappers).

## 2) What YOUR agent must implement

**POST /invoke (required)** — request {query, profile?, context?, llm_base_url?}; response:

```json
{"answer_text": "consolidated revenue 9.62B",
 "final_json": {"value": 96.2, "unit": "USD billions", "evidence": "...", "answer": "..."},
 "trace_id": "your-internal-id",
 "usage_total": 12345,
 "audit": [{"tool": "retrieve_report", "status": "ok", "summary": "2 hits"},
           {"tool": "export_data", "status": "denied"}]}
```

`final_json` is what the judge compares: numeric needs `value` (unit/evidence recommended);
boolean `value: "yes"/"no"`; refusal keywords must appear in answer_text. Without final_json
the judge falls back to text matching (extractive/refusal OK, numeric FAILs).

`audit` is the lightweight trace fallback for agents that do not export OTel: each entry is
`{"tool": name, "status": "ok|denied|error", "summary": str}` (a bare string also works;
denied/blocked/refused count as blocked calls). When the span-derived trace has no tool
steps, these entries become the tool sequence — tool-type checkpoints, red-line scanning and
the trajectory summary keep working. With OTel present, OTel wins and audit is not double
counted. `usage_total` likewise backs the cost dimension when spans carry no usage.

**GET /capabilities (recommended)** — {"agent", "profiles": [...], "traces": true,
"llm_base_url_supported": true}. The platform handshakes against bank requirements; missing
profiles -> cases SKIPPED with a reason, never mis-judged. `traces` only affects the
span-wait window: declared true -> up to 15s per case for asynchronously exported spans;
a capabilities document without the flag -> 3s; no /capabilities at all -> 8s.

**OTLP export (recommended)** — env `OTEL_EXPORTER_OTLP_ENDPOINT=http://<platform>:4318`.
Spans (private convention or openJiuwen-native gen_ai.*): `agent.run` (root, agent.id/model),
`llm.call` (usage tokens), `tool.execute` (tool.name, tool.status ok/denied/error). Without
OTLP, evals still run (answer-level judging) but forbidden-tool attempts, broken-chain
detection and agent identity are lost — see [Trace recording](trace-recording.md).

**Message-level recording (recommended, on by default)** — when recording is on, /invoke
carries `llm_base_url`: temporarily use it as the agent's LLM gateway (sample:
`fin_runtime/llm.py`). Ignoring it degrades recording; cost dimension shows n/a.

## 3) Two fast paths (no changes to your agent needed)

**Start from the minimal example**: `examples/external_agent_example.py` is a zero-dependency
single-file agent implementing /health, /capabilities and /invoke (with audit reporting).
Run it, probe it, then replace `_answer()` with your real logic:

```bash
python examples/external_agent_example.py --port 8220
agentgate probe http://127.0.0.1:8220
```

**Operator-in-the-loop (a human / an LLM chat session answers)**: `tools/agent_relay.py`
turns any interactive answerer into a target agent — an invoke parks the case payload and
waits; the operator writes the answer file and the relay returns it verbatim:

```bash
python tools/agent_relay.py --port 8210 --spool ./spool
# target = http://127.0.0.1:8210; each case waits at spool/pending/<stem>.json until you
# write spool/answers/<stem>.json in the invoke response format
```

## 4) Probe before you burn a run

Validate the target endpoint BEFORE a full evaluation (CLI, API and the run-create page's
"Test connection" button are equivalent):

```bash
agentgate probe http://127.0.0.1:8200
#  ✓ health        HTTP 200
#  ✓ capabilities  profiles: base, bank …
#  ✓ invoke        HTTP 200 in 0.3s
#  ✓ answer_text   131 chars
#  ✗ final_json    missing — numeric/yes-no cases will FAIL without it
#  ! audit         empty — tool checkpoints need OTel spans or audit entries
```

`✗` = the run would break or misjudge (fix first); `!` = it completes but a dimension or
signal degrades (missing optional endpoints only warn, matching the platform's permissive
treatment of unknown agents).

## 5) Checklist

1. `agentgate probe <target>`: no ✗ items.
2. A 1-case run (pin it with `case_ids`) shows trace_id & the tool sequence in the run detail.
3. Re-run with message-level recording: the trajectory section & cost dimension appear.
4. Then full banks (sample first to estimate cost).

Deployment questions: [Deployment](deployment.md); judging rules: [Scoring](scoring.md).

## 6) FAQ

- **All cases FAIL, run status failed, "target unreachable"**: the target address is
  unreachable (agent down / wrong port). The platform gives no scores — all six dimensions
  are n/a; fix the address and re-run.
- **My agent has no /capabilities**: treated as an unknown agent (permissive: no skips), but
  banks requiring a profile cannot handshake and get SKIPPED — implement the endpoint.
- **Tool-type checkpoints all fail / empty trajectory summary**: the agent reported no tool
  sequence at all. Either export OTel spans (OTEL_EXPORTER_OTLP_ENDPOINT -> platform :4318)
  or report each tool call honestly in the response `audit`. The probe's audit check flags
  this in advance.
- **A few seconds of fixed delay before each result**: the platform is waiting for
  asynchronously exported spans. If your agent does not export OTel, say so in
  /capabilities (omit `traces`) and the window shrinks automatically.
- **The probe's invoke check times out**: expected for an operator-in-the-loop relay (nobody
  wrote the answer file). In relay mode skip the invoke probe and use the spool workflow.
