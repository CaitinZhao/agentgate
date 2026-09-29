<div align="right">English（this page） | [简体中文](../zh/ai-assist.md)</div>

# AI Assist: what the platform's own LLM does, config, boundaries

Configure per user in **User Center -> AI enhancements**: gateway base_url (OpenAI-compatible),
API key (stored server-side only, never echoed, never sent to evaluated agents), model, and a
"high-confidence auto-adopt" switch (off by default).

Four enhancements:

| Feature | What it does | Default |
|---|---|---|
| Report AI summary | appends a one-line conclusion + 2-4 improvement actions (which layer to fix) to the report | on when AI configured |
| Failure root causes | per-failed-case root_cause + fix, in the report AI section | same |
| Judge suggestions | PENDING free_text cases scored against the rubric: PASS/FAIL/PARTIAL + confidence + rationale | display-only; auto-adopt only when enabled AND confidence=high (recorded in judge.jsonl) |
| Drafting | case-gold drafting in the case form; domain-pack drafting in the bank editor | drafts require human review; saving a pack signs it llm-draft+human-reviewed |
| Post-hoc AI judging (single/batch) | runs that finished before AI was configured leave PENDING cases stuck — "✨ AI judge" per case and "✨ Batch AI judge" drain them all (with per-point rubric coverage); suggestions feed the human final ruling. The "✨ AI-assisted judging" toggle at launch controls whether a run generates suggestions/root-causes/summary automatically |
| Dispute → AI gold revision | "✨ AI fix gold" on FAIL rows in the run detail: reviews query + current gold + the agent's actual answer + judging rationale, and drafts a revised gold — first distinguishing "agent is wrong (keep gold)" from "gold is unreasonable (propose fix)" | draft is editable; applying goes through the normal case PATCH and takes effect on the next re-run |

## Every AI prompt is visible and editable

Every LLM prompt the platform ships lives in User Center -> AI enhancements ->
"AI prompts (all editable)": judge, report summary, root-cause attribution, pack drafting,
gold drafting, gold revision (6 in total). Each shows its purpose and slot meaning (%s
placeholders). Overrides are saved per user; clearing a text restores the builtin default,
and a broken custom template automatically falls back to the default (it can never break a
run). Domain-specific scoring preferences can therefore be encoded without code changes.

Iron rules: the AI never invents gold (it only structures material/dataset answers, human
confirmed); it cannot pass safety P0 signals; it cannot overturn hard-FAILed numerics.
Without AI configured the platform is fully usable — everything is deterministic.
