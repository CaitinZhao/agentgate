"""Probe the LLM gateway's prompt length limit (error code 1261 threshold) to size the memory-profile truncation budget.

Usage: python agentgate/deploy/probe_llm_limit.py
(credentials read from fin-runtime/.env, never written anywhere; sends only 3-5 probe requests)
"""
import json
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
env = {}
for line in (ROOT / "fin-runtime" / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

URL = env["LLM_BASE_URL"].rstrip("/") + "/chat/completions"
WORDS = ("memory session dialogue conversation context user assistant task item "
         "detail note record event plan schedule preference message reply ")
FILLER = (WORDS * 4200)[:200000]      # English is ~4 chars/token


def probe(chars: int) -> str:
    body = json.dumps({
        "model": env["LLM_MODEL"],
        "messages": [{"role": "user", "content": "请只回复OK。以下是无关填充文本：\n" + FILLER[:chars]}],
        "max_tokens": 8,
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + env["LLM_API_KEY"]})
    try:
        urllib.request.urlopen(req, timeout=120).read()
        return "OK"
    except Exception as e:
        return "FAIL: %s" % str(e)[:120]


for chars in (32000, 64000, 96000, 128000, 160000):
    print("%7d chars -> %s" % (chars, probe(chars)))
