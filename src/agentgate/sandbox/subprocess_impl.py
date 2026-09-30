"""Subprocess sandbox provider: runs commands in a temp workdir on the host.

NO isolation — dev/test convenience only (and the default way to try the state
assertion flow on a laptop without docker). Never point untrusted agents at it.
"""
import os
import shutil
import subprocess
import tempfile
from typing import Dict

from .base import SandboxHandle, SandboxProvider


class SubprocessHandle(SandboxHandle):
    name = "subprocess"

    def __init__(self, workdir: str):
        self.workdir = workdir
        self._closed = False

    def _resolve(self, path: str) -> str:
        p = os.path.normpath(os.path.join(self.workdir, path.lstrip("/\\")))
        if not p.startswith(os.path.normpath(self.workdir)):
            raise ValueError("path escapes the sandbox workdir: %s" % path)
        return p

    def exec(self, cmd: str, timeout: float = 30) -> Dict:
        # POSIX semantics to match the docker provider (cat/test/heredocs); bash is
        # expected on dev machines (Git Bash / WSL). Relative paths resolve against
        # the workdir — absolute paths are the docker provider's domain.
        shell = _bash_path()
        args = [shell, "-c", cmd] if shell else cmd
        r = subprocess.run(args, shell=not shell, cwd=self.workdir, capture_output=True,
                           text=True, timeout=timeout)
        return {"exit_code": r.returncode, "stdout": r.stdout, "stderr": r.stderr}

    def write_file(self, path: str, content: str) -> None:
        target = self._resolve(path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(content)

    def read_file(self, path: str) -> str:
        target = self._resolve(path)
        with open(target, "r", encoding="utf-8") as fh:
            return fh.read()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        shutil.rmtree(self.workdir, ignore_errors=True)


def _bash_path():
    return shutil.which("bash") or shutil.which("sh")


class SubprocessSandboxProvider(SandboxProvider):
    name = "subprocess"

    def create(self, spec: Dict) -> SandboxHandle:
        handle = SubprocessHandle(tempfile.mkdtemp(prefix="agentgate-sandbox-"))
        try:
            for op in (spec.get("setup") or []):
                if not isinstance(op, dict):
                    continue
                if "cmd" in op:
                    out = handle.exec(op["cmd"], timeout=60)
                    if out["exit_code"] != 0:
                        raise RuntimeError("sandbox setup cmd failed: %s -> %s"
                                           % (op["cmd"], (out["stderr"] or out["stdout"])[-200:]))
                elif "write_file" in op:
                    wf = op["write_file"] or {}
                    handle.write_file(wf.get("path", ""), str(wf.get("content", "")))
        except Exception:
            handle.close()
            raise
        return handle
