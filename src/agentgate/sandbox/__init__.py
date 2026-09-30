"""Sandboxed execution (P1): platform-managed environments for `state` cases.

The platform runs the target agent's terminal tools inside a sandbox it owns
(Docker container by default), then asserts the environment's FINAL STATE after
the invoke — τ-bench-style terminal assertions instead of trusting the agent's
self-reported answer.

Pieces:
  - base.SandboxHandle / SandboxProvider   the provider contract
  - docker_impl.DockerSandboxProvider      production provider (docker CLI, no SDK dep)
  - subprocess_impl.SubprocessSandboxProvider  dev/test provider (temp dir, NO isolation)
  - fake.FakeSandboxProvider               scripted in-memory provider for tests
  - provider.from_config                   builds the configured provider ("off" -> None)
  - registry                               token -> handle registry behind the exec HTTP API
  - verify                                 final-state assertion evaluation (judging side)

Case schema (all inside the free-form gold.final dict, no model change):
  "final": {
    "sandbox": {"image": "python:3.11-slim",
                "setup": [{"cmd": "mkdir -p /app/out"},
                          {"write_file": {"path": "/app/in/x.txt", "content": "..."}}]},
    "assertions": [{"read_file": "/app/out/report.json", "json_path": "status",
                    "equals": "done"},
                   {"exec": "test -f /app/out/task.bak", "exit_code": 0}]
  }
Legacy assertions without read_file/exec keep their old meaning (a path walk over
the agent's final_json).
"""
from .base import SandboxHandle, SandboxProvider
from .provider import from_config
from .registry import register, resolve, unregister

__all__ = ["SandboxHandle", "SandboxProvider", "from_config",
           "register", "resolve", "unregister"]
