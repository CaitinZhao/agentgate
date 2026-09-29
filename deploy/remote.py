"""Remote deployment driver: deploy and verify the evaluation environment on a remote server via SSH/SFTP.

Usage (credentials come from the environment, never from git):
  AGENTGATE_DEPLOY_HOST=... AGENTGATE_DEPLOY_USER=... AGENTGATE_DEPLOY_PASSWORD=...       python -m deploy.remote probe
"""
import os
import sys
from pathlib import Path
from typing import Dict

import paramiko

HOST = os.environ.get("AGENTGATE_DEPLOY_HOST", "")
USER = os.environ.get("AGENTGATE_DEPLOY_USER", "")
ROOT = Path(__file__).resolve().parent


def connect(password: str) -> paramiko.SSHClient:
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, username=USER, password=password, timeout=30,
                allow_agent=False, look_for_keys=False)
    return cli


def run(cli: paramiko.SSHClient, cmd: str, timeout: int = 600, pty: bool = False) -> tuple:
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout, get_pty=pty)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def _sudo_impl(cli: paramiko.SSHClient, password: str, cmd: str, timeout: int) -> tuple:
    stdin, stdout, stderr = cli.exec_command("sudo -S -p '' bash -c %s" % _shq(cmd),
                                             timeout=timeout, get_pty=True)
    stdin.write(password + "\n")
    stdin.flush()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def probe(cli: paramiko.SSHClient, password: str) -> Dict:
    info: Dict = {}
    for label, cmd in [
        ("os", "cat /etc/os-release | head -2"),
        ("arch", "uname -m"),
        ("user", "id"),
        ("sudo", "sudo -S -p '' true"),
        ("docker", "command -v docker && docker version --format '{{.Server.Version}}' || echo NO_DOCKER"),
        ("python3", "python3 --version 2>&1 || echo NO_PYTHON3"),
        ("disk_free", "df -h / | tail -1"),
        ("mem", "free -m | head -2 | tail -1"),
    ]:
        if label == "sudo":
            code, out, err = _sudo_impl(cli, password, "true", 30)
            info["sudo"] = "OK" if code == 0 else "FAIL(%d)" % code
            continue
        code, out, err = run(cli, cmd, timeout=60)
        info[label] = out.strip() or err.strip() or "(empty)"
    return info


def _shq(s: str) -> str:  # noqa: F811 (safe single-quote wrapping)
    return "'" + s.replace("'", "'\\''") + "'"


from typing import Dict  # noqa: E402

if __name__ == "__main__":
    pwd = sys.argv[1] if len(sys.argv) > 1 else ""
    if not pwd:
        print("usage: python deploy/remote.py <password> [probe]")
        sys.exit(1)
    c = connect(pwd)
    for k, v in probe(c, pwd).items():
        print("%-10s: %s" % (k, v))
    c.close()
