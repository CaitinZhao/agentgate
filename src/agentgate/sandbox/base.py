"""Sandbox provider contract: create a managed environment, run terminal ops, tear down.

A handle is the unit the rest of the platform talks to: the agent reaches it only
through the HTTP exec API (token-gated), the judge reaches it directly to read the
final state. Providers must be safe to create/close per case; a broken create must
never take the run down (the service falls back to PENDING for that case).
"""
from abc import ABC, abstractmethod
from typing import Dict, Optional


class SandboxHandle(ABC):
    """One live sandbox instance."""

    name: str = "handle"

    @abstractmethod
    def exec(self, cmd: str, timeout: float = 30) -> Dict:
        """Run a shell command inside the sandbox ->
        {"exit_code": int, "stdout": str, "stderr": str}."""

    @abstractmethod
    def write_file(self, path: str, content: str) -> None:
        """Write (overwrite) a text file inside the sandbox."""

    @abstractmethod
    def read_file(self, path: str) -> str:
        """Read a text file; raise FileNotFoundError/IOError when absent."""

    @abstractmethod
    def close(self) -> None:
        """Tear the sandbox down (idempotent)."""


class SandboxProvider(ABC):
    """Creates and tears down sandboxes; one instance lives per worker."""

    name: str = "provider"

    @abstractmethod
    def create(self, spec: Dict) -> SandboxHandle:
        """spec: {"image": str?, "setup": [{"cmd": str} | {"write_file": {path, content}}]?}.
        Setup failures raise — the caller treats the case as PENDING."""

    def close(self, handle: SandboxHandle) -> None:
        handle.close()


def looks_like_env_assertion(a) -> bool:
    """Assertions evaluated against the sandbox (vs the legacy final_json path walk)."""
    return isinstance(a, dict) and bool(a.get("read_file") or a.get("exec"))


def sandbox_spec(case) -> Optional[Dict]:
    """The gold.final.sandbox spec of a state case, when present and well-formed."""
    gfinal = (getattr(case.gold, "final", None) or {})
    spec = gfinal.get("sandbox")
    return spec if isinstance(spec, dict) else None
