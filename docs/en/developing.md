<div align="right">

English（this page） | [简体中文](../zh/developing.md)

</div>

# Testing Guide (developers)

Layers: unit/pipeline tests (`python -m pytest tests/ -q --ignore=tests/test_jiuwen_agent.py`
**from the agentgate/ directory** — judging pipeline, six-dimension scorer, gold v2, Web API
end-to-end via TestClient + MockTarget, Excel round-trip); offline smoke (`agentgate smoke` —
no network, no LLM: scripted answers + spans -> judge -> gate); real-chain smoke
(`deploy/smoke_jiuwen.py` against the running container); visual review (`deploy/review_ui.py`
sends page screenshots to a vision model).

Design conventions:

1. No network or real model in tests — `MockTarget` (scripted responses + spans,
   `run/targets/base.py`) with `worker.make_target` monkeypatched; judging/scoring are pure
   functions tested with constructed cases.
2. Per-test data isolation — `AGENTGATE_DATA_DIR` points at a tmp dir (the `platform()`
   fixture); platform db, banks and results all live under tmp.
3. Fixtures double as documentation — the sample agent (`tests/fixtures/jiuwen_server.py` +
   `fin_runtime/`) is the integration reference ([Agent integration](agent-integration.md));
   change the /invoke contract there first.
4. Derived banks are generated — generators (`case/build_banks.py`, public_benchmarks
   adapters) are tested; generated artifacts are not committed.

Quick runs:

```bash
python -m pytest tests/ -q --ignore=tests/test_jiuwen_agent.py   # all (~30s)
python -m pytest tests/test_doc35.py -q                          # judging/scoring/gold v2
python -m pytest tests/test_webapp.py -q                         # web API chains
```

| You changed | See / extend |
|---|---|
| judging rules (red lines / types / checkpoints) | tests/test_doc35.py::test_judging_by_type |
| scoring weights/tiers/caps | tests/test_doc35.py::test_scores_units |
| web API (roles / banks / run lifecycle) | tests/test_webapp.py |
| AI enhancements | tests/test_doc35.py::test_ai_settings_and_pack_endpoints (fake config, no real calls) |
| stability repeat_k | tests/test_doc35.py::test_stability_repeat_k |
| Excel round-trip | tests/test_webapp_content.py::test_excel_roundtrip |
| deploy scripts | no unit tests; at least syntax-check after editing |

New-feature requirements: deterministic changes need unit tests (seconds, no network);
/invoke-contract, span-convention and domain-pack changes must update the sample fixture and
agent-contracts schemas; bilingual report/UI changes assert both languages.
