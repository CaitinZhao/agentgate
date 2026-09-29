"""Vendor tau-bench tools and domain data into the jiuwen sample (minimal slice).

Takes only envs/tool.py + both domains' tools/ and data/ (pure json+stdlib, no litellm dependency);
rewrites the import prefix tau_bench.envs -> tau_envs. wiki.md is copied for the system prompt.

Usage: python agentgate/deploy/vendor_tau.py
"""
import re
import shutil
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "reference" / "refs" / "code" / "tau-bench" / "tau_bench" / "envs"
DST = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tau_envs"


def copy_tools(domain: str):
    src_tools = SRC / domain / "tools"
    dst_tools = DST / domain / "tools"
    dst_tools.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(src_tools.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        text = text.replace("from tau_bench.envs.tool import Tool",
                            "from tau_envs.tool import Tool")
        (dst_tools / f.name).write_text(text, encoding="utf-8")
        n += 1
    shutil.copytree(SRC / domain / "data", DST / domain / "data", dirs_exist_ok=True)
    shutil.copy2(SRC / domain / "wiki.md", DST / domain / "wiki.md")   # policy text goes into the system prompt
    (DST / domain / "__init__.py").write_text("", encoding="utf-8")
    print("%s: %d tool files + data + wiki" % (domain, n))


def main():
    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True)
    (DST / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(SRC / "tool.py", DST / "tool.py")
    copy_tools("airline")
    copy_tools("retail")
    # verify: no tau_bench references may remain in the vendored copy
    bad = [str(f) for f in DST.rglob("*.py") if "tau_bench" in f.read_text(encoding="utf-8")]
    print("residual tau_bench refs:", bad or "none")


if __name__ == "__main__":
    main()
