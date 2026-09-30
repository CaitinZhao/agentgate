"""Import the public-benchmark banks (W21) into a deployed AgentGate platform.

Run AFTER deploy_all.py — it creates the bank entries via the API, uploads the generated
cases.jsonl files, imports them into each bank's SQLite, and ships the bank-side assets
(spider DBs) the platform's native scorers resolve through the bank directory.

Usage:
  export AGENTGATE_DEPLOY_HOST=... AGENTGATE_DEPLOY_USER=... AGENTGATE_DEPLOY_PASSWORD=...
  python agentgate/deploy/import_banks.py            # create banks + import cases + assets
  python agentgate/deploy/import_banks.py --banks bfcl,gaia   # subset

API login: the self-test admin account qa-robot / qa-robot-pass-1 (overridable via
AGENTGATE_QA_USER / AGENTGATE_QA_PASSWORD). Bank creation requires admin.
"""
import argparse
import json
import os
import sys
import tarfile
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CASES = ROOT / "agentgate" / "cases"

GREEN, YELLOW, RED, NC = "\033[32m", "\033[33m", "\033[31m", "\033[0m"

PLATFORM_PORT = 8030
DATA_DIR = "/opt/agentgate-platform/data"       # volume root on the HOST (sftp side)
CONTAINER_DATA = "/app/data"                    # the same volume inside agentgate-web
QA_USER = "qa-robot"                            # import banks are public -> fixed db nesting

# bank -> (display, category, default_level, requirements, local cases.jsonl, assets dir)
BANKS = {
    "bfcl": ("BFCL 函数调用（v4 抽样）", "open-benchmark", "L1",
             {"profile": "bfcl", "pack": "bfcl"}, "bfcl/cases.jsonl", None),
    "spider": ("Spider 文本到 SQL（8 库全题）", "open-benchmark", "L1",
               {"profile": "spider", "pack": "spider", "env_scope": "judge-only"}, "spider/cases.jsonl", "spider/dbs"),
    "gaia": ("GAIA 通用助手（validation 文本子集）", "open-benchmark", "L2",
             {"profile": "gaia", "pack": "gaia"}, "gaia/cases.jsonl", None),
    "airbench": ("AIR-Bench 检索（qa/wiki/en dev 子集）", "open-benchmark", "L1",
                 {"profile": "airbench", "pack": "airbench"}, "airbench/cases.jsonl", None),
    "agentdojo": ("AgentDojo 工具任务与注入（v1_2_2）", "open-benchmark", "L2",
                  {"profile": "agentdojo", "pack": "agentdojo"},
                  "agentdojo/cases.jsonl", None),
    "harmbench": ("HarmBench 安全红队（standard）", "open-benchmark", "L2",
                  {"profile": "harmbench", "pack": "harmbench", "tool_strict": False},
                  "harmbench/cases.jsonl", None),
}


def fail(text):
    print("%s[x] %s%s" % (RED, text, NC))
    sys.exit(1)


def _client():
    try:
        import httpx
    except ImportError:
        fail("缺少 httpx：pip install httpx")
    return httpx.Client(base_url="http://%s:%d" % (os.environ["AGENTGATE_DEPLOY_HOST"],
                                                   PLATFORM_PORT), timeout=60)


def _login(http: "httpx.Client") -> None:
    user = os.environ.get("AGENTGATE_QA_USER", "qa-robot")
    pw = os.environ.get("AGENTGATE_QA_PASSWORD", "qa-robot-pass-1")
    r = http.post("/api/v1/auth/login", json={"username": user, "password": pw})
    if r.status_code != 200:
        fail("API 登录失败（%s）：%s" % (user, r.text[:200]))
    print("    API 登录 ✓ (%s)" % user)


def _connect_sftp():
    import paramiko
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(os.environ["AGENTGATE_DEPLOY_HOST"], username=os.environ["AGENTGATE_DEPLOY_USER"],
                password=os.environ["AGENTGATE_DEPLOY_PASSWORD"], timeout=20,
                allow_agent=False, look_for_keys=False)
    return cli



def _bank_visibility(http, name: str) -> str:
    """Public banks nest at banks/public/<name>, private ones under the owner."""
    r = http.get("/api/v1/benchmarks/%s" % name)
    if r.status_code == 200:
        return r.json().get("visibility", "public")
    return "public"                       # import_one creates it as public next


