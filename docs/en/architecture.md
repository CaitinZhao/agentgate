<div align="right">

English（this page） | [简体中文](../zh/architecture.md)

</div>

# Architecture & Repository Layout

This page summarizes the design principles, module boundaries and repository layout.
Per-topic deep dives are the sub-documents linked below.

## Design principles

1. **Evaluation observes; it never decides.** The platform runs cases, judges and reports;
   accept/rollback belongs to the evolution control plane. The evaluation module has no
   write access to the system under test.
2. **The score's denominator is the whole agent stack** (model + prompts + tools + retrieval
   + policy). Reports record agent identity and component versions.
3. **Layered honesty.** Hard checks run first; anything the machine cannot judge is PENDING
   (AI judge suggestion / human); missing data shows n/a — no fake scores.
4. **Judge is separate from the bank.** Banks declare what is right (gold); how to judge
   (judge version, AI config) is platform configuration.
5. **Penalties are part of the score.** Skip-answer shortcuts, repeated calls, denied
   attempts and cost explosions all deduct points.
6. **Credentials stay separated.** The platform never holds the agent's model key; recording
   is proxy-forwarding; the platform's own AI assist uses per-user config.
7. **Data is data, code is code.** Case definitions are data (form/JSON/Excel); judging
   logic lives in code; ports and gateways are centralized configuration.

## Modules & data flow

```text
Banks (banks/*/cases.db, gold v2)
  -> Run (worker queue; optional concurrency)
  -> Agent /invoke (HTTP) + two observation channels:
       (1) OTLP :4318   span-level trajectory
       (2) recording proxy :8300   message-level LLM records
  -> Judging pipeline (red lines -> format -> typed final -> checkpoints -> process signals)
  -> Six-dimension scores (scores.json) + bilingual reports
  -> Gate (GREEN / FAIL / PENDING(n)) + artifacts (data/results/<run_id>/)
```

## Repository layout

```text
agentgate/
├── src/agentgate/     # case / evaluator / analysis / control / run / trace / result / webapp
├── web/               # Vue 3 frontend (built inside the platform image)
├── tests/             # developer tests + the sample-agent fixtures (tests/fixtures/)
├── cases/             # shipped banks (example/injection) + regeneration scripts
├── deploy/            # deploy scripts (one-click remote / compose / vision review)
├── docs/              # this documentation (zh + en, screenshots in docs/images/)
├── Dockerfile.web     # platform image (multi-stage; frontend built in-image)
├── Dockerfile.agent   # sample agent image
└── docker-compose.yml # one-shot platform (+ optional sample agent)
agent-contracts/       # shared schema package for traces / eval results
```

## Sub-documents

| Topic | Doc | One-liner |
|---|---|---|
| Trace recording | [trace-recording.md](trace-recording.md) | what each observation channel sees, why both exist |
| Scoring | [scoring.md](scoring.md) | three-layer judging, gold v2, six-dimension rules |
| Sandbox | [sandbox.md](sandbox.md) | where the agent runs; isolation boundary & roadmap |
| AI assist | [ai-assist.md](ai-assist.md) | what the platform's own LLM does, config, boundaries |
| Diagnostic report | [diagnostic-report.md](diagnostic-report.md) | reports and artifacts of one run, how to read them |
