"""One-shot deployment: set up the evaluation environment on the remote server (openEuler/aarch64) and run an evaluation in Docker.

Usage (from the workspace root):
  python agentgate/deploy/deploy_eval.py [--suite example] [--agent jiuwen|fin]

Flow: pack & upload -> install deps -> pull images (with mirror fallback) ->
  -> build/start the target agent container -> run the chosen suite remotely -> fetch the report

Target agent, one of:
  jiuwen  jiuwen sample agent (openJiuwen ReActAgent, py3.11 container :8200, profiles base/bank)
  fin     fin-runtime fixture container (:8100, profiles bank/fb)
"""
import argparse
import os
import tarfile
from pathlib import Path

import paramiko

# Server credentials come from the environment (never committed to the repo):
#   AGENTGATE_DEPLOY_HOST / AGENTGATE_DEPLOY_USER / AGENTGATE_DEPLOY_PASSWORD
# Git Bash:  export AGENTGATE_DEPLOY_HOST=1.2.3.4 AGENTGATE_DEPLOY_USER=agent
#            export AGENTGATE_DEPLOY_PASSWORD='...'      (PowerShell: $env: same names)
HOST = os.environ.get("AGENTGATE_DEPLOY_HOST", "")
USER = os.environ.get("AGENTGATE_DEPLOY_USER", "")
PASSWORD = os.environ.get("AGENTGATE_DEPLOY_PASSWORD", "")
REMOTE_ROOT = "/opt/agentgate"
LOCAL_ROOT = Path(__file__).resolve().parents[2]
BUNDLE = Path(__file__).resolve().parent / "bundle.tar.gz"

BUNDLE_ITEMS = [
    "agent-contracts/pyproject.toml",
    "agent-contracts/schemas",
    "agent-contracts/python",
    "agentgate/pyproject.toml",
    "agentgate/src",
    "agentgate/cases",
    "agentgate/tests",          # fixtures incl. fin_runtime + data + .env (the agent's model credentials)
    "agentgate/deploy/fb_pdfs",
]

DEPS = ("pydantic fastapi uvicorn httpx typer pyyaml jsonschema "
        "opentelemetry-sdk opentelemetry-exporter-otlp opentelemetry-proto")

MIRRORS = [
    "",                                       # direct official registry
    "docker.m.daocloud.io/library/",
    "docker.1ms.run/library/",
]

FIN_DOCKERFILE = """FROM {base}
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY agentgate/tests/fixtures/fin_runtime /app/fin_runtime
COPY agentgate/tests/fixtures/data /app/data
ENV OTEL_EXPORTER_OTLP_ENDPOINT=http://172.17.0.1:4318
EXPOSE 8100
CMD ["python", "-m", "uvicorn", "fin_runtime.server:app", "--host", "0.0.0.0", "--port", "8100"]
"""

JIUWEN_DOCKERFILE = """FROM {base}
WORKDIR /app
COPY requirements-jiuwen.txt /app/requirements-jiuwen.txt
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \\
    -r /app/requirements-jiuwen.txt
COPY agentgate/tests/fixtures/fin_runtime /app/fin_runtime
COPY agentgate/tests/fixtures/data /app/data
COPY agentgate/tests/fixtures/jiuwen_server.py /app/jiuwen_server.py
COPY agentgate/tests/fixtures/jiuwen_agent.py /app/tests/fixtures/jiuwen_agent.py
COPY agentgate/tests/fixtures/tau_envs /app/tau_envs
COPY agentgate/deploy/fb_pdfs /app/fb_data
ENV FB_DATA_DIR=/app/fb_data
ENV FB_PDF_DIR=/app/fb_data
ENV JIUWEN_FIXTURE=/app/tests/fixtures/jiuwen_agent.py
ENV OTEL_EXPORTER_OTLP_ENDPOINT=http://172.17.0.1:4318
EXPOSE 8200
CMD ["python", "-m", "uvicorn", "jiuwen_server:app", "--host", "0.0.0.0", "--port", "8200"]
"""

FIN_REQUIREMENTS = """pydantic>=2.0
fastapi>=0.100
uvicorn>=0.23
httpx>=0.27
opentelemetry-sdk>=1.20
opentelemetry-exporter-otlp>=1.20
"""

