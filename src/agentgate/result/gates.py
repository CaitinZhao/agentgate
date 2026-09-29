"""Gate: all-green decision / pass-fail-pending bucketing. Pending does not count as failure."""
from typing import Dict, List


def evaluate_gate(results: List[Dict]) -> Dict:
    pending = [r["task_id"] for r in results
               if r["scores"].get("human_review") == "pending"
               and r["scores"]["deterministic_pass"] and r["scores"]["rule_pass"]]
    failures = [r["task_id"] for r in results
                if not (r["scores"]["deterministic_pass"] and r["scores"]["rule_pass"])]
    suite = results[0]["eval_set_version"] if results else "example-v0.1"
    if failures:
        decision = "FAIL"
    elif pending:
        decision = "PENDING(%d)" % len(pending)
    else:
        decision = "GREEN"
    return {
        "gate": suite, "decision": decision,
        "failures": failures, "pending": pending,
        "counts": {"pass": len(results) - len(failures) - len(pending),
                   "fail": len(failures), "pending": len(pending),
                   "total": len(results)},
        "score": ("%d/%d" % (len(results) - len(failures) - len(pending),
                             len(results) - len(pending))
                  if (len(results) - len(pending)) > 0 else "n/a"),
        "note": "evaluation observes only; pending = free-text cases awaiting L3 judge/human review; accept decisions belong to the control plane",
    }
