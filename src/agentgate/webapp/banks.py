"""Bank storage under the data root (doc 33 §3): one SQLite per bank, split by name.

  data/banks/public/<bank>/cases.db             public banks (admin managed)
  data/banks/users/<username>/<bank>/cases.db   private banks (per user)

Case rows use the same schema as case/store.py (the suite column = bank name). The platform
database only holds the registry (benchmarks table) and per-user overrides — distribution and
success stats are aggregated live from banks + run history, no denormalized tables.
"""
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from ..case.models import Case
from ..case.store import _connect, load_cases_db, upsert_cases
from . import db

BANK_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")


def validate_bank_name(name: str):
    if not BANK_NAME_RE.match(name or ""):
        raise ValueError("bank name must match ^[a-z][a-z0-9-]{1,39}$ (lowercase letters, "
                         "digits, hyphens; starts with a letter), got: %r" % name)


def bank_db_path(bank: Dict, owner_username: str = "") -> Path:
    """cases.db path for one bank row; private banks nest under the owner's username."""
    root = db.data_root() / "banks"
    if bank["visibility"] == "public":
        return root / "public" / bank["name"] / "cases.db"
    return root / "users" / (owner_username or "unknown") / bank["name"] / "cases.db"


def create_bank_db(path: Path) -> Path:
    """Create an empty case bank with the standard schema (idempotent)."""
    _connect(str(path)).close()
    return path


def bank_level_distribution(db_path: Path) -> Dict[str, int]:
    """Active-case level distribution read live from the bank (L0/L1/L2)."""
    import sqlite3
    if not Path(db_path).exists():
        return {}
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute("SELECT level, COUNT(*) FROM cases WHERE status='active' "
                           "GROUP BY level").fetchall()
        return {lvl: n for lvl, n in rows}
    finally:
        con.close()


def load_bank_cases(db_path: Path, include_retired: bool = False) -> List[Case]:
    return load_cases_db(str(db_path), status=None if include_retired else "active")


# -- fairness contract: default per-bank requirements (profile / tool_strict / materials) ----
# W7 methodology fix: banks that depend on a provisioned environment must SAY SO, and agents
# that cannot meet the requirement get their cases SKIPPED (never FAILed on missing tools).

_DEFAULT_REQUIREMENTS = {
    "fb": {"profile": "fb", "tool_strict": False, "pack": "fb",
           "materials": "FinanceBench 真实财报 PDF 语料（84 份），由 fb 剖面工具提供："
                        "list_filings / search_filing / calculate"},
    "locomo": {"profile": "locomo", "tool_strict": False, "pack": "locomo",
               "materials": "LoCoMo 多人长对话文本，由评测侧展开注入 /invoke 的 context 字段"},
    "longmem": {"profile": "longmem", "tool_strict": False, "pack": "longmem",
                "materials": "LongMemEval 长对话文本，由评测侧展开注入 /invoke 的 context 字段"},
}


def default_requirements(name: str) -> Dict:
    for prefix, req in _DEFAULT_REQUIREMENTS.items():
        if name.startswith(prefix):
            return dict(req)
    if name.startswith("tau-"):
        return {"profile": name, "tool_strict": True, "pack": "tau",
                "materials": "tau-bench 领域数据与政策 wiki，随剖面工具提供"}
    req = {"profile": "bank", "tool_strict": True,
           "materials": "银行财报 mock 数据，随 bank 剖面工具提供"
                        "（retrieve_report / revision_check / calculate）"}
    if name.startswith("injection"):
        req["pack"] = "injection"
    return req


def backfill_requirements():
    """One-time backfill at startup: banks without a requirements contract get the default
    for their name prefix; banks without a pack assignment get their prefix pack added
    (existing values are never overwritten)."""
    import json as _json
    from ..case.packs import pack_for_bank
    for bank in db.list_benchmarks():
        raw = (bank.get("requirements") or "").strip()
        if not raw:
            db.update_benchmark(bank["name"],
                                requirements=_json.dumps(default_requirements(bank["name"]),
                                                         ensure_ascii=False))
            continue
        try:
            req = _json.loads(raw)
        except ValueError:
            continue
        if isinstance(req, dict) and not req.get("pack"):
            req["pack"] = pack_for_bank(bank["name"], {})
            db.update_benchmark(bank["name"],
                                requirements=_json.dumps(req, ensure_ascii=False))
