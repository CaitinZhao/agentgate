"""Deploy the AgentGate web platform to the remote server in Docker.

Usage (from the workspace root):
  python agentgate/deploy/deploy_platform.py [--owner-password secret] [--skip-build]

Flow: pack bundle (code + prebuilt web/dist) -> upload -> build the platform image
(context = bundle root with agent-contracts/ + agentgate/) -> run with host networking
(API :8030 / receiver :4318 / recording proxy :8300, reachable by the agent container
via 172.17.0.1) -> migrate the legacy case banks -> health check.

The real gateway URL for the recording proxy is read from the existing
/opt/agentgate/fin-runtime/.env.origin (deploy_eval.py's snapshot of the true LLM_BASE_URL).
The jiuwen sample agent container (deploy_eval.py, :8200) is expected to exist already;
this script does not rebuild it.
"""
import argparse
import os
import secrets
import subprocess
import tarfile
import time
from pathlib import Path

import paramiko

# Server credentials come from the environment (never committed to the repo):
#   AGENTGATE_DEPLOY_HOST / AGENTGATE_DEPLOY_USER / AGENTGATE_DEPLOY_PASSWORD
# Git Bash:  export AGENTGATE_DEPLOY_HOST=1.2.3.4 AGENTGATE_DEPLOY_USER=agent
#            export AGENTGATE_DEPLOY_PASSWORD='...'      (PowerShell: $env: same names)
HOST = os.environ.get("AGENTGATE_DEPLOY_HOST", "")
USER = os.environ.get("AGENTGATE_DEPLOY_USER", "")
PASSWORD = os.environ.get("AGENTGATE_DEPLOY_PASSWORD", "")
REMOTE_ROOT = "/opt/agentgate-platform"
EVAL_ROOT = "/opt/agentgate"                # existing deploy_eval.py layout (.env.origin lives there)
LOCAL_ROOT = Path(__file__).resolve().parents[2]
BUNDLE = Path(__file__).resolve().parent / "platform-bundle.tar.gz"
IMAGE = "agentgate-platform:0.1"
CONTAINER = "agentgate-web"

BUNDLE_ITEMS = [
    "agent-contracts/pyproject.toml",
    "agent-contracts/schemas",
    "agent-contracts/python",
    "agentgate/pyproject.toml",
    "agentgate/README.md",
    "agentgate/src",
    "agentgate/docs",
    "agentgate/cases",
    "agentgate/web/dist",
]

REMOTE_DOCKERFILE = """FROM {base}
WORKDIR /app
ENV TZ=Asia/Shanghai
COPY agent-contracts/ /app/agent-contracts/
COPY agentgate/ /app/agentgate/
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \\
      /app/agent-contracts /app/agentgate \\
 && agentgate --help > /dev/null
ENV AGENTGATE_DATA_DIR=/app/data
VOLUME ["/app/data"]
EXPOSE 8030 4318 8300
CMD ["agentgate", "web", "--host", "0.0.0.0", "--port", "8030"]
"""

MIRRORS = [
    "",
    "docker.m.daocloud.io/library/",
    "docker.1ms.run/library/",
]


def _shq(s):
    return "'" + s.replace("'", "'\\''") + "'"


class Remote:
    def __init__(self):
        self.cli = paramiko.SSHClient()
        self.cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.cli.connect(HOST, username=USER, password=PASSWORD, timeout=30,
                         allow_agent=False, look_for_keys=False)
        self.sftp = self.cli.open_sftp()

    def run(self, cmd, timeout=1800, show_tail=2):
        _, out, err = self.cli.exec_command(cmd, timeout=timeout, get_pty=True)
        text = out.read().decode("utf-8", "replace")
        code = out.channel.recv_exit_status()
        if show_tail:
            tail = [l for l in text.strip().splitlines()[-show_tail:]]
            print("  [rc=%d] %s" % (code, " | ".join(tail)[:240]))
        return code, text

    def put(self, local: Path, remote: str):
        self.sftp.put(str(local), remote)

    def put_text(self, remote: str, content: str):
        with self.sftp.open(remote, "w") as f:
            f.write(content)


