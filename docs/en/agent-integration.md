<div align="right">English（this page） | [简体中文](../zh/agent-integration.md)</div>

# Agent Integration

Start with the bundled sample agent, then port the same contract to yours. Why the platform
collects trajectories over two channels: [Trace recording](trace-recording.md).

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
 "trace_id": "your-internal-id", "usage_total": 12345, "audit": []}
```

`final_json` is what the judge compares: numeric needs `value` (unit/evidence recommended);
boolean `value: "yes"/"no"`; refusal keywords must appear in answer_text. Without final_json
the judge falls back to text matching (extractive/refusal OK, numeric FAILs).

**GET /capabilities (recommended)** — {"agent", "profiles": [...], "llm_base_url_supported":
true}. The platform handshakes against bank requirements; missing profiles -> cases SKIPPED
with a reason, never mis-judged.

**OTLP export (recommended)** — env `OTEL_EXPORTER_OTLP_ENDPOINT=http://<platform>:4318`.
Spans (private convention or openJiuwen-native gen_ai.*): `agent.run` (root, agent.id/model),
`llm.call` (usage tokens), `tool.execute` (tool.name, tool.status ok/denied/error). Without
OTLP, evals still run (answer-level judging) but forbidden-tool attempts, broken-chain
detection and agent identity are lost — see [Trace recording](trace-recording.md).

**Message-level recording (recommended, on by default)** — when recording is on, /invoke
carries `llm_base_url`: temporarily use it as the agent's LLM gateway (sample:
`fin_runtime/llm.py`). Ignoring it degrades recording; cost dimension shows n/a.

## 3) Checklist

health OK -> capabilities lists profiles -> 1-case run shows trace_id & tool sequence in the
run detail -> re-run with recording shows the trajectory section & cost dimension -> then full
banks (sample first to estimate cost).
