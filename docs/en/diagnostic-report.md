<div align="right">

English（this page） | [简体中文](../zh/diagnostic-report.md)

</div>

# Diagnostic Report: what one run produces and how to read it

Run detail page blocks: progress & gate (GREEN / FAIL / PENDING(n) / SKIPPED(n)); dimension
radar (n/a for missing data; "safety-capped" badge when safety < 60); **native reading card**
(each open-source bank under its own dataset metric, next to the platform reading);
per-dimension diagnostic cards (penalty signals / worst samples / improvements); per-case
rows with a one-line human summary — click the case id for the **detail dialog** (question,
full agent answer, gold, tool sequence, per-check ✓/✗ details; PENDING cases arbitrate right
there, with "✨ AI judge" and "✨ Batch AI judge" (batch = background job, polled progress); Report tab (bilingual statistics report —
AI summary first, failure-attribution distribution, six-dimension overview, native reading,
token summary); Trajectory tab (plain-language behavior bullets per case); Artifacts tab
(downloads).

Archived artifacts under data volume `results/<run_id>/`:

| File | Contents |
|---|---|
| report.md / report-en.md | bilingual statistics report: AI summary (first), failure-attribution distribution, six-dimension overview & diagnostics, native reading, token summary (per-case details live in the items tab) |
| scores.json | machine-readable six-dimension scores (run-level, per-case, cards) |
| eval_results.json | full per-case judging record: scores, cost, evidence (checkpoint hits), error_localization, attribution |
| failures.jsonl | failed cases with ASI (fuel for the evolution engine) |
| judge.jsonl | AI judge suggestions with rubric coverage and adoption status (incl. post-hoc batch judging) |
| answers.jsonl | full agent answers + FINAL JSON (source for the detail dialog and AI fix gold) |
| spans_raw.json / llm_calls_raw.json | raw spans and message-level records |
| runs_meta.json | per-case meta (trace_id, tool sequence, answer preview) |

Archive & cleanup rules: [Runs & archiving](run-eval.md).
