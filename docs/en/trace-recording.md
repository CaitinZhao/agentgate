<div align="right">English（this page） | [简体中文](../zh/trace-recording.md)</div>

# Trace Recording: agent OTLP export + message-level recording proxy

Why two channels, what each sees, and what both sides must configure. Integration steps with
sample code: [Agent integration](agent-integration.md).

| Channel | Port | Sees | Cannot see |
|---|---|---|---|
| ① OTLP export (agent pushes) | 4318 | span graph: agent.run / llm.call / tool.execute; tool ok/denied/error status; parent links; agent identity | message content, injection payloads, per-call token detail |
| ② Recording proxy (platform intercepts) | 8300 | every LLM call's full messages: request/response/tool-call args/usage | calls blocked by the agent's permission layer, span-tree integrity, agent identity |

Complementary: ① answers "what did the agent system DO", ② "what did the model SAY".
Unique to ①: forbidden-tool ATTEMPT detection (safety zero relies on it), broken-chain
detection, agent identity in reports. Unique to ②: injection-follow & canary-leak detection,
skip-answer, exact token cost, argument-level checkpoint hits.

Switches: ① always on (resident receiver; one env var on the agent side). ② on by default —
the platform delivers the proxy URL per call via /invoke's `llm_base_url`; the agent sends its
LLM traffic there. Without it: span-level degradation, cost dimension n/a.

Agent-side summary: `OTEL_EXPORTER_OTLP_ENDPOINT=http://<platform>:4318`; no secrets needed
for ② (the URL arrives per call). Full contract: [Agent integration](agent-integration.md).
