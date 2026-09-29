"""Case data models v2 (doc 35 §4): type/gold/pack skeleton replaces the old
ten-field `expected` model. No compatibility layer — the store migrates once.

The author fills four things: query (input), level, and gold (final answer +
optional checkpoints / rubric). `type` may be left "auto" — the judging pipeline
infers it from the gold shape (LLM auto-typing refines this later, 35 §4.1.1).
Domain-pack defaults (evidence/caliber/red lines/judge hints) are inherited at
run time from the pack registry — per-case fields stay minimal.
"""
from typing import List, Optional

from pydantic import BaseModel, Field

CASE_TYPES = ("numeric", "boolean", "extractive", "free_text", "refusal", "state")


class Checkpoint(BaseModel):
    """One solution checkpoint (中间步骤): how the judging system credits partial work.

    signal=tool   -> `pattern` must appear among the tools/args the agent used
    signal=content-> `pattern` must appear in the answer (intermediate value/statement)
    signal=state  -> reserved for environment-state assertions (no state env yet)
    """

    desc: str = ""
    signal: str = "content"                # tool / content / state
    pattern: str = ""
    weight: float = 1.0


class RubricPoint(BaseModel):
    """One scoring point for free_text cases — the rubric consumed by the (soft) judge."""

    point: str
    weight: float = 1.0


class Gold(BaseModel):
    """gold v2: final answer + solution checkpoints + scoring rubric (35 §4.1)."""

    final: dict = Field(default_factory=dict)
    checkpoints: List[Checkpoint] = Field(default_factory=list)
    rubric: List[RubricPoint] = Field(default_factory=list)


class DiagnosisHint(BaseModel):
    """Attribution hint when the case fails (from the case author's business criteria;
    feeds ASI and attribution)."""

    failure_kind: str = "unknown"
    target_layer: str = "domain"
    expected_behavior: str = ""


class CaseSource(BaseModel):
    """Provenance of a case."""

    origin: str = "bank_archive"          # bank_archive / public_benchmark / trace_mined
    seed: Optional[str] = None            # seed ID of the public benchmark case
    adaptation: Optional[str] = None      # domain adaptation notes
    provenance: Optional[str] = None


def infer_type(case: "Case") -> str:
    """Infer the case type from the gold shape when the author left it auto/blank (35 §4.1.1).

    numeric/boolean/extractive/refusal come from gold.final's shape; free_text wins when a
    rubric exists (safest default — falls to the soft judge / human review)."""
    if case.type and case.type in CASE_TYPES and case.type != "free_text":
        return case.type
    f = case.gold.final or {}
    if f.get("value") is not None or f.get("tol_rel") is not None:
        v = f.get("value")
        if isinstance(v, str) and v.strip().lower() in ("yes", "no", "true", "false"):
            return "boolean"
        if isinstance(v, (int, float)) or (isinstance(v, str) and
                                           v.strip().replace("-", "", 1).replace(".", "", 1).isdigit()):
            return "numeric"
        return "extractive"
    if case.gold.rubric:
        return "free_text"
    if f.get("keywords"):
        return "refusal"
    return "free_text"


class Case(BaseModel):
    """One case in the bank (v2)."""

    case_id: str
    version: int = 1
    suite: Optional[str] = None             # suite name (inferred from the case_id prefix when omitted)
    eval_set_version: Optional[str] = None  # eval set version (defaults to <suite>-v0.1)
    status: str = "active"                  # active / retired (bank growth & retirement)
    level: str = "L2"                       # level: L0 core / L1 domain / L2 full (default L2)
    as_of: Optional[str] = None             # time gate: mandatory for time-sensitive cases
    source: CaseSource = Field(default_factory=CaseSource)
    input: dict = Field(default_factory=dict)
    type: str = "auto"                      # auto / numeric / boolean / extractive / free_text / refusal / state
    pack: str = ""                          # domain pack id ("" = inherit from the bank/pack registry)
    gold: Gold = Field(default_factory=Gold)
    diagnosis_hint: DiagnosisHint = Field(default_factory=DiagnosisHint)

    def effective_type(self) -> str:
        return infer_type(self)


