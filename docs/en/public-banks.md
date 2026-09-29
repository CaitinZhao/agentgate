<div align="right">English（this page） | [简体中文](../zh/public-banks.md)</div>

# Open-source banks: download & regenerate

This page lists the platform's open-source benchmark banks, how to fetch each dataset and
regenerate the bank, how the NATIVE reading (the second, paper-comparable number) is
computed, and the contamination conventions. For self-authored banks see
[Bank creation](bank-create.md).

## Which open-source banks ship

| Bank | Dataset | What it tests | Platform reading | Native reading |
|---|---|---|---|---|
| fb (150) | FinanceBench | real-report retrieval, unit conversion, refusal | typed hard checks + evidence (fb pack) + rubric | typed hard check + judge suggestion |
| bfcl (529) | BFCL v4 (gorilla, Apache-2.0) | function calling: single / parallel / multiple / irrelevance | native AST-equivalent match (also the platform typed check) | AST-equivalent accuracy |
| spider (654) | Spider dev, 8 DBs (CC BY-SA 4.0) | text-to-SQL (sql_query tool + schema) | execution match (gold vs agent SQL result sets) | execution accuracy |
| gaia (127) | GAIA validation text-only subset (Apache-2.0) | general assistant (reasoning / calc; web gaps surface honestly) | official EM normalization | exact match |
| airbench (120) | AIR-Bench 24.05 qa/wiki/en dev (CC BY-NC-SA 4.0) | retrieval (doc_search tool over a subset corpus) | nDCG@10 ≥ 0.5 counts as typed PASS | nDCG@10 / recall@5 |
| agentdojo (391) | AgentDojo v1_2_2 banking+workspace (MIT) | tool tasks + injection attacks (direct template) | the suite's own utility/security conditions (official code) | utility / security dual metric |
| harmbench (200) | HarmBench standard behaviors (Apache-2.0) | safety red team (compliance rate) | refusal patterns + judge suggestion → PENDING/human | ASR (attack success rate, lower is better) |
| locomo (1986) | LoCoMo | long-conversation memory QA, adversarial refusal | numeric + refusal + rubric | typed hard check + judge suggestion |
| longmem (500) | LongMemEval oracle | cross-session memory | numeric + rubric | typed hard check + judge suggestion |
| tau-airline / tau-retail | tau-bench | tool orchestration & policy | tool checkpoints (partial credit) + judge/human | typed hard check + judge suggestion |
| fineval (42) | FinEval | domain knowledge | numeric + judge/human | typed hard check + judge suggestion |
| injection (6) | self-authored corpus | injection resistance | safety red lines + rubric | typed hard check + judge suggestion |

## Dual reading: platform score vs native score

Every run reports two readings of the same data:

1. **Platform reading** (six-dimension radar): this suite's own methodology — success /
   quality / reliability / stability / efficiency / cost / safety — uniform across ALL
   banks, comparable bank-to-bank.
2. **Native reading** (the report's "Native reading" section + the run-detail card): each
   dataset's own metric — BFCL AST match, Spider execution accuracy, GAIA official EM,
   AIR-Bench nDCG@10, AgentDojo official utility/security conditions, HarmBench ASR — so
   numbers align with papers and other harnesses.

Both readings are **observation-only**: public questions may already be inside the
evaluated model's training data, so neither feeds a release-accept gate. Known deviations
are recorded in each bank README and per-case adaptation notes (e.g. the AIR-Bench subset
corpus; HarmBench judged by the user's LLM rather than their Llama-13B classifier).

## Fetch datasets & regenerate

Derived banks are **not in git** (third-party dataset content). Put the datasets under
`reference/refs/` (`code/` and `data/`), then:

| Dataset | Source | Command |
|---|---|---|
| FinanceBench | github.com/ParetoIntel/financebench | `python -m agentgate.case.build_banks --fb` |
| BFCL v4 | github.com/ShishirPatil/gorilla (bfcl_eval/data) | `python -m agentgate.case.build_banks --bfcl` |
| Spider | HF `HAL-9001/spider-databases` (spider_data.zip) or official dev package | `python -m agentgate.case.build_banks --spider` |
| GAIA | HF `gaia-benchmark/GAIA` (gated; text-only mirror parquet) | `python -m agentgate.case.build_banks --gaia` |
| AIR-Bench | HF `AIR-Bench/qa_wiki_en` + `AIR-Bench/qrels-qa_wiki_en-dev` | `python -m agentgate.case.build_banks --airbench` |
| AgentDojo | github.com/ethz-spylab/agentdojo | `python -m agentgate.case.build_banks --agentdojo` |
| HarmBench | github.com/centerforaisafety/harmbench | `python -m agentgate.case.build_banks --harmbench` |
| LoCoMo | github.com/snap-research/locomo (data/locomo10.json) | `--locomo --locomo-data <path>` |
| LongMemEval | github.com/xiaowu0162/LongMemEval (oracle) | `--longmem --longmem-data <path>` |
| τ-bench | github.com/sierra-research/tau-bench | `--tau --tau-repo <path>` |
| FinEval | any question/answer dataset (jsonl/csv) | the "external dataset" UI or `agentgate autoadapt` |

Sampling / subset conventions (reproducible; seed and counts recorded in each bank README):
BFCL seeded stratified sample (per-category caps, seed=42); Spider takes ALL questions of 8
dev DBs (wta_1 excluded — its SQLite alone is 105MB); AIR-Bench corpus = qrels documents +
5,000 randomly padded passages; GAIA = the full 127-question validation text-only subset.

**Tool-dependency grading (env_scope)**: `agent-side` (default) = the agent provides the
environment; banks whose profile the agent does not declare are SKIPPED (fair handshake).
`judge-only` = the assets exist only for SCORING (e.g. Spider's SQLite) — the agent may
answer by any means; a missing profile merely degrades the profile prompt (Spider ships
this way).

**Deployment note**: the Spider SQLite files and the AIR-Bench corpus subset are
**baked into the agent image** (`Dockerfile.agent` COPYs `cases/spider/dbs` and
`cases/airbench/corpus.jsonl`) — run `build_banks` BEFORE building the image. The `fb`
profile additionally needs the PDF corpus mounted at runtime (`deploy/fb_pdfs/README.md`);
the `agentdojo` profiles are provided by the official agentdojo pip package inside the
agent container (environment + conditions).

Capability handshake: banks declare their required profile via the domain pack; agents that
do not declare it get those cases SKIPPED (never mis-judged).

Contamination guard: open-benchmark scores (both readings) are for cross-agent observation
and trends only — never a release-accept criterion. Provenance (dataset seed, adaptation
notes) is kept on every case and visible in the bank detail page.
