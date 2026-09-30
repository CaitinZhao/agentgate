"""In-memory scripted sandbox provider: deterministic behavior for tests.

files: {path: content} served by read_file/write_file; exec_results: either a dict
used for every command or a list popped per command — each entry shaped like the
exec() contract {"exit_code", "stdout", "stderr"}.
"""
from typing import Dict, List, Optional

from .base import SandboxHandle, SandboxProvider


class FakeSandboxHandle(SandboxHandle):
    name = "fake"

    def __init__(self, files: Dict[str, str], exec_results: Optional[object]):
        self.files = dict(files)
        self.written: List[tuple] = []
        self.exec_calls: List[str] = []
        self.exec_results = exec_results if isinstance(exec_results, list) \
            else ([exec_results] if exec_results else [])
        self.closed = False

    def exec(self, cmd: str, timeout: float = 30) -> Dict:
        self.exec_calls.append(cmd)
        if self.exec_results:
            r = self.exec_results.pop(0)
        else:
            r = {"exit_code": 0, "stdout": "", "stderr": ""}
        return {"exit_code": int(r.get("exit_code", 0)), "stdout": str(r.get("stdout", "")),
                "stderr": str(r.get("stderr", ""))}

    def write_file(self, path: str, content: str) -> None:
        self.written.append((path, content))
        self.files[path] = content

    def read_file(self, path: str) -> str:
        if path not in self.files:
            raise FileNotFoundError("fake sandbox: %s" % path)
        return self.files[path]

    def close(self) -> None:
        self.closed = True


class FakeSandboxProvider(SandboxProvider):
    name = "fake"

    def __init__(self, files: Dict[str, str] = None, exec_results: Optional[object] = None):
        self.files = files or {}
        self.exec_results = exec_results
        self.created: List[Dict] = []

    def create(self, spec: Dict) -> SandboxHandle:
        self.created.append(spec)
        return FakeSandboxHandle(dict(self.files), self.exec_results)
