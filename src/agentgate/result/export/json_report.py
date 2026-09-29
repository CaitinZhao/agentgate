"""Result export: eval_results.json (contract-validated) + runs_meta/gate + failures.jsonl."""
import json
from pathlib import Path
from typing import Dict, List

from agent_contracts import validate_dict


def export(out_dir: str, results: List[Dict], runs_meta: List[Dict], gate: Dict) -> Dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for r in results:
        ok, err = validate_dict(r, "eval_result")
        if not ok:
            raise ValueError("EvalResult violates contract %s: %s" % (r.get("task_id"), err))
    (out / "eval_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "runs_meta.json").write_text(
        json.dumps(runs_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "gate.json").write_text(
        json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [r for r in results if r["task_id"] in gate.get("failures", [])]
    if failures:
        with (out / "failures.jsonl").open("w", encoding="utf-8", newline=chr(10)) as fh:
            for r in failures:
                slim = {"task_id": r["task_id"], "ASI": r["ASI"],
                        "attribution": r["attribution"],
                        "error_localization": r.get("error_localization"),
                        "agent_answer": next((m.get("answer_preview", "")
                                              for m in runs_meta
                                              if m["task_id"] == r["task_id"]), "")}
                fh.write(json.dumps(slim, ensure_ascii=False) + chr(10))
    return {"results": str(out / "eval_results.json"), "gate": str(out / "gate.json")}
