"""Platform database (data/agentgate.db): users / sessions / benchmarks / case overrides /
runs / run_items / settings (doc 33 §5).

SQLite in WAL mode; every helper opens a short-lived connection so the REST API threads and
the serial worker thread can share the file safely. All rows are returned as plain dicts.
"""
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from ..config import load_config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL DEFAULT 'viewer',   -- owner | admin | member | viewer
  display_name  TEXT DEFAULT '',
  created_at    TEXT,
  last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL,
  created_at TEXT,
  expires_at TEXT
);
CREATE TABLE IF NOT EXISTS benchmarks (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  name           TEXT UNIQUE NOT NULL,            -- bank name (also the banks/ directory name)
  display_name   TEXT DEFAULT '',
  description_zh TEXT DEFAULT '',
  description_en TEXT DEFAULT '',
  source_note    TEXT DEFAULT '',
  category       TEXT DEFAULT '',                 -- public-bank grouping (finance/general/security...)
  default_level  TEXT DEFAULT 'L2',
  visibility     TEXT NOT NULL DEFAULT 'public',  -- public | private
  status         TEXT NOT NULL DEFAULT 'online',  -- online | offline (public banks only)
  owner_id       INTEGER,                         -- private: creator; public: the admin who created it
  created_at     TEXT
);
CREATE TABLE IF NOT EXISTS user_case_overrides (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL,
  bank_id    INTEGER NOT NULL,
  case_id    TEXT NOT NULL,
  level      TEXT,                                -- NULL = follow the bank default
  enabled    INTEGER NOT NULL DEFAULT 1,          -- 0 = removed from my runs (others unaffected)
  updated_at TEXT,
  UNIQUE(user_id, bank_id, case_id)
);
CREATE TABLE IF NOT EXISTS runs (
  id              TEXT PRIMARY KEY,               -- run-<ts>-<rand>; also the results/ dir name
  name            TEXT NOT NULL,                  -- unique task name = user input + timestamp suffix
  user_name       TEXT DEFAULT '',                -- the business name the user typed (searchable)
  created_by      INTEGER,
  created_at      TEXT,
  status          TEXT NOT NULL DEFAULT 'queued', -- queued|running|succeeded|failed|cancelled
  scheduled_for   TEXT,                           -- NULL = immediate
  bank_filter     TEXT DEFAULT '[]',              -- JSON [{"bank": name, "levels": [..]}]
  case_ids        TEXT,                           -- JSON list or NULL
  target_url      TEXT DEFAULT '',
  proxy_enabled   INTEGER NOT NULL DEFAULT 0,
  ai_assist       INTEGER NOT NULL DEFAULT 1,
  receiver_port   INTEGER DEFAULT 4318,
  total_cases     INTEGER DEFAULT 0,
  done_cases      INTEGER DEFAULT 0,
  est_duration_s  INTEGER,
  started_at      TEXT,
  finished_at     TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  gate_decision   TEXT,
  score           TEXT,
  result_dir      TEXT,
  error           TEXT
);
CREATE TABLE IF NOT EXISTS run_items (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      TEXT NOT NULL,
  case_id     TEXT NOT NULL,
  bank        TEXT DEFAULT '',
  level       TEXT DEFAULT '',
  verdict      TEXT DEFAULT 'PENDING',            -- PASS | FAIL | PENDING
  tokens      INTEGER DEFAULT 0,
  wall_time_s REAL DEFAULT 0,
  asi         TEXT DEFAULT '',
  created_at  TEXT,
  UNIQUE(run_id, case_id)
);
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS edit_drafts (
  user_id    INTEGER NOT NULL,
  bank_id    INTEGER NOT NULL,
  draft      TEXT NOT NULL,                  -- JSON {new_cases:[], edits:{}, deletes:[]}
  updated_at TEXT,
  UNIQUE(user_id, bank_id)
)
"""

DEFAULT_SETTINGS = {
    "report_retention_days": "30",
    "registration_open": "true",
    "proxy_upstream": "",            # real LLM gateway base_url (OpenAI-compatible); admin configures
    "proxy_agent_url": "",           # proxy address as seen FROM the agent container (llm_base_url delivery)
    "proxy_port": "8300",
    "receiver_port": "4318",
}


def data_root() -> Path:
    """Data root: AGENTGATE_DATA_DIR env > agentgate.json data_dir > ./data (always outside src)."""
    env = os.environ.get("AGENTGATE_DATA_DIR", "")
    if env:
        return Path(env)
    return Path(load_config().get("data_dir", "data"))


def platform_db() -> Path:
    return data_root() / "agentgate.db"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(db: Optional[str] = None) -> sqlite3.Connection:
    p = Path(db) if db else platform_db()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    _migrate(con)
    for k, v in DEFAULT_SETTINGS.items():
        con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    con.commit()
    return con


def _migrate(con: sqlite3.Connection):
    """Idempotent column additions for pre-W7 databases."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(benchmarks)")}
    if "requirements" not in cols:
        con.execute("ALTER TABLE benchmarks ADD COLUMN requirements TEXT DEFAULT ''")
    run_cols = {r[1] for r in con.execute("PRAGMA table_info(runs)")}
    if run_cols and "stability_k" not in run_cols:
        con.execute("ALTER TABLE runs ADD COLUMN stability_k INTEGER DEFAULT 1")
    if run_cols and "ai_assist" not in run_cols:
        con.execute("ALTER TABLE runs ADD COLUMN ai_assist INTEGER NOT NULL DEFAULT 1")
    if run_cols and "final_gate" not in run_cols:
        con.execute("ALTER TABLE runs ADD COLUMN final_gate TEXT")
    it = {r[1] for r in con.execute("PRAGMA table_info(run_items)")}
    for col in ("final_verdict", "reviewed_by", "review_note"):
        if it and col not in it:
            con.execute("ALTER TABLE run_items ADD COLUMN %s TEXT" % col)
    con.execute("""CREATE TABLE IF NOT EXISTS user_settings (
      user_id INTEGER NOT NULL,
      key     TEXT NOT NULL,
      value   TEXT,
      UNIQUE(user_id, key)
    )""")


