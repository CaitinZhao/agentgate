"""Provider factory from the agentgate.json "sandbox" section.

{"sandbox": {"provider": "docker" | "subprocess" | "off" (default),
             "image": "python:3.11-slim",       # docker default image
             "network": "",                     # docker --network (optional)
             "exec_base_url": "",               # agent-reachable platform URL
             "ttl_s": 1800}}                    # exec-registry token TTL
"off" (or missing) -> None: state cases with a sandbox spec stay PENDING, exactly
the pre-P1 behavior.
"""
from typing import Dict, Optional

from .base import SandboxProvider


def from_config(cfg: Optional[Dict]) -> Optional[SandboxProvider]:
    cfg = cfg or {}
    which = str(cfg.get("provider") or "off").lower()
    if which in ("", "off", "none", "disabled"):
        return None
    if which == "docker":
        from .docker_impl import DockerSandboxProvider
        return DockerSandboxProvider(default_image=cfg.get("image") or "python:3.11-slim",
                                     network=cfg.get("network") or "")
    if which == "subprocess":
        from .subprocess_impl import SubprocessSandboxProvider
        return SubprocessSandboxProvider()
    raise ValueError("unknown sandbox provider: %s (docker/subprocess/off)" % which)
