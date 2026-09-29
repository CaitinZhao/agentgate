"""SQLite case bank (v2 schema): lightweight persistence for case data.

v2 keeps per-case config minimal (doc 35 §4): type / pack / gold. There is no
read path for the pre-v2 `expected` schema — banks are created/converted by the
generators and the platform UI, never carried over as legacy baggage.
"""
import json

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .models import Case

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  case_id        TEXT PRIMARY KEY,
  suite          TEXT,
  level          TEXT DEFAULT 'L2',
  status         TEXT DEFAULT 'active',
  as_of          TEXT,
  source         TEXT,
  input          TEXT,
  type           TEXT DEFAULT 'auto',
  pack           TEXT DEFAULT '',
  gold           TEXT,
  diagnosis_hint TEXT,
  created_at     TEXT,
  updated_at     TEXT
)
"""

_COLUMNS = ["case_id", "suite", "level", "status", "as_of", "source", "input",
            "type", "pack", "gold", "diagnosis_hint", "created_at", "updated_at"]


def _connect(db: str):
    import sqlite3
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute(SCHEMA)
    return con


def _row_to_case(row) -> Case:
    r = dict(zip(_COLUMNS, row))
    try:
        gold = json.loads(r["gold"] or "{}")
    except ValueError:
        gold = {}
    return Case(case_id=r["case_id"], suite=r["suite"], level=r["level"] or "L2",
                status=r["status"] or "active", as_of=r["as_of"],
                source=json.loads(r["source"] or "{}"),
                input=json.loads(r["input"] or "{}"),
                type=r["type"] or "auto", pack=r["pack"] or "",
                gold=gold, diagnosis_hint=json.loads(r["diagnosis_hint"] or "{}"))


def upsert_cases(db: str, cases: List[Case]) -> int:
    """Insert/update incrementally by case_id (created_at preserved, updated_at refreshed)."""
    con = _connect(db)
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for c in cases:
        d = json.loads(c.model_dump_json())
        con.execute(
            "INSERT INTO cases (%s) VALUES (%s) "
            "ON CONFLICT(case_id) DO UPDATE SET suite=excluded.suite, level=excluded.level, "
            "status=excluded.status, as_of=excluded.as_of, source=excluded.source, "
            "input=excluded.input, type=excluded.type, pack=excluded.pack, "
            "gold=excluded.gold, diagnosis_hint=excluded.diagnosis_hint, "
            "updated_at=excluded.updated_at"
            % (",".join(_COLUMNS), ",".join("?" * len(_COLUMNS))),
            (c.case_id, c.suite, c.level, c.status, c.as_of,
             json.dumps(d["source"], ensure_ascii=False),
             json.dumps(c.input, ensure_ascii=False),
             c.type, c.pack,
             json.dumps({"final": c.gold.final,
                         "checkpoints": [cp.model_dump() for cp in c.gold.checkpoints],
                         "rubric": [rp.model_dump() for rp in c.gold.rubric]},
                        ensure_ascii=False),
             json.dumps(d["diagnosis_hint"], ensure_ascii=False), now, now))
        n += 1
    con.commit()
    con.close()
    return n


def load_cases_db(db: str, suite: Optional[str] = None, level: Optional[str] = None,
                  status: Optional[str] = "active") -> List[Case]:
    con = _connect(db)
    q, args = "SELECT %s FROM cases WHERE 1=1" % ",".join(_COLUMNS), []
    if status:
        q += " AND status = ?"
        args.append(status)
    if suite:
        q += " AND suite = ?"
        args.append(suite)
    if level:
        q += " AND level = ?"
        args.append(level)
    rows = con.execute(q + " ORDER BY case_id", args).fetchall()
    con.close()
    return [_row_to_case(r) for r in rows]


def update_levels(db: str, mapping: Dict[str, str]) -> int:
    """Adjust case levels in batch: mapping = {case_id: level} (level in L0/L1/L2)."""
    con = _connect(db)
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for cid, level in mapping.items():
        cur = con.execute("UPDATE cases SET level=?, updated_at=? WHERE case_id=?",
                          (level, now, cid))
        n += cur.rowcount
    con.commit()
    con.close()
    return n


def update_status(db: str, mapping: Dict[str, str]) -> int:
    """Adjust case statuses in batch: mapping = {case_id: active|retired} (bank growth & retirement)."""
    con = _connect(db)
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for cid, status in mapping.items():
        cur = con.execute("UPDATE cases SET status=?, updated_at=? WHERE case_id=?",
                          (status, now, cid))
        n += cur.rowcount
    con.commit()
    con.close()
    return n


def stats_by_suite(db: str) -> List[Dict]:
    con = _connect(db)
    rows = con.execute("SELECT suite, COUNT(*) FROM cases WHERE status='active' "
                       "GROUP BY suite").fetchall()
    con.close()
    return [{"suite": s, "count": n} for s, n in rows]


def stats(db: str) -> Dict:
    con = _connect(db)
    rows = con.execute("SELECT suite, level, status, COUNT(*) FROM cases "
                       "GROUP BY suite, level, status").fetchall()
    con.close()
    return [{"suite": s, "level": l, "status": st, "count": n} for s, l, st, n in rows]


def export_jsonl(path: str, cases: List[Case]) -> int:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8", newline=chr(10)) as fh:
        for c in cases:
            fh.write(c.model_dump_json() + chr(10))
    return len(cases)
