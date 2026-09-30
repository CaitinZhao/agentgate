"""Guard tests for cross-file consistency drifts found in practice:

1. zh/en i18n messages must define the exact same key sets (the en run-detail
   batch keys were missing once — the UI fell back to raw key names).
2. deploy_eval.py's embedded agent Dockerfile templates must COPY the same
   fixture files as the repo Dockerfile.agent (the profile_prompts.py COPY was
   missed once and the deployed agent container crashed on boot).
"""
import io
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _extract_block(text: str, start: int) -> tuple:
    """Return (block_text, end_index) of the balanced {...} starting at `start`."""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1], i + 1
    raise ValueError("unbalanced braces")


def _keys_of_block(block: str, depth: int = 0, prefix: str = "") -> set:
    """Collect dotted key paths inside a message object block."""
    keys, i, n = set(), 0, len(block)
    key_re = re.compile(r"([A-Za-z0-9_]+)\s*:\s*")
    while i < n:
        ch = block[i]
        if ch in "\"'`":                       # skip string literals (values)
            quote = ch
            i += 1
            while i < n and block[i] != quote:
                i += 2 if block[i] == "\\" else 1
            i += 1
            continue
        m = key_re.match(block, i)
        if m:
            key, j = m.group(1), m.end()
            # find what follows: nested object or leaf value
            k = j
            while k < n and block[k] in " \t\r\n":
                k += 1
            if k < n and block[k] == "{":
                sub, end = _extract_block(block, k)
                keys.add(prefix + key)
                keys |= {prefix + key + "." + kk for kk in _keys_of_block(sub, 0)}
                i = end
                continue
            keys.add(prefix + key)
            i = j
            continue
        i += 1
    return keys


def _message_keys(lang_block: str) -> set:
    body, _ = _extract_block(lang_block, lang_block.index("{"))
    return _keys_of_block(body)


def test_zh_en_message_keys_match():
    text = (ROOT / "web" / "src" / "messages.ts").read_text(encoding="utf-8")
    # the file exports `export const zh = {...}` and `export const en: typeof zh = {...}`
    zh_m = re.search(r"export const zh\s*=\s*\{", text)
    en_m = re.search(r"export const en[^=]*=\s*\{", text)
    assert zh_m and en_m, "zh/en message objects not found"
    zh_keys = _message_keys(text[zh_m.start():])
    en_keys = _message_keys(text[en_m.start():])
    assert zh_keys == en_keys, (
        "zh-only: %s | en-only: %s" % (sorted(zh_keys - en_keys)[:12],
                                       sorted(en_keys - zh_keys)[:12]))


def test_deploy_agent_dockerfile_matches_repo_dockerfile():
    repo = (ROOT / "Dockerfile.agent").read_text(encoding="utf-8")
    deploy = (ROOT / "deploy" / "deploy_eval.py").read_text(encoding="utf-8")

    repo_copies = set(re.findall(r"COPY (tests/fixtures/\S+)", repo))
    # deploy_eval maps agentgate/<path> -> /app/<...>; check every fixture COPY exists there
    for src in repo_copies:
        deploy_src = "agentgate/" + src
        assert f"COPY {deploy_src} " in deploy, (
            f"deploy_eval.py embeds an agent Dockerfile that misses COPY {deploy_src} — "
            "the container will boot without it (this is how profile_prompts.py broke)")

    # runtime deps declared in the repo Dockerfile must exist in the embedded
    # requirements templates too (agentdojo was missing once)
    reqs = (ROOT / "deploy" / "deploy_eval.py").read_text(encoding="utf-8")
    pip_line = re.search(r"RUN pip install[^\n]*agentdojo==([0-9.]+)", repo)
    if pip_line:
        m2 = re.search(r'JIUWEN_REQUIREMENTS = """([^"]+)"""', reqs)
        assert m2 and f"agentdojo=={pip_line.group(1)}" in m2.group(1), (
            "agentdojo pinned in Dockerfile.agent is missing from JIUWEN_REQUIREMENTS")
