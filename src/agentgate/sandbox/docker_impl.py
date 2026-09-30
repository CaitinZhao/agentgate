"""Docker sandbox provider: one container per case, driven through the docker CLI.

No docker SDK dependency — the platform host already has docker (deploy mode).
Isolation boundary: the container shares the docker network only as configured;
the exec API is token-gated and the token is per sandbox.
"""
import json
import shlex
import subprocess
from typing import Dict

from .base import SandboxHandle, SandboxProvider


def _run(args, timeout: float = 60, input_text: str = None, check: bool = True):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                       input=input_text)
    if check and r.returncode != 0:
        raise RuntimeError("docker %s failed: %s" % (args[1], (r.stderr or r.stdout)[-300:]))
    return r


class DockerHandle(SandboxHandle):
    name = "docker"

    def __init__(self, container_id: str):
        self.container_id = container_id
        self._closed = False

    def exec(self, cmd: str, timeout: float = 30) -> Dict:
        r = _run(["docker", "exec", self.container_id, "sh", "-c", cmd], timeout=timeout,
                 check=False)
        return {"exit_code": r.returncode, "stdout": r.stdout, "stderr": r.stderr}

    def write_file(self, path: str, content: str) -> None:
        target = "cat > " + shlex.quote(path)
        _run(["docker", "exec", "-i", self.container_id, "sh", "-c", target],
             input_text=content)

    def read_file(self, path: str) -> str:
        r = _run(["docker", "exec", self.container_id, "sh", "-c", "cat " + shlex.quote(path)],
                 check=False)
        if r.returncode != 0:
            raise FileNotFoundError("sandbox read_file %s: %s" % (path, (r.stderr or "")[-200:]))
        return r.stdout

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _run(["docker", "rm", "-f", self.container_id], check=False)


class DockerSandboxProvider(SandboxProvider):
    name = "docker"

    def __init__(self, default_image: str = "python:3.11-slim", network: str = ""):
        self.default_image = default_image
        self.network = network

    def create(self, spec: Dict) -> SandboxHandle:
        image = spec.get("image") or self.default_image
        args = ["docker", "run", "-d", "--rm"]
        if self.network:
            args += ["--network", self.network]
        args += [image, "sleep", "3600"]        # keep-alive; closed explicitly per case
        cid = _run(args, timeout=180).stdout.strip()
        if not cid:
            raise RuntimeError("docker run returned no container id")
        handle = DockerHandle(cid)
        for op in (spec.get("setup") or []):
            if not isinstance(op, dict):
                continue
            if "cmd" in op:
                out = handle.exec(op["cmd"], timeout=60)
                if out["exit_code"] != 0:
                    handle.close()
                    raise RuntimeError("sandbox setup cmd failed: %s -> %s"
                                       % (op["cmd"], (out["stderr"] or out["stdout"])[-200:]))
            elif "write_file" in op:
                wf = op["write_file"] or {}
                handle.write_file(wf.get("path", ""), str(wf.get("content", "")))
        return handle
