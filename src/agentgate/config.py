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
    # P1 sandbox (state cases with gold.final.sandbox): provider off = pre-P1 behavior
    "sandbox": {
        "provider": "off",               # off | docker | subprocess
        "image": "python:3.11-slim",     # docker default image (case spec may override)
        "network": "",                   # docker --network (optional)
        "exec_base_url": "",             # agent-reachable platform URL; empty = localhost:rest_port
        "ttl_s": 1800,                   # exec-registry token TTL
    },
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
