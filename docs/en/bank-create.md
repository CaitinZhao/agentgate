<div align="right">

English（this page） | [简体中文](../zh/bank-create.md)

</div>

# Create a Bank

Wizard (Banks page -> New benchmark):

| Step | Fields |
|---|---|
| ① Basics | name (lowercase/digits/hyphens = the bank id), display name, visibility (public = admin-only to create/manage; private = creator & admins), category, default level, descriptions |
| ② Authoring mode | empty bank (add cases in the detail page) / Excel batch (template -> fill -> upload, red-marked error loop) / external dataset (autoadapt auto-bucketing) |

Three ways to add cases: online single-case form ([Edit a bank](bank-edit.md)); Excel
round-trip; autoadapt for jsonl/csv datasets (auto-bucketing into gold v2 + a human review
checklist). Open-benchmark banks come from dataset regeneration — see
[Open-source banks](public-banks.md).

AI drafting (optional, per-user AI config): "AI-draft gold" in the case form detects the case
type and extracts candidate answer/checkpoints from your material — the AI never invents gold,
review before saving. "AI-draft domain pack" drafts the pack JSON for a new domain; red lines
and gold-like content require human review, and saving signs it `llm-draft+human-reviewed`.
Config & boundaries: [AI assist](ai-assist.md).

Domain pack (bank-level judging config inherited by all cases): profile & materials,
evidence/caliber requirements, red lines, judge hints, per-type defaults. Built-ins:
bank/fb/locomo/longmem/tau/injection/generic, auto-matched by name prefix, editable in the
bank's pack card. Consumption rules: [Scoring](scoring.md).