def _rows(con, q: str, args: tuple = ()) -> List[Dict]:
    return [dict(r) for r in con.execute(q, args).fetchall()]


def _row(con, q: str, args: tuple = ()) -> Optional[Dict]:
    r = con.execute(q, args).fetchone()
    return dict(r) if r else None


# -- settings ---------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    con = connect()
    try:
        r = _row(con, "SELECT value FROM settings WHERE key=?", (key,))
        return r["value"] if r else default
    finally:
        con.close()


def set_setting(key: str, value: str):
    con = connect()
    try:
        con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        con.commit()
    finally:
        con.close()


def get_int_setting(key: str, default: int) -> int:
    try:
        return int(get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default


def get_bool_setting(key: str, default: bool) -> bool:
    return get_setting(key, "true" if default else "false").lower() in ("true", "1", "yes")


# -- users ------------------------------------------------------------------

def create_user(username: str, password_hash: str, role: str,
                display_name: str = "") -> Dict:
    con = connect()
    try:
        con.execute("INSERT INTO users (username, password_hash, role, display_name, created_at) "
                    "VALUES (?,?,?,?,?)", (username, password_hash, role, display_name, now()))
        con.commit()
        return _row(con, "SELECT * FROM users WHERE username=?", (username,))
    finally:
        con.close()


def get_user_by_name(username: str) -> Optional[Dict]:
    con = connect()
    try:
        return _row(con, "SELECT * FROM users WHERE username=?", (username,))
    finally:
        con.close()


def get_user(user_id: int) -> Optional[Dict]:
    con = connect()
    try:
        return _row(con, "SELECT * FROM users WHERE id=?", (user_id,))
    finally:
        con.close()


def list_users(roles: Optional[List[str]] = None) -> List[Dict]:
    con = connect()
    try:
        if roles:
            marks = ",".join("?" for _ in roles)
            return _rows(con, "SELECT * FROM users WHERE role IN (%s) ORDER BY id" % marks, tuple(roles))
        return _rows(con, "SELECT * FROM users ORDER BY id")
    finally:
        con.close()


def update_user(username: str, **fields) -> Optional[Dict]:
    """Set allowed columns (password_hash / role / display_name / last_login_at)."""
    allowed = {"password_hash", "role", "display_name", "last_login_at"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append("%s=?" % k)
            args.append(v)
    if not sets:
        return get_user_by_name(username)
    con = connect()
    try:
        con.execute("UPDATE users SET %s WHERE username=?" % ",".join(sets), args + [username])
        con.commit()
        return _row(con, "SELECT * FROM users WHERE username=?", (username,))
    finally:
        con.close()


def delete_user(username: str) -> int:
    con = connect()
    try:
        u = _row(con, "SELECT id FROM users WHERE username=?", (username,))
        if not u:
            return 0
        con.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
        con.execute("DELETE FROM user_case_overrides WHERE user_id=?", (u["id"],))
        cur = con.execute("DELETE FROM users WHERE id=?", (u["id"],))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


# -- sessions ---------------------------------------------------------------

def create_session(user_id: int, days: int = 7) -> str:
    token = secrets.token_urlsafe(32)
    tok_hash = _hash_token(token)
    con = connect()
    try:
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (now(),))
        con.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) "
                    "VALUES (?,?,?,?)",
                    (tok_hash, user_id, now(),
                     (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")))
        con.commit()
    finally:
        con.close()
    return token


def _hash_token(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_session_user(token: str) -> Optional[Dict]:
    con = connect()
    try:
        s = _row(con, "SELECT * FROM sessions WHERE token_hash=?", (_hash_token(token),))
        if not s or s["expires_at"] < now():
            return None
        return _row(con, "SELECT * FROM users WHERE id=?", (s["user_id"],))
    finally:
        con.close()


def delete_session(token: str):
    con = connect()
    try:
        con.execute("DELETE FROM sessions WHERE token_hash=?", (_hash_token(token),))
        con.commit()
    finally:
        con.close()


# -- benchmarks (bank registry; case content lives in banks/<name>/cases.db) --

def create_benchmark(name: str, visibility: str, owner_id: int,
                     display_name: str = "", description_zh: str = "",
                     description_en: str = "", source_note: str = "",
                     category: str = "", default_level: str = "L2",
                     requirements: str = "") -> Dict:
    con = connect()
    try:
        con.execute("INSERT INTO benchmarks (name, display_name, description_zh, description_en, "
                    "source_note, category, default_level, visibility, status, owner_id, "
                    "requirements, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (name, display_name or name, description_zh, description_en, source_note,
                     category, default_level, visibility,
                     "online" if visibility == "public" else "online", owner_id,
                     requirements, now()))
        con.commit()
        return _row(con, "SELECT * FROM benchmarks WHERE name=?", (name,))
    finally:
        con.close()


def get_benchmark(name: str) -> Optional[Dict]:
    con = connect()
    try:
        return _row(con, "SELECT * FROM benchmarks WHERE name=?", (name,))
    finally:
        con.close()


def list_benchmarks() -> List[Dict]:
    con = connect()
    try:
        return _rows(con, "SELECT * FROM benchmarks ORDER BY visibility, category, name")
    finally:
        con.close()


def update_benchmark(name: str, **fields) -> Optional[Dict]:
    allowed = {"display_name", "description_zh", "description_en", "source_note",
               "category", "default_level", "status", "requirements"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append("%s=?" % k)
            args.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
    if not sets:
        return get_benchmark(name)
    con = connect()
    try:
        con.execute("UPDATE benchmarks SET %s WHERE name=?" % ",".join(sets), args + [name])
        con.commit()
        return _row(con, "SELECT * FROM benchmarks WHERE name=?", (name,))
    finally:
        con.close()


def delete_benchmark(name: str) -> int:
    con = connect()
    try:
        b = _row(con, "SELECT id FROM benchmarks WHERE name=?", (name,))
        if not b:
            return 0
        con.execute("DELETE FROM user_case_overrides WHERE bank_id=?", (b["id"],))
        cur = con.execute("DELETE FROM benchmarks WHERE id=?", (b["id"],))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


# -- user case overrides ("my way of running" on public banks) ---------------

def upsert_override(user_id: int, bank_id: int, case_id: str,
                    level: Optional[str] = None, enabled: Optional[int] = None):
    con = connect()
    try:
        old = _row(con, "SELECT * FROM user_case_overrides WHERE user_id=? AND bank_id=? AND case_id=?",
                   (user_id, bank_id, case_id))
        new_level = level if level is not None else (old["level"] if old else None)
        new_enabled = enabled if enabled is not None else (old["enabled"] if old else 1)
        con.execute("INSERT INTO user_case_overrides (user_id, bank_id, case_id, level, enabled, updated_at) "
                    "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id, bank_id, case_id) DO UPDATE SET "
                    "level=excluded.level, enabled=excluded.enabled, updated_at=excluded.updated_at",
                    (user_id, bank_id, case_id, new_level, int(new_enabled), now()))
        con.commit()
    finally:
        con.close()


def list_overrides(user_id: int, bank_id: int) -> List[Dict]:
    con = connect()
    try:
        return _rows(con, "SELECT * FROM user_case_overrides WHERE user_id=? AND bank_id=?",
                     (user_id, bank_id))
    finally:
        con.close()


def clear_overrides(user_id: int, bank_id: int) -> int:
    con = connect()
    try:
        cur = con.execute("DELETE FROM user_case_overrides WHERE user_id=? AND bank_id=?",
                          (user_id, bank_id))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


# -- runs ---------------------------------------------------------------------

def new_run_id() -> str:
    return "run-%s-%s" % (datetime.now().strftime("%Y%m%d%H%M%S"), uuid.uuid4().hex[:6])


def unique_run_name(user_name: str) -> str:
    """Unique task name = user input + timestamp suffix; same-second conflicts increment a counter."""
    base = "%s-%s" % (user_name.strip(), datetime.now().strftime("%Y%m%d%H%M%S"))
    con = connect()
    try:
        candidate, n = base, 1
        while _row(con, "SELECT id FROM runs WHERE name=?", (candidate,)):
            n += 1
            candidate = "%s-%d" % (base, n)
        return candidate
    finally:
        con.close()


def create_run(name: str, user_name: str, created_by: int, bank_filter: List[Dict],
               target_url: str, proxy_enabled: bool, receiver_port: int,
               case_ids: Optional[List[str]] = None, scheduled_for: Optional[str] = None,
               total_cases: int = 0, est_duration_s: Optional[int] = None,
               stability_k: int = 1, ai_assist: bool = True) -> Dict:
    rid = new_run_id()
    con = connect()
    try:
        con.execute("INSERT INTO runs (id, name, user_name, created_by, created_at, status, "
                    "scheduled_for, bank_filter, case_ids, target_url, proxy_enabled, "
                    "receiver_port, total_cases, est_duration_s, stability_k, ai_assist) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, name, user_name, created_by, now(), "queued", scheduled_for,
                     json.dumps(bank_filter, ensure_ascii=False),
                     json.dumps(case_ids) if case_ids else None,
                     target_url, int(proxy_enabled), receiver_port, total_cases,
                     est_duration_s, max(1, int(stability_k or 1)), int(bool(ai_assist))))
        con.commit()
        return _row(con, "SELECT * FROM runs WHERE id=?", (rid,))
    finally:
        con.close()


def get_run(run_id: str) -> Optional[Dict]:
    con = connect()
    try:
        return _row(con, "SELECT * FROM runs WHERE id=?", (run_id,))
    finally:
        con.close()


def list_runs(created_by: Optional[int] = None, name_like: str = "", bank: str = "",
              status: str = "", limit: int = 100) -> List[Dict]:
    con = connect()
    try:
        q, args = "SELECT * FROM runs WHERE 1=1", []
        if created_by is not None:
            q += " AND created_by=?"
            args.append(created_by)
        if name_like:
            q += " AND (name LIKE ? OR user_name LIKE ?)"
            args += ["%" + name_like + "%", "%" + name_like + "%"]
        if bank:
            q += " AND bank_filter LIKE ?"
            args.append('%"' + bank + '"%')
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY created_at DESC, id DESC LIMIT ?"
        args.append(limit)
        return _rows(con, q, tuple(args))
    finally:
        con.close()


def next_queued_run() -> Optional[Dict]:
    """Next runnable run: queued and (immediate or scheduled time already reached); FIFO by creation."""
    con = connect()
    try:
        return _row(con, "SELECT * FROM runs WHERE status='queued' AND "
                         "(scheduled_for IS NULL OR scheduled_for <= ?) "
                         "ORDER BY created_at, id LIMIT 1", (now(),))
    finally:
        con.close()


def update_run(run_id: str, **fields):
    allowed = {"status", "started_at", "finished_at", "total_cases", "done_cases",
               "cancel_requested", "gate_decision", "score", "result_dir", "error",
               "est_duration_s", "scheduled_for", "final_gate"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append("%s=?" % k)
            args.append(v)
    if not sets:
        return
    con = connect()
    try:
        con.execute("UPDATE runs SET %s WHERE id=?" % ",".join(sets), args + [run_id])
        con.commit()
    finally:
        con.close()


def queue_position(run_id: str) -> int:
    con = connect()
    try:
        rows = _rows(con, "SELECT id FROM runs WHERE status='queued' ORDER BY created_at, id")
        for i, r in enumerate(rows, 1):
            if r["id"] == run_id:
                return i
        return 0
    finally:
        con.close()


# -- run items (per-case live results) ----------------------------------------

def add_run_item(run_id: str, case_id: str, bank: str, level: str, verdict: str,
                 tokens: int, wall_time_s: float, asi: str = ""):
    con = connect()
    try:
        con.execute("INSERT OR REPLACE INTO run_items (run_id, case_id, bank, level, verdict, "
                    "tokens, wall_time_s, asi, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (run_id, case_id, bank, level, verdict, tokens, wall_time_s,
                     (asi or "")[:300], now()))
        con.execute("UPDATE runs SET done_cases = done_cases + 1 WHERE id=?", (run_id,))
        con.commit()
    finally:
        con.close()


def list_run_items(run_id: str) -> List[Dict]:
    con = connect()
    try:
        return _rows(con, "SELECT * FROM run_items WHERE run_id=? ORDER BY id", (run_id,))
    finally:
        con.close()


def avg_case_seconds(limit: int = 200) -> float:
    """Historical mean per-case wall time (ETA fuel); 30s placeholder when no history exists."""
    con = connect()
    try:
        r = _row(con, "SELECT AVG(wall_time_s) AS a FROM "
                      "(SELECT wall_time_s FROM run_items ORDER BY id DESC LIMIT ?)", (limit,))
        return float(r["a"]) if r and r["a"] else 30.0
    finally:
        con.close()


def bank_run_history(bank: str, days: int = 10) -> Dict:
    """Aggregate history for one bank: total runs / success rate / recent failure count / latest run."""
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    con = connect()
    try:
        like = '%"' + bank + '"%'
        runs = _rows(con, "SELECT r.id, r.name, r.user_name, r.status, r.created_at, r.finished_at, "
                          "r.gate_decision, r.score, r.total_cases, r.done_cases, "
                          "u.username AS creator FROM runs r LEFT JOIN users u ON u.id = r.created_by "
                          "WHERE r.bank_filter LIKE ? AND r.created_at >= ? ORDER BY r.created_at DESC",
                     (like, since))
        failed = _row(con, "SELECT COUNT(*) AS n FROM run_items WHERE bank=? AND verdict='FAIL' "
                           "AND created_at >= ?", (bank, since))
        total_all = _row(con, "SELECT COUNT(*) AS n FROM runs WHERE bank_filter LIKE ?", (like,))
        return {"runs_recent": runs, "recent_failures": failed["n"] if failed else 0,
                "total_runs": total_all["n"] if total_all else 0, "window_days": days}
    finally:
        con.close()


def retention_expired_dirs(days: int) -> List[Dict]:
    """Finished runs whose result_dir is older than the retention window (dir removed by caller)."""
    cut = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    con = connect()
    try:
        return _rows(con, "SELECT id, result_dir FROM runs WHERE result_dir IS NOT NULL AND "
                          "result_dir != '' AND finished_at IS NOT NULL AND finished_at < ?", (cut,))
    finally:
        con.close()


# -- per-user per-bank edit drafts (uncommitted bank changes, one per user per bank) ----------

def get_edit_draft(user_id: int, bank_id: int) -> Optional[Dict]:
    con = connect()
    try:
        r = _row(con, "SELECT draft, updated_at FROM edit_drafts WHERE user_id=? AND bank_id=?",
                 (user_id, bank_id))
        if not r:
            return None
        d = json.loads(r["draft"])
        d["updated_at"] = r["updated_at"]
        return d
    finally:
        con.close()


def put_edit_draft(user_id: int, bank_id: int, draft: Dict):
    con = connect()
    try:
        con.execute("INSERT INTO edit_drafts (user_id, bank_id, draft, updated_at) "
                    "VALUES (?,?,?,?) ON CONFLICT(user_id, bank_id) DO UPDATE SET "
                    "draft=excluded.draft, updated_at=excluded.updated_at",
                    (user_id, bank_id, json.dumps(draft, ensure_ascii=False), now()))
        con.commit()
    finally:
        con.close()


def delete_edit_draft(user_id: int, bank_id: int) -> int:
    con = connect()
    try:
        cur = con.execute("DELETE FROM edit_drafts WHERE user_id=? AND bank_id=?",
                          (user_id, bank_id))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


# -- per-user settings (User-Center AI config, doc 34; secrets never leave the server) ----

AI_KEYS = ("ai_base_url", "ai_api_key", "ai_model", "ai_judge_auto", "ai_prompts")


def set_user_settings(user_id: int, values: Dict) -> None:
    con = connect()
    try:
        for k, v in values.items():
            if k not in AI_KEYS:
                continue
            con.execute("INSERT INTO user_settings (user_id, key, value) VALUES (?,?,?) "
                        "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
                        (user_id, k, "" if v is None else str(v)))
        con.commit()
    finally:
        con.close()


def get_user_settings(user_id: int, include_secret: bool = False) -> Dict:
    con = connect()
    try:
        rows = _rows(con, "SELECT key, value FROM user_settings WHERE user_id=?", (user_id,))
    finally:
        con.close()
    out = {r["key"]: (r["value"] or "") for r in rows}
    if not include_secret:
        out.pop("ai_api_key", None)
    return out


def ai_config_for(user_id: int) -> Optional[Dict]:
    """The caller's LLM config for optional AI enhancements; None = AI disabled for this user.
    Includes the user's prompt overrides (analysis/prompts.py registry) when set."""
    s = get_user_settings(user_id, include_secret=True)
    if s.get("ai_base_url") and s.get("ai_api_key") and s.get("ai_model"):
        from ..analysis.prompts import parse_user_prompts
        return {"base_url": s["ai_base_url"], "api_key": s["ai_api_key"],
                "model": s["ai_model"],
                "auto_adopt": str(s.get("ai_judge_auto", "")).lower() in ("1", "true", "yes"),
                "prompts": parse_user_prompts(s.get("ai_prompts", ""))}
    return None


def requeue_interrupted_runs() -> int:
    """Startup recovery: a platform restart kills the serial worker mid-run; runs left in
    'running' would be stuck forever (the worker only claims queued rows). Re-queue them —
    per-case results may duplicate in run_items history but the result dir is only written
    at the end, so a clean re-run always produces a consistent report."""
    con = connect()
    try:
        cur = con.execute("UPDATE runs SET status='queued', "
                          "error='interrupted by platform restart; requeued' "
                          "WHERE status='running'")
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def review_item(run_id: str, case_id: str, final_verdict: str,
                reviewed_by: str, note: str) -> int:
    """Human final ruling on a PENDING item (who / what / why are kept on the row)."""
    assert final_verdict in ("PASS", "FAIL")
    con = connect()
    try:
        cur = con.execute("UPDATE run_items SET final_verdict=?, reviewed_by=?, review_note=? "
                          "WHERE run_id=? AND case_id=? AND verdict='PENDING'",
                          (final_verdict, reviewed_by, note, run_id, case_id))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def case_history(case_ids: List[str]) -> Dict[str, Dict]:
    """Per-case history across ALL runs (for the run-detail history column):
    how many runs included the case and how many passed."""
    out = {cid: {"runs": 0, "pass": 0} for cid in case_ids}
    if not case_ids:
        return out
    con = connect()
    try:
        qmarks = ",".join("?" * len(case_ids))
        for cid, verdict in con.execute(
                "SELECT case_id, verdict FROM run_items WHERE case_id IN (%s)" % qmarks,
                case_ids):
            h = out.setdefault(cid, {"runs": 0, "pass": 0})
            h["runs"] += 1
            if verdict == "PASS":
                h["pass"] += 1
    finally:
        con.close()
    return out
