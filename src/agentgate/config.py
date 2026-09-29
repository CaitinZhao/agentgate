"""AgentGate unified config: read from agentgate.json in the working directory;
unspecified keys use defaults.

Example agentgate.json:
{
  "target_base_url": "http://127.0.0.1:8100",
  "receiver_port": 4318,
  "rest_port": 8030,
  "default_cases": "cases/example",
  "results_root": "results"
}
"""
import json
from pathlib import Path
from typing import Dict

DEFAULTS = {
    "target_base_url": "http://127.0.0.1:8100",
    "receiver_port": 4318,
    "rest_port": 8030,
    "default_cases": "cases/example",
    "results_root": "results",
    "data_dir": "data",      # web platform data root: agentgate.db + banks/ + results/ (outside src)
}


def load_config(path: str = "agentgate.json") -> Dict:
    cfg = dict(DEFAULTS)
    f = Path(path)
    if f.exists():
        try:
            user = json.loads(f.read_text(encoding="utf-8"))
            for k, v in user.items():
                if k in DEFAULTS:
                    cfg[k] = v
        except (ValueError, OSError):
            pass  # broken config falls back to defaults; never break evaluation on it
    return cfg
