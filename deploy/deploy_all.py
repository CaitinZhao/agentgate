"""One-click deployment of the whole AgentGate evaluation environment to the remote server.

Usage (from anywhere; paths are resolved relative to this file):
  python agentgate/deploy/deploy_all.py                    # agent + platform + verify
  python agentgate/deploy/deploy_all.py --skip-agent       # platform only
  python agentgate/deploy/deploy_all.py --skip-platform    # agent only
  python agentgate/deploy/deploy_all.py --owner-password SECRET
  python agentgate/deploy/deploy_all.py --skip-build       # reuse remote images, just restart

Deployment order matters: the AGENT deploy lays down /opt/agentgate/fin-runtime/.env (the real
LLM-gateway credentials) — the platform's recording proxy reads its upstream from the
.env.origin snapshot of that file. Hence: agent first, then platform.

What ends up running on the remote host (all host-networked Docker):
  agentgate-web   :8030 web UI + API      (platform image, data volume /opt/agentgate-platform/data)
                  :4318 OTLP receiver     (traces from the agent)
                  :8300 recording proxy   (message-level LLM recording; upstream = real gateway)
  jiuwen-agent    :8200 the sample agent  (openJiuwen ReAct, profiles base/bank/locomo/longmem/fb/tau-*)
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent.parent                 # workspace root (contains agent-contracts/ agentgate/ fin-runtime/)

import deploy_platform as DP              # single source of the remote host credentials
import deploy_eval as DE

HOST = DP.HOST
REMOTE_PLATFORM = DP.REMOTE_ROOT
REMOTE_EVAL = DE.REMOTE_ROOT

GREEN, YELLOW, RED, DIM, NC = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def sh(cmd, cwd=None, timeout=3600):
    """Run a local subprocess, streaming its output (the two deploy scripts print progress)."""
    return subprocess.run(cmd, cwd=cwd, timeout=timeout, shell=(isinstance(cmd, str)))


def sh_retry(cmd, what, attempts=3, timeout=3600):
    """Deploy steps do 100+ MB SFTP uploads; a dropped connection is transient — retry."""
    for i in range(1, attempts + 1):
        rc = sh(cmd, timeout=timeout)
        if rc.returncode == 0:
            return 0
        warn("%s 失败（第 %d/%d 次尝试）" % (what, i, attempts))
        time.sleep(10)
    fail("%s 连续 %d 次失败" % (what, attempts))


def step(n, text):
    print("\n%s[%s]%s %s" % (GREEN, n, NC, text), flush=True)


def warn(text):
    print("%s[!] %s%s" % (YELLOW, text, NC), flush=True)


def fail(text):
    print("%s[x] %s%s" % (RED, text, NC), flush=True)
    sys.exit(1)


def local_preflight(skip_build: bool, need_agent_env: bool):
    step("0", "本地预检")
    try:
        import paramiko  # noqa: F401
    except ImportError:
        fail("缺少 paramiko：pip install paramiko（见 agentgate/deploy/requirements.txt）")
    missing = [k for k in ("AGENTGATE_DEPLOY_HOST", "AGENTGATE_DEPLOY_USER",
                           "AGENTGATE_DEPLOY_PASSWORD") if not os.environ.get(k)]
    if missing:
        fail("缺少部署目标环境变量：%s（服务器地址/账号/密码不入仓库）。"
             "示例：export AGENTGATE_DEPLOY_HOST=1.2.3.4 AGENTGATE_DEPLOY_USER=agent "
             "AGENTGATE_DEPLOY_PASSWORD='...'" % ",".join(missing))
    if need_agent_env and not (ROOT / "agentgate" / "tests" / "fixtures" / ".env").exists():
        fail("fin-runtime/.env 不存在：这是【被测 Agent 的模型凭据】（LLM_BASE_URL/LLM_API_KEY/"
             "LLM_MODEL_NAME，OpenAI 兼容），样例 Agent 离开它无法运行。只部署平台可加 --skip-agent 跳过；"
             "也可从旧部署的 /opt/agentgate/fin-runtime/.env.origin 拷回后放到 agentgate/tests/fixtures/.env。"
             "注意：它与用户中心的 AI 增强凭据是两回事，互不共用。")
    for item in ("agent-contracts/python", "agentgate/src", "agentgate/web",
                 "agentgate/deploy/fb_pdfs"):
        if not (ROOT / item).exists():
            fail("缺少目录 %s（请在仓库根目录运行，或检查仓库完整性）" % item)
    if not skip_build:
        npm = shutil.which("npm")
        if not npm:
            warn("未找到 npm：无法构建前端。若 agentgate/web/dist 已存在可继续，否则先安装 Node.js")
        web_modules = ROOT / "agentgate" / "web" / "node_modules"
        if web_modules.exists() and npm:
            print("    npm 前端构建可用（web/node_modules 就绪）")
        elif npm:
            warn("web/node_modules 缺失，首次构建前会自动执行 npm install（较慢）")
    print("    本地预检通过")


def _connect():
    import paramiko
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(DP.HOST, username=DP.USER, password=DP.PASSWORD, timeout=20,
                allow_agent=False, look_for_keys=False)
    return cli


def remote_preflight():
    step("1", "远程预检（%s）" % HOST)
    try:
        cli = _connect()
    except Exception as e:
        fail("SSH 连接失败：%s" % e)
    _, out, _ = cli.exec_command("docker version --format '{{.Server.Version}}' 2>&1", timeout=30)
    docker_v = out.read().decode().strip()
    if "Client" in docker_v or "command not found" in docker_v or not docker_v:
        fail("远程 docker 不可用：%s" % docker_v)
    print("    docker %s ✓" % docker_v)
    _, out, _ = cli.exec_command("df -h /opt | tail -1 | awk '{print $4}'", timeout=20)
    print("    /opt 可用空间：%s" % out.read().decode().strip())
    _, out, _ = cli.exec_command(
        "for p in 8030 4318 8300 8200; do ss -tln | grep -q ':$p ' && echo \"$p占用\" || true; done",
        timeout=20)
    busy = out.read().decode().strip()
    if busy:
        warn("端口被占用（部署脚本会按端口精确清理）： %s" % busy.replace("\n", "、"))
    cli.close()
    print("    远程预检通过")


def ensure_gateway_snapshot():
    """The platform proxy reads its upstream from the agent layout's .env.origin snapshot.
    deploy_eval only creates it in --proxy mode; make sure it exists after the agent deploy."""
    cli = _connect()
    cli.exec_command(
        "test -f %s/fin-runtime/.env.origin || "
        "cp %s/fin-runtime/.env %s/fin-runtime/.env.origin; "
        "grep -q '^LLM_BASE_URL=' %s/fin-runtime/.env.origin && echo origin_ok"
        % (REMOTE_EVAL, REMOTE_EVAL, REMOTE_EVAL, REMOTE_EVAL), timeout=20)[1].channel.recv_exit_status()
    cli.close()


def verify(args):
    """Post-deploy health matrix, executed from the remote host (ports may be firewalled
    to the public internet; the authoritative check runs where the containers live)."""
    step("V", "部署后体检（远程本机视角）")
    script = (
        "echo '--- platform /api/v1/health'; curl -s -m 5 http://127.0.0.1:%d/api/v1/health; echo; "
        "echo '--- proxy /health'; curl -s -m 5 http://127.0.0.1:%d/health; echo; "
        "echo '--- agent /health'; curl -s -m 5 http://127.0.0.1:%d/health; echo; "
        "echo '--- agent /capabilities'; curl -s -m 5 http://127.0.0.1:%d/capabilities; echo"
        % (args.web_port, args.proxy_port, args.agent_port, args.agent_port)
    )
    cli = _connect()
    _, out, _ = cli.exec_command(script, timeout=60)
    text = out.read().decode()
    # self-heal: a server/docker restart leaves the agent container stopped (Exited 0);
    # start it once and re-check instead of failing the whole verification
    if "/health" in text and "jiuwen-sample-agent" not in text:
        print("%s    agent 未运行，尝试 docker start jiuwen-agent ...%s" % (YELLOW, NC))
        cli.exec_command("docker start jiuwen-agent && sleep 6", timeout=60)[1].channel.recv_exit_status()
        _, out, _ = cli.exec_command(script, timeout=60)
        text = out.read().decode()
    print(text)
    ok = ('"status":"ok"' in text) and ("jiuwen-sample-agent" in text)
    cli.close()
    return ok


def main():
    ap = argparse.ArgumentParser(description="AgentGate 评测环境一键部署（agent + platform + 体检）")
    ap.add_argument("--skip-agent", action="store_true", help="跳过被测 Agent（jiuwen）部署")
    ap.add_argument("--skip-platform", action="store_true", help="跳过评测平台部署")
    ap.add_argument("--skip-build", action="store_true",
                    help="跳过打包/镜像构建，直接用远程现有镜像重启容器")
    ap.add_argument("--owner-password", default="",
                    help="平台 owner 初始密码（仅首次初始化数据卷时生效；留空随机生成）")
    ap.add_argument("--skip-verify", action="store_true", help="跳过部署后体检")
    ap.add_argument("--web-port", type=int, default=8030, help="平台 Web/API 端口（默认 8030）")
    ap.add_argument("--receiver-port", type=int, default=4318, help="OTLP 接收器端口（默认 4318）")
    ap.add_argument("--proxy-port", type=int, default=8300, help="录制代理端口（默认 8300）")
    ap.add_argument("--agent-port", type=int, default=8200, help="被测 Agent 宿主端口（默认 8200）")
    ap.add_argument("--data-dir", default=os.environ.get("AGENTGATE_DEPLOY_DATA_DIR", ""),
                    help="宿主机数据目录（挂载为容器 /app/data；默认 /opt/agentgate-platform/data）")
    args = ap.parse_args()

    t0 = time.time()
    local_preflight(skip_build=args.skip_build, need_agent_env=not args.skip_agent)
    remote_preflight()

    if not args.skip_agent:
        step("2", "部署被测 Agent（jiuwen 样例，:8200；含宿主依赖与镜像源配置）")
        sh_retry([sys.executable, str(HERE / "deploy_eval.py"), "--skip-run",
                  "--agent-port", str(args.agent_port)],
                 "Agent 部署", timeout=3600)
        ensure_gateway_snapshot()
        print("    网关凭据快照 .env.origin 就绪（平台录制代理的上游来源）")
    else:
        print("%s[skip]%s Agent 部署" % (YELLOW, NC))

    if not args.skip_platform:
        step("3", "部署评测平台（Web UI :8030 / 接收器 :4318 / 录制代理 :8300）")
        cmd = [sys.executable, str(HERE / "deploy_platform.py")]
        if args.data_dir:
            cmd += ["--data-dir", args.data_dir]
        if args.skip_build:
            cmd.append("--skip-build")
        if args.owner_password:
            cmd += ["--owner-password", args.owner_password]
        cmd += ["--web-port", str(args.web_port),
                "--receiver-port", str(args.receiver_port),
                "--proxy-port", str(args.proxy_port),
                "--agent-port", str(args.agent_port)]
        sh_retry(cmd, "平台部署", timeout=3600)
    else:
        print("%s[skip]%s 平台部署" % (YELLOW, NC))

    if not args.skip_verify:
        ok = verify(args)
        if not ok:
            warn("体检未全绿：检查上方各 /health 输出与 docker logs")

    print("=" * 68)
    print("%s部署完成（耗时 %.0f 分钟）%s" % (GREEN, (time.time() - t0) / 60, NC))
    print("""
  评测平台    http://%s:%d       （浏览器打开即 Web UI；owner 密码见上方 deploy 输出）
  被测 Agent  http://%s:%d       （/invoke、/capabilities；剖面 base/bank/locomo/longmem/fb/tau-*）
  数据卷      %s/data              （agentgate.db + banks/ + results/，升级部署不丢数据）
  Agent 部署  %s                   （fin-runtime/.env = 真实网关凭据；.env.origin = 其快照）

  下一步：
    1. 浏览器登录平台 → 题库页确认题库列表
    2. 发起评测：目标填 http://127.0.0.1:%d（同机 agent）；消息级录制默认开启
       （每次模型调用的完整消息留痕 → 轨迹分析/成本维/AI 摘要；不需录制时可取消勾选，agent 直连网关）
    3. 重新部署/升级：重跑本脚本即可（数据卷持久；正在跑的任务会被重排队）
    4. 换端口：--web-port/--receiver-port/--proxy-port/--agent-port 重跑即可
    5. 日志：ssh %s@%s 'docker logs --tail 100 agentgate-web / jiuwen-agent'
""" % (HOST, args.web_port, HOST, args.agent_port, REMOTE_PLATFORM, REMOTE_EVAL,
      args.agent_port, DP.USER, HOST))


if __name__ == "__main__":
    main()