JIUWEN_REQUIREMENTS = """pypdf>=4.0
openjiuwen==0.1.18
fastapi>=0.100
uvicorn>=0.23
httpx>=0.27
pydantic>=2.0
opentelemetry-sdk>=1.20
opentelemetry-exporter-otlp>=1.20
"""

AGENTS = {
    "fin": {"port": 8100, "image": "fin-runtime:0.1",
            "base": "python:3.9-slim", "dockerfile": FIN_DOCKERFILE,
            "requirements": FIN_REQUIREMENTS, "req_name": "requirements.txt",
            "container": "fin-runtime"},
    "jiuwen": {"port": 8200, "image": "jiuwen-agent:0.1",
               "base": "python:3.11-slim", "dockerfile": JIUWEN_DOCKERFILE,
               "requirements": JIUWEN_REQUIREMENTS, "req_name": "requirements-jiuwen.txt",
               "container": "jiuwen-agent"},
}


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

    def read_text(self, remote: str) -> str:
        with self.sftp.open(remote, "r") as f:
            return f.read().decode("utf-8")


def make_bundle():
    print("[1] packing local code and data ...")
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
    print("    bundle: %.1f KB" % (BUNDLE.stat().st_size / 1024))


def pull_image(r: Remote, image: str) -> str:
    """Pull an image: official registry -> mirror fallback chain. Returns the image ref in use."""
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


