<div align="right">English（this page） | [简体中文](../zh/run-eval.md)</div>

# Runs & Archiving

Launch (Runs -> Launch):

| Setting | Description |
|---|---|
| task name | business name (timestamp suffix makes it unique; searchable) |
| agent URL | the /invoke base URL (sample agent: `http://jiuwen-agent:8200` in compose) |
| banks & levels | any visible banks; level filter stacks on top of personal overrides |
| case_ids | optional exact cases — sample before full runs |
| message-level recording | on by default: LLM traffic via the recording proxy (trajectory/cost depend on it) |
| ✨ AI-assisted judging | on by default (needs your User-Center AI): judge suggestions for PENDING cases, failure root causes, AI report summary |
| stability check | off by default: repeat each case k times and compare outputs (result-consistency, orthogonal to success; cost ~k×) |
| schedule | optional future time |

The platform dry-resolves the case set for a live count & ETA; capability handshake happens at
execution (missing profiles -> SKIPPED with a reason, never mis-judged).

Queue: the worker is serial by default (one run at a time, position shown); set
`AGENTGATE_WORKER_CONCURRENCY` to run several runs in parallel (per-case behavior unchanged).
Cancel: queued cancels immediately; running cancels between cases.

Verdicts: PASS / FAIL (hard layer), **PENDING** (free_text etc.; not counted as failure, gate
shows PENDING(n)), SKIPPED (environment unmet). A run whose target is unreachable is marked
**failed** with a reason — no misleading score, dimensions n/a.

PENDING handling (all inside the items tab, evidence and ruling on one screen): click
"✧ Arbitrate" to open the case detail dialog (question, full answer, gold, tool sequence,
per-check details); "✨ AI judge" gets a suggestion with per-point rubric coverage, or
"✨ Batch AI judge" drains every PENDING case; the human final ruling records reviewer +
note — **when the last PENDING is ruled**, the total and final gate are recomputed and the
report can be refreshed with "↻ Rebuild report". The "high-confidence auto-adopt" switch
(User Center -> AI) auto-applies confidence=high suggestions during the run (judge.jsonl).

Reports: radar, diagnostic cards, bilingual report ([Diagnostic report](diagnostic-report.md));
run the same bank twice and use the compare page for radar overlay + per-dimension deltas.

Archived artifacts per run (data volume `results/<run_id>/`, downloadable in the Artifacts
tab): report.md / report-en.md, scores.json, eval_results.json, failures.jsonl, judge.jsonl,
answers.jsonl (full agent answers — powers the detail dialog and AI fix gold),
spans_raw.json, llm_calls_raw.json, runs_meta.json.

Retention & deletion: `report_retention_days` (default 30) sweeps result dirs hourly (runs
rows remain); manual per-run cleanup = delete `results/<run_id>/`; full reset destroys
accounts/banks too. Run results are private data — never upload them to public repos.