def ensure_frontend_dist():
    """The image ships the built frontend; rebuild it locally when missing."""
    dist = LOCAL_ROOT / "agentgate" / "web" / "dist" / "index.html"
    if not dist.exists():
        print("    web/dist 缺失，本地 npm run build ...")
        subprocess.run(["npm", "run", "build"], cwd=LOCAL_ROOT / "agentgate" / "web",
                       check=True)
    return dist.exists()


def make_bundle():
    print("[1] packing local code + prebuilt frontend ...")
    if not ensure_frontend_dist():
        raise RuntimeError("web/dist unavailable (npm build failed)")
    if BUNDLE.exists():
        BUNDLE.unlink()
    with tarfile.open(BUNDLE, "w:gz") as tf:
        for item in BUNDLE_ITEMS:
            p = LOCAL_ROOT / item
            if p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file() and "__pycache__" not in str(f) \
                            and "/logs/" not in str(f).replace("\\", "/"):
                        tf.add(f, arcname=str(f.relative_to(LOCAL_ROOT)))
            elif p.is_file():
                tf.add(p, arcname=item)
    print("    bundle: %.1f MB" % (BUNDLE.stat().st_size / 1024 / 1024))


def pull_image(r: Remote, image: str) -> str:
    """Pull an image: official registry -> mirror fallback chain. Returns the ref in use."""
    code, _ = r.run("docker image inspect %s >/dev/null 2>&1" % image, show_tail=0)
    if code == 0:
        print("    镜像已存在，跳过拉取：%s" % image)
        return image
    for mirror in MIRRORS:
        ref = (mirror + image.split("/")[-1]) if mirror else image
        code, _ = r.run("docker pull %s 2>&1 | tail -1" % ref, timeout=900, show_tail=0)
        if code == 0:
            if mirror:
                r.run("docker tag %s %s" % (ref, image))
            print("    镜像拉取成功：%s" % ref)
            return image
    raise RuntimeError("所有镜像源均失败")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner-password", default="",
                    help="owner 初始密码（留空则随机生成并打印一次）")
    ap.add_argument("--skip-build", action="store_true",
                    help="跳过打包/构建，直接用远程现有镜像重启容器")
    ap.add_argument("--web-port", type=int, default=8030, help="平台 Web/API 端口（默认 8030）")
    ap.add_argument("--receiver-port", type=int, default=4318,
                    help="OTLP 接收器端口（默认 4318；容器内 env 每次启动覆盖设置表）")
    ap.add_argument("--proxy-port", type=int, default=8300,
                    help="消息级录制代理端口（默认 8300）")
    ap.add_argument("--agent-port", type=int, default=8200,
                    help="被测 Agent 端口（默认 8200；用于体检与提示信息）")
    args = ap.parse_args()

    r = Remote()
    print("[0] 连接 %s（platform -> %s）" % (HOST, REMOTE_ROOT))
    owner_password = args.owner_password or secrets.token_urlsafe(12)
    printed_password = not args.owner_password   # auto-generated ones get printed

    if not args.skip_build:
        make_bundle()
        print("[2] 上传并解包 ...")
        r.run("mkdir -p %s/data" % REMOTE_ROOT)
        r.put(BUNDLE, REMOTE_ROOT + "/platform-bundle.tar.gz")
        r.run("cd %s && tar -xzf platform-bundle.tar.gz && rm platform-bundle.tar.gz" % REMOTE_ROOT)

        print("[3] 拉取基础镜像（含镜像源回退）...")
        base = pull_image(r, "python:3.11-slim")

        print("[4] 构建平台镜像 %s ..." % IMAGE)
        r.put_text(REMOTE_ROOT + "/Dockerfile.web", REMOTE_DOCKERFILE.format(base=base))
        code, text = r.run("cd %s && DOCKER_BUILDKIT=0 docker build -t %s -f Dockerfile.web . "
                           "2>&1 | tail -3" % (REMOTE_ROOT, IMAGE), timeout=2400)
        if code != 0:
            print("    [!] 构建失败，输出尾部：")
            for line in text.strip().splitlines()[-10:]:
                print("    | %s" % line[:200])
            r.cli.close()
            sys.exit(1)

    print("[5] 启动容器（host 网络：API :8030 / 接收器 :4318 / 录制代理 :8300）...")
    # port-precise cleanup of stale processes (never pkill -f — it kills the wrapping shell):
    # 8300 may hold the previous session's host llm-proxy, 4318 a stale receiver
    r.run("for port in 8030 4318 8300; do "
          "pid=$(ss -tlnp 2>/dev/null | grep \":$port \" | grep -oP 'pid=\\K[0-9]+' | head -1); "
          "[ -n \"$pid\" ] && kill $pid && echo killed_port_$pid || true; done; sleep 1", show_tail=1)
    r.run("docker rm -f %s 2>/dev/null; true" % CONTAINER, show_tail=0)
    # real gateway URL for the proxy upstream from the eval layout's origin snapshot
    code, gw = r.run("grep -oP '^LLM_BASE_URL=\\K.*' %s/fin-runtime/.env.origin 2>/dev/null | tr -d '\\r'"
                     % EVAL_ROOT, show_tail=0)
    gateway = gw.strip().splitlines()[-1].strip() if gw.strip() else ""
    print("    proxy upstream = %s" % (gateway or "(未取到，稍后在设置页配置)"))
    code, _ = r.run(
        "docker run -d --name %s --network host "
        "-v %s/data:/app/data "
        "-e OWNER_PASSWORD=%s "
        "-e AGENTGATE_PROXY_UPSTREAM=%s "
        "-e AGENTGATE_PROXY_AGENT_URL=http://172.17.0.1:%d/v1 "
        "-e AGENTGATE_RECEIVER_PORT=%d -e AGENTGATE_PROXY_PORT=%d "
        "%s agentgate web --host 0.0.0.0 --port %d"
        % (CONTAINER, REMOTE_ROOT, _shq(owner_password),
           _shq(gateway), args.proxy_port, args.receiver_port, args.proxy_port,
           IMAGE, args.web_port), timeout=120)
    if code != 0:
        print("    [!] 容器启动失败")
        r.cli.close()
        sys.exit(1)

    print("[6] 健康检查 ...")
    ok = False
    for _ in range(15):
        time.sleep(2)
        code, text = r.run("curl -s -m 5 http://127.0.0.1:%d/api/v1/health || true" % args.web_port,
                           show_tail=0)
        if '"status":"ok"' in text:
            print("    %s" % text.strip()[:160])
            ok = True
            break
    if not ok:
        r.run("docker logs --tail 20 %s" % CONTAINER, show_tail=1)
        r.cli.close()
        sys.exit(1)


    code, proxy = r.run("curl -s -m 5 http://127.0.0.1:%d/health || true" % args.proxy_port,
                        show_tail=0)
    print("[7] 录制代理： %s" % proxy.strip()[:160])
    code, agent = r.run("curl -s -m 5 http://127.0.0.1:%d/health || true" % args.agent_port,
                        show_tail=0)
    print("    被测 Agent： %s" % agent.strip()[:160])
    # OWNER_PASSWORD only bootstraps an empty platform DB — make stale-password prints honest
    code, n_users = r.run("docker exec %s python -c \"import sqlite3;"
                          "print(sqlite3.connect('/app/data/agentgate.db')"
                          ".execute('select count(*) from users').fetchone()[0])\"" % CONTAINER,
                          show_tail=0)
    bootstrapped = n_users.strip().splitlines()[-1] == "0" if n_users.strip() else False

    print("=" * 68)
    print("平台已部署：http://%s:%d  （浏览器打开即 Web UI）" % (HOST, args.web_port))
    if printed_password and bootstrapped:
        print("owner 初始密码（仅此一次）： %s" % owner_password)
        print("修改：ssh 部署机执行  agentgate passwd owner  （docker exec %s agentgate passwd owner）"
              % CONTAINER)
    elif not bootstrapped:
        print("owner 账号沿用数据卷中的既有密码（OWNER_PASSWORD 未生效）")
    print("数据卷： %s/data（agentgate.db + banks/ + results/）" % REMOTE_ROOT)
    print("端口：Web %d（UI）/ 接收器 %d（跨机 agent 轨迹）/ 录制代理 %d；Agent %d（:8200 默认）"
          % (args.web_port, args.receiver_port, args.proxy_port, args.agent_port))
    print("端口自定义：deploy_platform.py --web-port/--receiver-port/--proxy-port 重跑即可（env 每次启动覆盖设置表）")
    r.cli.close()


if __name__ == "__main__":
    main()