def import_one(http, cli, name: str) -> None:
    display, category, level, requirements, cases_rel, assets_rel = BANKS[name]
    r = http.get("/api/v1/benchmarks/%s" % name)
    if r.status_code == 404:
        r = http.post("/api/v1/benchmarks", json={
            "name": name, "display_name": display, "category": category,
            "default_level": level, "visibility": "public",
            "description_zh": "开源 benchmark 数据集改编（防污染：只做观测对标，不作 accept 判据）",
            "source_note": "public-benchmark", "requirements": requirements})
        if r.status_code != 200:
            fail("创建题库 %s 失败：%s" % (name, r.text[:300]))
        print("    题库创建 ✓")
    else:
        print("    题库已存在，跳过创建")
    cases_file = CASES / cases_rel
    if not cases_file.is_file():
        fail("cases 文件不存在：%s（先跑 build_banks）" % cases_file)
    sftp = cli.open_sftp()
    # staging lives under the data volume: the host path for sftp, /app/data inside
    # the container (docker exec can only see the volume, not the host's /tmp)
    tmp_host = "%s/tmp-import/%s" % (DATA_DIR, name)
    tmp_ctr = "%s/tmp-import/%s" % (CONTAINER_DATA, name)
    db_ctr = "%s/banks/%s/%s/cases.db" % (CONTAINER_DATA,
                                          "public" if _bank_visibility(http, name) == "public"
                                          else "users/%s" % QA_USER, name)
    try:
        cli.exec_command("mkdir -p %s" % tmp_host)[1].channel.recv_exit_status()
        sftp.put(str(cases_file), "%s/cases.jsonl" % tmp_host)
        if assets_rel:
            assets = CASES / assets_rel
            fd, tar_local = tempfile.mkstemp(suffix=".tar.gz")
            os.close(fd)
            with tarfile.open(tar_local, "w:gz") as tar:
                tar.add(str(assets), arcname="dbs")
            sftp.put(tar_local, "%s/assets.tar.gz" % tmp_host)
            os.unlink(tar_local)
        # import inside the platform container (module lives in the image; the volume
        # is mounted at /app/data, and bank dbs nest under banks/public|users/<owner>)
        cmd = ("docker exec agentgate-web python -m agentgate.case.build_banks "
               "--db-import %s/cases.jsonl --db %s" % (tmp_ctr, db_ctr))
        _, out, err = cli.exec_command(cmd, timeout=300)
        rc = out.channel.recv_exit_status()
        if rc != 0:
            fail("db-import %s 失败：%s" % (name, err.read().decode()[:600]))
        print("    cases 导入 ✓ (%s)" % out.read().decode().strip())
        if assets_rel:
            cmd = ("docker exec agentgate-web sh -c "
                   "'tar xzf %s/assets.tar.gz -C %s'" % (tmp_ctr, db_ctr.rsplit("/", 1)[0]))
            _, out, err = cli.exec_command(cmd, timeout=300)
            if out.channel.recv_exit_status() != 0:
                fail("资产解压失败：%s" % err.read().decode()[:300])
            print("    资产（dbs）✓")
        cli.exec_command("rm -rf %s" % tmp_host)[1].channel.recv_exit_status()
    finally:
        sftp.close()
    time.sleep(1)
    r = http.get("/api/v1/benchmarks/%s" % name)
    if r.status_code == 200:
        print("    验证：%s 共 %s 题" % (name, r.json().get("case_count")))


def main():
    global DATA_DIR, CONTAINER_DATA
    ap = argparse.ArgumentParser()
    ap.add_argument("--banks", default=",".join(BANKS))
    ap.add_argument("--data-dir", default=os.environ.get(
        "AGENTGATE_DEPLOY_DATA_DIR", DATA_DIR),
        help="宿主机数据目录（默认 /opt/agentgate-platform/data）")
    ap.add_argument("--container-data-dir", default=os.environ.get(
        "AGENTGATE_CONTAINER_DATA_DIR", CONTAINER_DATA),
        help="容器内对应路径（默认 /app/data）")
    args = ap.parse_args()
    DATA_DIR, CONTAINER_DATA = args.data_dir, args.container_data_dir
    for k in ("AGENTGATE_DEPLOY_HOST", "AGENTGATE_DEPLOY_USER", "AGENTGATE_DEPLOY_PASSWORD"):
        if not os.environ.get(k):
            fail("缺少环境变量 %s（部署目标凭据不入仓库）" % k)
    http = _client()
    _login(http)
    cli = _connect_sftp()
    # detect the in-container data dir (matches the AGENTGATE_DATA_DIR the platform
    # was started with) so staging paths always line up with --db paths
    _, out, _ = cli.exec_command(
        "docker exec agentgate-web printenv AGENTGATE_DATA_DIR 2>/dev/null || echo /app/data",
        timeout=30)
    detected = out.read().decode().strip()
    if detected and detected != CONTAINER_DATA:
        print("    容器内数据目录：%s" % detected)
        CONTAINER_DATA = detected
    try:
        for name in [b.strip() for b in args.banks.split(",") if b.strip()]:
            if name not in BANKS:
                fail("未知题库：%s（可选：%s）" % (name, ",".join(BANKS)))
            print("%s[import]%s %s" % (GREEN, NC, name))
            import_one(http, cli, name)
    finally:
        cli.close()
    print("%s全部完成%s" % (GREEN, NC))


if __name__ == "__main__":
    main()
