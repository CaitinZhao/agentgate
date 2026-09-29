"""Case loader: reads case definitions from a directory or file.

Standard format: cases.jsonl (one case per line); also reads single-case YAML/JSON
and JSON arrays (legacy formats).
"""
import json
from pathlib import Path
from typing import List, Union

import yaml

from .models import Case


def _parse_case_file(f: Path) -> List[Case]:
    text = f.read_text(encoding="utf-8")
    if f.suffix == ".jsonl":
        return [Case(**json.loads(line)) for line in text.splitlines() if line.strip()]
    data = yaml.safe_load(text) if f.suffix in (".yaml", ".yml") else json.loads(text)
    if isinstance(data, list):
        return [Case(**item) for item in data]
    if f.suffix == ".json":
        return []            # dict-shaped json (e.g. smoke script templates) is not a case file, skip
    return [Case(**data)]


def load_cases(path: Union[str, Path], status: str = "active") -> List[Case]:
    p = Path(path)
    if p.suffix == ".db" or p.name.endswith(".db"):
        from .store import load_cases_db
        return load_cases_db(str(p), status=status)
    if p.is_file():
        files = [p]
    elif p.is_dir():
        files = sorted(list(p.glob("*.jsonl")) + list(p.glob("*.yaml"))
                       + list(p.glob("*.yml")) + list(p.glob("*.json")))
    else:
        raise FileNotFoundError("case path not found: %s" % p)
    cases: List[Case] = []
    for f in files:
        cases.extend(_parse_case_file(f))
    cases.sort(key=lambda c: c.case_id)
    return cases
