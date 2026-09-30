<div align="right">

English（this page） | [简体中文](../zh/sandbox.md)

</div>

This document explains where the evaluated agent runs, what the isolation boundary
between the platform and the agent is, and how to use the P1 sandbox (platform-managed
environments + terminal-state assertions).

## Today: the agent brings its own environment; the platform only observes

AgentGate's evaluation model is "the platform watches from outside the gate":

- The target agent is an **independently deployed service** (its own container/process/
  model credentials) exposing `/invoke`. The sample agent ships via `Dockerfile.agent` /
  the compose `jiuwen-agent` service.
- The platform does exactly three things to the agent: call `/invoke` over HTTP, collect
  its OTel traces on :4318, and forward/record its LLM traffic on :8300. **The platform
  never enters the agent's process space and never holds its keys.**
- The resulting score is a **stack score** (model + prompt + tools + retrieval + policy
  as a whole); reports record agent identity and component versions for cross-version
  comparison.

Boundary note: what tools the agent runs and what data it accesses are the agent
deployer's responsibility — the platform does not vouch for it, so **do not expose the
sample agent or the platform to untrusted callers** (see the deployment doc).

## The evaluation environment must match the real one

The scoring rule "attribute environment gaps separately from agent faults" relies on
environment declaration: banks declare profiles and material sources via **domain
packs**; at launch the platform handshakes with the agent (`/capabilities`), and cases
whose profile the agent did not declare are SKIPPED with a reason — never mis-judged.

## Shipped in P1: platform-managed sandbox + terminal-state assertions

For `state` cases the platform can now **host the execution environment**: the agent's
terminal commands run inside a sandbox the platform owns, and after the invoke the
platform checks the environment's **final state** — no longer trusting what the agent
reports in its `final_json`. Writing such a case (everything inside the free-form
`gold.final` dict, no schema change):

```json
"final": {
  "sandbox": {
    "image": "python:3.11-slim",
    "setup": [{"cmd": "mkdir -p /app/in /app/out"},
              {"write_file": {"path": "/app/in/task.txt", "content": "monthly data"}}]
  },
  "assertions": [
    {"read_file": "/app/out/report.json", "json_path": "status", "equals": "done"},
    {"exec": "test -f /app/out/task.bak", "exit_code": 0}
  ]
}
```

Run flow (automatic inside `run_case_set`):

1. The platform creates the sandbox from the `sandbox` spec and runs `setup`
   (directories, input files).
2. The **exec endpoint and a one-time token** are injected into the case's
   `input.context` — the agent runs terminal commands via
   `POST /api/v1/sandbox/exec` (body `{"token", "cmd"}`).
3. After the invoke, the platform evaluates every `assertion` (all pass = PASS, any
   miss = FAIL), then destroys the sandbox and revokes the token.

Judging semantics (honest layering):

- A state case with env assertions: sandbox available -> deterministic verdict;
  **sandbox unavailable (provider off / create failed) -> PENDING** — the agent's
  self-reported answer is never used to fake a verdict.
- Legacy assertions without `read_file`/`exec` (a path walk over the agent's
  `final_json`) keep their original meaning.

### Configuration and providers

`agentgate.json`:

```json
"sandbox": {
  "provider": "docker",            // off (default = disabled) | docker | subprocess
  "image": "python:3.11-slim",     // default image when the case spec has none
  "network": "",                   // optional docker --network
  "exec_base_url": "",             // agent-reachable platform URL; empty = http://127.0.0.1:8030
  "ttl_s": 1800                    // exec token TTL
}
```

| Provider | Use | Isolation |
|---|---|---|
| `docker` (production) | one container per case, executed via `docker exec` | container isolation |
| `subprocess` (dev/demo) | POSIX commands in a local temp dir (needs bash) | **none** — trusted environments only |
| `off` | disabled: state cases fall back to PENDING | — |

Security boundary: the exec endpoint exposes exactly one action — run a command behind
a token. Tokens are random per sandbox, revoked when the case closes (plus a TTL), and
an agent cannot reach other cases' environments or anything else on the platform. The
commands inside the sandbox come from the evaluated agent — **never put secrets in the
sandbox image**, and leave `network` empty unless required.

Example bank: `cases/sandbox-demo/cases.json` (file writes + command assertions);
tests: `tests/test_sandbox.py`.

## Roadmap (beyond P1)

- **Platform material service**: host domain materials (e.g. policy libraries) on the
  platform and inject them via `/invoke` context, replacing "agent brings its own
  materials".
- **Trace provenance**: backfill every in-sandbox tool execution's input/output into
  span attributes.
- **Stability checks x sandbox semantics** (environment reuse vs reset per repeat).