def install_host_deps(r: Remote):
    """Idempotent host pip dependency install (marker file skips it)."""
    marker = "%s/.deps_ok" % REMOTE_ROOT
    code, _ = r.run("test -f %s && echo ok" % marker, show_tail=0)
    if code == 0:
        print("    宿主依赖已装（标记 %s），跳过" % marker)
        return
    print("    升级远端 pip 并安装 python 依赖 ...")
    r.run("python3 -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple "
          "2>&1 | tail -1", timeout=600)
    r.run("cd %s && python3 -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple %s "
          "2>&1 | tail -1" % (REMOTE_ROOT, DEPS), timeout=1800)
    r.run("touch %s" % marker)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="example", help="远程跑测的套件名（默认 example）")
    ap.add_argument("--cases-file", default=None,
                    help="题目文件（json/jsonl）替代套件名，如 cases/locomo/sample-40.jsonl（抽样验证）")
    ap.add_argument("--agent", choices=["jiuwen", "fin"], default="jiuwen",
                    help="被测 Agent：jiuwen（openJiuwen 样例）/ fin（fin-runtime 夹具）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（抽样估成本）")
    ap.add_argument("--case-ids", default="", help="仅跑指定题目，逗号分隔")
    ap.add_argument("--skip-run", action="store_true", help="只部署不跑测")
    ap.add_argument("--skip-deploy", action="store_true",
                    help="跳过打包部署，直接用远程现有容器跑测")
    ap.add_argument("--agent-port", type=int, default=8200,
                    help="宿主机映射端口（默认 8200；容器内固定 8200）")
    ap.add_argument("--proxy", action="store_true",
                    help="启用 llm-proxy 录制：远端起录制代理，agent 流量经代理，"
                         "评测归集消息级记录（报告多一节轨迹分析）")
    args = ap.parse_args()

    spec = AGENTS[args.agent]
    r = Remote()
    print("[0] 连接 %s（agent=%s suite=%s）" % (HOST, args.agent, args.suite))

    if not args.skip_deploy:
        print("[1] 打包与上传 ...")
        make_bundle()
        r.run("mkdir -p %s/cases" % REMOTE_ROOT)
        r.put(BUNDLE, REMOTE_ROOT + "/bundle.tar.gz")
        r.run("cd %s && tar -xzf bundle.tar.gz && rm bundle.tar.gz" % REMOTE_ROOT)

        print("[2] 宿主 pip 依赖（幂等）...")
        install_host_deps(r)

        print("[3] 配置 daemon 镜像源（幂等，已配置不重启 docker）...")
        code, text = r.run("docker info 2>/dev/null | grep -c daocloud || true", show_tail=0)
        if text.strip() and text.strip() != "0":
            print("    镜像源已配置，跳过")
        else:
            r.run("mkdir -p /etc/docker && test -f /etc/docker/daemon.json || "
                  "echo '{}' > /etc/docker/daemon.json")
            r.run("python3 -c \"import json;p='/etc/docker/daemon.json';d=json.load(open(p));"
                  "m=d.setdefault('registry-mirrors',[]);"
                  "[m.append(x) for x in ['https://docker.m.daocloud.io'] if x not in m];"
                  "json.dump(d,open(p,'w'),indent=2)\" && systemctl restart docker", timeout=300)
            r.run("sleep 3 && docker info 2>/dev/null | grep -A2 -i mirror || true")

        print("[4] 拉取基础镜像（含镜像源回退）...")
        base = pull_image(r, spec["base"])

        print("[5] 构建并启动 %s 容器（:%d）..." % (args.agent, spec["port"]))
        r.put_text(REMOTE_ROOT + "/" + spec["req_name"], spec["requirements"])
        r.put_text(REMOTE_ROOT + "/Dockerfile", spec["dockerfile"].format(base=base))
        r.run("cd %s && DOCKER_BUILDKIT=0 docker build -t %s -f Dockerfile . "
              "2>&1 | tail -1" % (REMOTE_ROOT, spec["image"]), timeout=1800)
        r.run("docker rm -f %s 2>/dev/null; true" % spec["container"])
        r.run("sed -i 's|^OTEL_EXPORTER_OTLP_ENDPOINT=.*|OTEL_EXPORTER_OTLP_ENDPOINT=http://172.17.0.1:4318|' "
              "%s/agentgate/tests/fixtures/.env" % REMOTE_ROOT)
        extra_env = ["-e AGENT_ID=jiuwen-sample-agent"] if args.agent == "jiuwen" else []
        code, _ = r.run("docker run -d --name %s -p %d:8200 %s "
                        "--env-file %s/agentgate/tests/fixtures/.env %s"
                        % (spec["container"], args.agent_port,
                           " ".join(extra_env), REMOTE_ROOT, spec["image"]))
        if code != 0:
            raise RuntimeError("%s 容器启动失败" % spec["container"])
        r.run("sleep 5 && curl -s http://127.0.0.1:%d/health" % args.agent_port, timeout=120)

    if args.proxy:
        print("[5b] 启动 llm-proxy 录制代理（:8300）...")
        pypath = "%s/agent-contracts/python:%s/agentgate/src" % (REMOTE_ROOT, REMOTE_ROOT)
        sink = "%s/results/llm_calls/current.jsonl" % REMOTE_ROOT
        #clean up a stale proxy (kill precisely by port); read the real gateway from .env.origin -
        #.env may have been rewritten by a previous --proxy run to point at the proxy itself (self-loop incident); origin holds the real gateway
        r.run("pid=$(ss -tlnp 2>/dev/null | grep ':8300 ' | grep -oP 'pid=\\K[0-9]+' | head -1); "
              "[ -n \"$pid\" ] && kill $pid; sleep 1; "
              "test -f %s/fin-runtime/.env.origin || "
              "cp %s/agentgate/tests/fixtures/.env %s/fin-runtime/.env.origin; true"
              % (REMOTE_ROOT, REMOTE_ROOT, REMOTE_ROOT), show_tail=0)
        r.run("rm -f %s; "
              "up=$(grep -oP '^LLM_BASE_URL=\\K.*' %s/fin-runtime/.env.origin | tr -d '\\r'); "
              "echo upstream=$up; "
              "PYTHONPATH=%s nohup python3 -m agentgate.cli.main llm-proxy "
              "--upstream \"$up\" --port 8300 --sink %s "
              "> /tmp/llm-proxy.log 2>&1 & sleep 2; "
              "curl -s http://127.0.0.1:8300/health || cat /tmp/llm-proxy.log | tail -5"
              % (_shq(sink), REMOTE_ROOT, _shq(pypath), _shq(sink)), timeout=60, show_tail=1)
        #route agent traffic through the proxy (container reaches the host proxy via the bridge address);
        #note: docker restart does NOT re-read env-file — remove and recreate the container
        r.run("sed -i 's|^LLM_BASE_URL=.*|LLM_BASE_URL=http://172.17.0.1:8300/v1|; "
              "s|^API_BASE=.*|API_BASE=http://172.17.0.1:8300/v1|' "
              "%s/agentgate/tests/fixtures/.env" % REMOTE_ROOT, show_tail=0)
        extra_env2 = ["-e AGENT_ID=jiuwen-sample-agent"] if args.agent == "jiuwen" else []
        r.run("docker rm -f %s >/dev/null 2>&1; docker run -d --name %s -p %d:8200 %s "
              "--env-file %s/agentgate/tests/fixtures/.env %s >/dev/null; sleep 5; "
              "curl -s http://127.0.0.1:%d/health"
              % (spec["container"], spec["container"], args.agent_port,
                 " ".join(extra_env2), REMOTE_ROOT, spec["image"], args.agent_port),
              timeout=120, show_tail=1)
        proxy_env = "AGENTGATE_PROXY_SINK=%s" % sink
    else:
        proxy_env = ""

    if args.skip_run:
        print("部署完成（--skip-run，不跑测）")
        r.cli.close()
        return

    out_dir = "results/run-remote-%s-%s" % (args.agent, args.suite)
    cases_arg = args.cases_file or args.suite
    print("[6] remote evaluation (%s, target=%s :%d) ..." % (cases_arg, args.agent, spec["port"]))
    # a stale eval process holds the OTLP receiver port (EADDRINUSE) and silently fails the run;
    # kill precisely by listening port (never pkill -f on command lines — it kills the wrapping shell too)
    r.run("pid=$(ss -tlnp 2>/dev/null | grep ':4318 ' | grep -oP 'pid=\\K[0-9]+' | head -1); "
          "if [ -n \"$pid\" ]; then kill $pid; sleep 1; echo killed_stale=$pid; "
          "else echo '4318 ok'; fi", show_tail=1)
    pypath = "%s/agent-contracts/python:%s/agentgate/src" % (REMOTE_ROOT, REMOTE_ROOT)
    cmd = ("cd %s/agentgate && PYTHONPATH=%s %s python3 -m agentgate.cli.main run "
           "--cases %s --target http://127.0.0.1:%d --out %s/%s"
           % (REMOTE_ROOT, _shq(pypath), proxy_env, _shq(cases_arg), args.agent_port,
              REMOTE_ROOT, out_dir))
    if args.limit:
        cmd += " --limit %d" % args.limit
    if args.case_ids:
        cmd += " --case-ids '%s'" % args.case_ids
    code, text = r.run("{ %s 2>&1 | tail -10; echo EVAL_RC=${PIPESTATUS[0]}; }" % cmd,
                       timeout=7200, show_tail=0)
    eval_rc = [l for l in text.splitlines() if l.strip().startswith("EVAL_RC=")]
    rc_val = eval_rc[-1].split("=")[-1].strip() if eval_rc else "?"
    if rc_val != "0":
        print("    [!] 远程跑测失败（rc=%s），输出尾部：" % rc_val)
        for line in text.strip().splitlines()[-12:]:
            print("    | %s" % line[:200])

    print("[7] fetch the report back ...")
    r.run("rm -f %s/report-remote.md" % REMOTE_ROOT)   # prevent re-fetching a stale report
    r.run("cp %s/%s/report.md %s/report-remote.md 2>/dev/null; true"
          % (REMOTE_ROOT, out_dir, REMOTE_ROOT))
    code, _ = r.run("test -f %s/report-remote.md" % REMOTE_ROOT, show_tail=0)
    if code != 0:
        print("    [!] 报告未生成（远程跑测可能失败）——查看上方输出或远程 %s" % out_dir)
        r.cli.close()
        return
    report = r.read_text(REMOTE_ROOT + "/report-remote.md")
    local_report = Path(__file__).parent / "report-remote.md"
    with local_report.open("w", encoding="utf-8") as f:
        f.write(report)
    print("报告已回传：agentgate/deploy/report-remote.md（远程：%s）" % out_dir)
    print(report[:600])
    r.cli.close()


if __name__ == "__main__":
    main()
