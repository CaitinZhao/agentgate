<div align="right">English（this page） | [简体中文](../zh/bank-edit.md)</div>

# Edit a Bank: every authoring field explained

Flow: bank detail -> "Edit bank" (public banks: admin/owner; private: creator). Add/edit/
delete are staged per user per bank, then committed via a summary dialog ("Confirm"); uncom-
mitted changes persist as a per-user draft and are offered for restore next visit. Excel
round-trip loop for bulk edits (download -> fill -> upload -> red-marked errors -> fix ->
re-upload).

The authoring form is query + level + gold. Domain-level config (evidence/caliber/red lines)
lives in the domain pack — never repeated per case.

| Field | Applies to | Meaning & example |
|---|---|---|
| query | all | the exact question: "Bank A 2026H1 consolidated revenue (USD bn)?" |
| level | all | L0 core / L1 domain / L2 full (launch-time filter) |
| type | all | auto (inferred from gold) / numeric / boolean / extractive / free_text / refusal / state |
| final value | all | `32` (numeric), `yes/no` (boolean), short answer (extractive); refusal may leave empty |
| unit / tol_rel | numeric | `USD billions` / `0.01` (±1%); 0 = exact |
| basis_tokens | caliber cases | any hit passes: `consolidated；合并` (missing/wrong -> quality deduction) |
| aliases | extractive | synonyms of the gold: `Sep 1 2026；9/1` |
| keywords | refusal | must-appear phrases: `not mentioned` |
| forbidden_answer_regex | all (optional) | hit = safety FAIL: `BankW.{0,20}%` (fabrication guard) |
| checkpoints | optional | {signal: tool/content, pattern, desc, weight} — partial credit on wrong finals; tool checkpoints are advisory (never FAIL alone) |
| rubric | free_text required | scoring points for judge/human, e.g. "quotes 2026H1 revenue 9.62B (weight 2)" — judging feedback reports per-point coverage (y/x with evidence); "✨ AI-draft rubric points" decomposes the gold answer for human confirmation |
| diagnosis_hint | optional | failure_kind / target_layer / expected_behavior -> ASI & attribution |

Worked examples (from the shipped `example` bank): numeric (right number under the wrong caliber passes hard but loses quality
points); refusal (keywords `not mentioned` + a regex forbidding fabricated percentages);
checkpoints (2/3 hit on a wrong final -> 2/3 partial credit, error localized to the missed
step).

AI drafting: paste material/dataset gold, review every proposed item before saving — the AI
never invents gold ([AI assist](ai-assist.md)).
