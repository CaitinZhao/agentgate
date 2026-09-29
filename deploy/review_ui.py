"""Visual UI review: send page screenshots to GLM5.3-Flash (vision) via the LLM gateway.

Credentials are read from fin-runtime/.env at runtime only (never printed, never stored).
Usage: python agentgate/deploy/review_ui.py [screenshot_dir]
"""
import base64
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "ui-review"


def load_env():
    env = {}
    for line in (ROOT / "agentgate" / "tests" / "fixtures" / ".env").read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip("\r")
    return env


PROMPT = (
    "你是严格的前端 UI 审查员。这是 AgentGate 评测平台一个页面的截图（中文或英文界面）。"
    "请检查：1) 布局问题：元素重叠、错位、溢出、截断、表格挤压；"
    "2) 可读性：对比度不足、字号过小；3) 渲染错误：乱码、未渲染的模板变量、明显错别字；"
    "4) 交互暗示不清的地方。只列具体问题，每条一行，指明位置；没有问题只回答：无明显问题。"
)


def review(client, base, key, model, image_path):
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    r = client.post(base + "/chat/completions",
                    headers={"Authorization": "Bearer " + key},
                    json={"model": model, "temperature": 0.2,
                          "messages": [{"role": "user", "content": [
                              {"type": "image_url",
                               "image_url": {"url": "data:image/png;base64," + b64}},
                              {"type": "text", "text": PROMPT},
                          ]}]},
                    timeout=180)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def main():
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    env = load_env()
    base = (env.get("LLM_BASE_URL") or env.get("API_BASE") or "").rstrip("/")
    key = env.get("LLM_API_KEY") or env.get("API_KEY") or ""
    model = env.get("LLM_MODEL") or env.get("MODEL_NAME") or ""
    images = sorted(d.glob("*.png"))
    print("reviewing %d screenshots with %s ..." % (len(images), model))
    with httpx.Client() as client:
        for img in images:
            try:
                text = review(client, base, key, model, img)
            except Exception as e:
                text = "[review error] %r" % e
            print("\n=== %s ===" % img.name)
            print(text)


if __name__ == "__main__":
    main()
