"""Memory-profile context expansion (LoCoMo-style datasets; offline, no LLM)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentgate.case.models import Case
from agentgate.control.service import _expand_context


def _case(ctx_file):
    return Case(case_id="t-ctx-1", suite="locomo",
                input={"query": "q?", "profile": "locomo", "context_file": ctx_file})


def test_expand_context_resolves_relative_to_cases_root(tmp_path):
    """context_file resolves relative to the cases root; after expansion the reference key is replaced by the dialogue text."""
    ctx_dir = tmp_path / "locomo" / "contexts"
    ctx_dir.mkdir(parents=True)
    (ctx_dir / "s0.json").write_text(
        json.dumps({"dialogue": "[A] hi\n[B] hello"}, ensure_ascii=False),
        encoding="utf-8")
    cases = [_case("locomo/contexts/s0.json")]
    n = _expand_context(cases, [tmp_path])
    assert n == 1
    assert cases[0].input["context"] == "[A] hi\n[B] hello"
    assert "context_file" not in cases[0].input


def test_expand_context_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        _expand_context([_case("locomo/contexts/nope.json")], [Path("cases"), Path(".")])
