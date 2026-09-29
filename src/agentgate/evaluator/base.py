"""Verifier result models: L1 deterministic -> L2 rules (LLM judge is L3, deferred)."""
from typing import List, Optional

from pydantic import BaseModel


class VerifierResult(BaseModel):
    ok: bool
    layer: str = "L1"                      # L1 / L2
    name: str = ""
    reason: str = ""                       # human-readable
    asi: str = ""                          # mandatory on failure: by how much / which step / why
    failed_step: Optional[str] = None      # trace localization (step:N / tool:N)
    trace_linked: bool = True


def aggregate(fails: List[VerifierResult]) -> str:
    """Aggregate the failed items' ASI (fuel for the evolution engine)."""
    if not fails:
        return ""
    return chr(10).join("[%s] %s" % (f.name, f.asi or f.reason) for f in fails)
