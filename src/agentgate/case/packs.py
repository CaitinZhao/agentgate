"""Domain pack registry (doc 35 §4.2): declarative JSON, versioned in the repo.

A pack states how a *domain* is judged — environment requirements (W7 handshake),
evidence/caliber requirements, bank-level red lines, judge hints and per-type
defaults. Cases inherit pack config at run time; the author never repeats it.

Packs resolve from (first match wins):
  1. data dir `packs/` (web platform user packs — LLM drafts reviewed by humans)
  2. the builtin directory shipped with the package (`case/packs/*.json`)
"""
import json
from pathlib import Path
from typing import Dict, Optional

_BUILTIN_DIR = Path(__file__).resolve().parent / "packs"

# name-prefix -> default pack for banks created without an explicit pack
PREFIX_PACK = {
    "fb": "fb", "bank": "bank", "locomo": "locomo", "longmem": "longmem",
    "tau": "tau", "injection": "injection", "plat-smoke": "bank", "core": "bank",
    "bfcl": "bfcl", "spider": "spider", "gaia": "gaia", "airbench": "airbench",
    "agentdojo": "agentdojo", "harmbench": "harmbench",
}


def _read(path: Path) -> Optional[Dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def data_packs_dir(data_root: Path) -> Path:
    return Path(data_root) / "packs"


def load_pack(pack_id: str, data_root: Path = None) -> Optional[Dict]:
    """Resolve one pack by id; None = unknown (the caller falls back to generic)."""
    if not pack_id:
        return None
    if data_root is not None:
        custom = _read(data_packs_dir(data_root) / ("%s.json" % pack_id))
        if custom:
            return custom
    return _read(_BUILTIN_DIR / ("%s.json" % pack_id))


def list_packs(data_root: Path = None) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    if data_root is not None:
        d = data_packs_dir(data_root)
        if d.is_dir():
            for f in sorted(d.glob("*.json")):
                p = _read(f)
                if p and p.get("pack_id"):
                    out[p["pack_id"]] = p
    if _BUILTIN_DIR.is_dir():
        for f in sorted(_BUILTIN_DIR.glob("*.json")):
            p = _read(f)
            if p and p.get("pack_id") and p["pack_id"] not in out:
                out[p["pack_id"]] = p
    return out


def pack_for_bank(bank_name: str, requirements: Dict, data_root: Path = None) -> str:
    """The pack a bank inherits: explicit requirements.pack > name-prefix mapping > generic."""
    pack = (requirements or {}).get("pack") or ""
    if pack:
        return pack
    name = (bank_name or "").lower()
    for prefix, pid in sorted(PREFIX_PACK.items(), key=lambda kv: -len(kv[0])):
        if name.startswith(prefix):
            return pid
    return "generic"


def resolve_pack(case, bank_pack: str = "", data_root: Path = None) -> Dict:
    """Effective pack for one case: the case's own pack wins, then the bank's, then generic."""
    p = load_pack(case.pack or bank_pack or "generic", data_root) \
        or load_pack("generic", data_root) or {}
    return p


def save_pack(pack: Dict, data_root: Path) -> Path:
    """Persist a user pack (LLM draft after human review, or manual edit) into the data dir."""
    d = data_packs_dir(data_root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / ("%s.json" % pack.get("pack_id", "custom"))
    path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


DEFAULT_GENERIC = {
    "pack_id": "generic", "version": "1.0.0", "profile": "",
    "materials": "", "tool_strict": True, "evidence_required": False,
    "caliber_required": False,
    "red_lines": {"forbidden_tools": [], "forbidden_answer_regex": []},
    "judge_hints": [], "types": {}, "authored_by": "human", "source": "builtin-template",
}
