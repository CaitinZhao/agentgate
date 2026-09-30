"""Final-state assertion evaluation (the judging side of the sandbox).

Env assertions live in gold.final.assertions next to the legacy final_json path
assertions; an entry is an env assertion when it carries "read_file" or "exec":

  {"read_file": "/app/out/report.json", "json_path": "status", "equals": "done"}
  {"read_file": "/app/out/notes.txt", "contains": "DONE"}
  {"exec": "test -f /app/out/task.bak", "exit_code": 0}
  {"exec": "cat /app/out/summary.txt", "contains": "monthly"}

Every assertion must pass; any miss is a hard FAIL. When the case declares env
assertions but no sandbox handle reached the judge, the verdict is undecided
(None -> PENDING) — never guessed.
"""
import json
from typing import Dict, List, Optional, Tuple

from ..evaluator.base import VerifierResult

from .base import looks_like_env_assertion, SandboxHandle


def env_assertions(case) -> List[Dict]:
    gfinal = (getattr(case.gold, "final", None) or {})
    out = gfinal.get("assertions") or []
    return [a for a in out if looks_like_env_assertion(a)]


def _walk_json(node, dotted: str):
    for part in str(dotted or "").split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def verify_env_assertion(a: Dict, handle: SandboxHandle) -> Tuple[bool, str]:
    """One env assertion -> (ok, detail-on-miss)."""
    try:
        if "read_file" in a:
            path = str(a["read_file"])
            try:
                content = handle.read_file(path)
            except Exception as e:
                return False, "read_file %s unavailable: %s" % (path, str(e)[:160])
            if "json_path" in a:
                try:
                    node = _walk_json(json.loads(content), a["json_path"])
                except ValueError:
                    return False, "read_file %s is not valid JSON" % path
                ok = node == a.get("equals")
                return ok, "" if ok else ("json %s expected %r, got %r"
                                          % (a["json_path"], a.get("equals"), node))
            if "contains" in a:
                ok = str(a["contains"]) in content
                return ok, "" if ok else ("file %s missing %r" % (path, a["contains"]))
            if "equals" in a:
                ok = content.strip() == str(a["equals"])
                return ok, "" if ok else ("file %s expected %r, got %r"
                                          % (path, a["equals"], content[:120]))
            return False, "read_file assertion has no equals/contains/json_path"
        if "exec" in a:
            out = handle.exec(str(a["exec"]), timeout=float(a.get("timeout") or 30))
            if "exit_code" in a:
                ok = out["exit_code"] == a["exit_code"]
                return ok, "" if ok else ("exec %r exit %d (expected %s): %s"
                                          % (a["exec"], out["exit_code"], a["exit_code"],
                                             (out["stderr"] or out["stdout"])[-120:]))
            if "contains" in a:
                ok = str(a["contains"]) in (out["stdout"] or "")
                return ok, "" if ok else ("exec %r stdout missing %r" % (a["exec"], a["contains"]))
            ok = out["exit_code"] == 0
            return ok, "" if ok else ("exec %r exit %d: %s"
                                      % (a["exec"], out["exit_code"], (out["stderr"] or "")[-120:]))
    except Exception as e:                       # broken handle / docker gone: hard miss
        return False, "sandbox error: %s" % str(e)[:160]
    return False, "assertion is neither read_file nor exec"


def verify_final_state(case, handle: Optional[SandboxHandle]) -> Optional[Tuple[bool, List[VerifierResult]]]:
    """All env assertions of the case -> (all_passed, verifier_results).

    None = the case has env assertions but no sandbox handle (honest PENDING).
    """
    asserts = env_assertions(case)
    if not asserts:
        return None
    if handle is None:
        return None
    ok_all, results = True, []
    for a in asserts:
        ok, detail = verify_env_assertion(a, handle)
        ok_all = ok_all and ok
        label = a.get("read_file") or a.get("exec")
        results.append(VerifierResult(
            ok=ok, layer="L1", name="state",
            reason="" if ok else "environment state assertion failed: %s (%s)" % (label, detail),
            asi="" if ok else ("environment state %r not as expected: %s" % (label, detail)),
            failed_step=None if ok else "final"))
    return ok_all, results
