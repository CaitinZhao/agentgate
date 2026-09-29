<div align="right">English（this page） | [简体中文](../zh/sandbox.md)</div>

# Sandbox Execution: where the agent runs, and the isolation boundary

Current model: **the platform watches from outside the door**. The agent under test is an
independently deployed service (own container/process/model credentials) exposing `/invoke`.
The platform only: calls `/invoke`, receives spans on :4318, forwards/records LLM traffic on
:8300. It never enters the agent's process space nor holds its keys — scores are stack-level
(model+prompts+tools+retrieval+policy) with agent identity recorded.

Implication: tools and data access are the agent deployer's responsibility — do not expose
the sample agent or the platform to untrusted callers (see deployment security notes).

Environment fidelity is declared, not assumed: banks declare the required profile & materials
via the **domain pack**; the capability handshake (`/capabilities`) skips cases the agent
cannot run (SKIPPED with a reason) instead of mis-judging them.

Sandboxed execution (platform-hosted tools, tau-style state assertions, platform-served
materials) is a roadmap item (P1): Docker sandbox provider, platform material service,
provenance backfill. Until then, state-type cases without assertions stay PENDING — never
force a deterministic verdict.
