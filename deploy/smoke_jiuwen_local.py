"""Local smoke for jiuwen_server (real uvicorn subprocess, deployment-like):
injects the LLM env from fin-runtime/.env (credentials never written), serves :8200 -> checks base/bank profiles.

Usage: python agentgate/deploy/smoke_jiuwen_local.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "agentgate" / "tests" / "fixtures"

for line in (ROOT / "agentgate" / "tests" / "fixtures" / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")

PORT = 8200
env = dict(os.environ)
env["PYTHONPATH"] = str(ROOT / "agentgate" / "tests" / "fixtures") + os.pathsep + env.get("PYTHONPATH", "")
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "jiuwen_server:app",
     "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(FIXTURES), env=env,
    stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
try:
    for _ in range(60):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=2)
            break
        except Exception:
            time.sleep(1)
    print("health:", urllib.request.urlopen(
        "http://127.0.0.1:%d/health" % PORT, timeout=5).read().decode())

    def post(payload):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/invoke" % PORT,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=180).read().decode())

    r = post({"query": "回复两个字：连通", "profile": "base"})
    print("base  trace=%s" % r["trace_id"][:8])
    print("base  answer[:120]:", r["answer_text"][:120].replace("\n", " | "))

    r = post({"query": "银行X 2026H1 营收同比是多少（%）？请按最新可得口径计算。",
              "profile": "bank"})
    print("bank  trace=%s" % r["trace_id"][:8])
    print("bank  final_json:", r["final_json"])
    print("bank  answer[:200]:", r["answer_text"][:200].replace("\n", " | "))
finally:
    proc.terminate()
