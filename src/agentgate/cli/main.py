"""agentgate CLI: run (evaluate a case bank) / serve (REST) / proxy / compare / analyze."""
from typing import List, Optional

import typer

from ..config import load_config

from pathlib import Path

CFG = load_config()
app = typer.Typer(help="AgentGate：企业级 Agent 评测 harness（P0）")


@app.command()
def run(cases: str = typer.Option(CFG["default_cases"], help="套件名（如 example）或 .db 题库库路径；默认跑库内全部激活题"),
        target: str = typer.Option(CFG["target_base_url"], help="被测目标 base URL"),
        out: Optional[str] = typer.Option(None, help="结果输出目录"),
        cases_file: str = typer.Option(None, help="文件来源（json/jsonl/db，入库前试用场景）"),
        case_ids: Optional[str] = typer.Option(None, help="仅跑指定题目，逗号分隔"),
        limit: Optional[int] = typer.Option(None, help="只跑前 N 题"),
        level: Optional[str] = typer.Option(None, help="按分级批量跑（逗号分隔，如 L0,L1）"),
        port: int = typer.Option(CFG["receiver_port"], help="OTLP 接收器端口")):
    """Run a case bank: collect traces -> judge -> gate -> baseline report."""
    import datetime
    from ..control.service import run_case_set
    from ..run.targets.base import HttpTarget
    out_dir = out or ("%s/run-%s" % (CFG["results_root"], datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    tgt = HttpTarget(target)
    summary = run_case_set(cases, tgt, out_dir, receiver_port=port,
                           case_ids=(case_ids.split(",") if case_ids else None),
                           limit=limit, levels=(level.split(",") if level else None),
                           cases_file=cases_file)
    typer.echo("gate: %s" % summary["gate"]["decision"])
    for r in summary["results"]:
        mark = "PASS" if (r["scores"]["deterministic_pass"] and r["scores"]["rule_pass"]) else "FAIL"
        typer.echo("  [%s] %s" % (mark, r["task_id"]))
    typer.echo("report: %s" % summary["paths"]["report"])


@app.command()
def case_level(db: str = typer.Option("cases/cases.db", help="SQLite 题库库路径"),
               set_levels: str = typer.Option(..., "--set", help="批量调整分级：id=L0,id2=L1"),
               set_status: str = typer.Option(None, help="批量调整状态：id=retired,..."),
               status_mode: bool = typer.Option(False, help="--set 的值按状态解析（active/retired）")):
    """Dynamic case level/status adjustment (command alternative to API/config)."""
    from ..case.store import update_levels, update_status
    mapping = {}
    for pair in set_levels.split(","):
        if "=" in pair:
            k, _, v = pair.partition("=")
            mapping[k.strip()] = v.strip()
    if status_mode:
        n = update_status(db, mapping)
        typer.echo("状态更新 %d 题" % n)
    else:
        n = update_levels(db, mapping)
        typer.echo("分级更新 %d 题" % n)


@app.command()
def sandbox(image: str = typer.Option("python:3.11-alpine", help="容器镜像"),
            cmd: str = typer.Option("print('sandbox ok')", help="容器内执行的 Python 代码"),
            no_pull: bool = typer.Option(False, help="跳过 docker pull（镜像已在本地）")):
    """Docker sandbox probe: run a command inside a container to verify the execution environment.

    Remote server: set the DOCKER_HOST env var (e.g. ssh://user@server or tcp://...) and this
    command applies to the remote daemon.
    """
    import subprocess
    chk = subprocess.run(["docker", "version"], capture_output=True, text=True)
    if chk.returncode != 0:
        typer.echo("docker 不可用：请启动 Docker Desktop，或设置 DOCKER_HOST 指向远程服务器后重试")
        raise typer.Exit(1)
    if not no_pull:
        subprocess.run(["docker", "pull", image], capture_output=True, text=True)
    args = ["docker", "run", "--rm", image]
    args += ["python", "-c", cmd] if image.startswith("python") else ["sh", "-c", cmd]
    r = subprocess.run(args, capture_output=True, text=True, timeout=300)
    typer.echo("exit: %d" % r.returncode)
    typer.echo((r.stdout or r.stderr)[-800:])


@app.command()
def xlsx_export(cases: str = typer.Option(None, help="题库来源（.db/.json/.jsonl/目录）"),
                out: str = typer.Option("题库模板.xlsx", help="导出的 Excel 路径"),
                blank: bool = typer.Option(False, help="生成空白模板（不依赖已有题库，含一行示例）"),
                auto_case_id: bool = typer.Option(True, help="空 case_id 自动按 套件-序号 生成")):
    """Export a case bank to the Excel template (with level/yes-no dropdowns)."""
    from ..case.loader import load_cases
    from ..case.xlsx_io import export_cases, export_template
    if blank:
        typer.echo(export_template(out))
        return
    cs = load_cases(cases or CFG["default_cases"])
    typer.echo(export_cases(cs, out))


@app.command()
def xlsx_import(path: str = typer.Option(..., help="填写好的 Excel 路径"),
                db: str = typer.Option("cases/cases.db", help="SQLite 题库库路径"),
                suite: str = typer.Option("", help="suite 缺省值（行内未填时使用）"),
                jsonl_out: str = typer.Option("", help="可选：同步导出一份 cases.jsonl")):
    """Import a filled Excel into the case bank: auto-complete what can be completed (case_id/as_of/level);
    problem rows are written back as a red-marked Excel for the user to fix, looping until all pass."""
    import datetime
    from ..case.store import upsert_cases
    from ..case.xlsx_io import import_xlsx, mark_errors
    today = datetime.date.today().isoformat()
    valid, problems, fixes = import_xlsx(path, suite_fallback=suite, import_date=today)
    if problems:
        marked = mark_errors(path, problems, fixes)
        typer.echo("发现 %d 处问题，已生成标红文件：%s（问题单元格已标红，改对后重新导入）"
                   % (len(problems), marked))
        for p2 in problems:
            typer.echo("  [X] 第%d行 %s" % (p2["row"], p2["msg"]))
        if not valid:
            raise typer.Exit(1)
    if valid:
        upsert_cases(db, valid)
        typer.echo("入库 %d 题 → %s" % (len(valid), db))
        if jsonl_out:
            from ..case.store import export_jsonl
            export_jsonl(jsonl_out, valid)
            typer.echo("jsonl 同步：%s" % jsonl_out)


@app.command()
def smoke_init(cases: str = typer.Option(CFG["default_cases"], help="题库目录（支持 .json/.jsonl/.db）"),
               out: Optional[str] = typer.Option(None, help="冒烟脚本输出路径")):
    """Generate the offline smoke script template (fill in expected answers and tool calls per case)."""
    import json
    from pathlib import Path
    from ..case.loader import load_cases
    tpl = {"_说明": "把每道题的 answer_text / final_json 按你的 Agent 预期行为填写；"
                    "tool_calls 每项为 [工具名, ok或denied]。"
                    "填好后运行：agentgate smoke --cases <目录> --script <本文件>"}
    for c in load_cases(cases):
        tpl[c.case_id] = {"answer_text": "", "final_json": {}, "tool_calls": []}
    out_path = Path(out or (Path(CFG["default_cases"]).parent / "smoke.template.json"))
    out_path.write_text(json.dumps(tpl, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo("template written: %s" % out_path)


@app.command()
def smoke(cases: str = typer.Option(CFG["default_cases"], help="题库目录（支持 .json/.jsonl/.db）"),
          script: str = typer.Option(..., help="冒烟脚本（smoke_init 生成的 json）"),
          out: Optional[str] = typer.Option(None, help="结果输出目录")):
    """Offline smoke: scripted answers+spans -> judge -> gate (no network, no LLM)."""
    import datetime
    import json
    from pathlib import Path
    from ..case.loader import load_cases
    from ..control.service import run_case_set
    from ..run.targets.base import MockTarget
    tpl = json.loads(Path(script).read_text(encoding="utf-8"))
    tpl.pop("_说明", None)
    # resolve the case source: suite name (no path separators and present in the bank) or a path
    src = cases
    if not Path(src).exists() and ("/" not in src and chr(92) not in src):
        from ..case.loader import load_cases as _load
        root = Path("cases")
        pool: list = []
        for f in sorted(root.rglob("*.jsonl")) + sorted(root.rglob("*.json")):
            try:
                pool.extend(_load(f))
            except Exception:
                continue
        matched = [c for c in pool if c.suite == src]
        if matched:
            tmp = Path(CFG["results_root"]) / ("smoke-%s-cases.jsonl" % src)
            tmp.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8", newline=chr(10)) as fh:
                for c in matched:
                    fh.write(c.model_dump_json() + chr(10))
            src = str(tmp)
    responses, spans, scripted = {}, {}, []
    for cid, spec in tpl.items():
        if not isinstance(spec, dict) or not spec.get("answer_text"):
            continue                     # skip unfilled cases
        scripted.append(cid)
        responses[cid] = {"answer_text": spec.get("answer_text", ""),
                          "final_json": spec.get("final_json", {}),
                          "trace_id": "smoke-" + cid, "usage_total": 0, "audit": []}
        spans[cid] = [
            {"trace_id": "smoke-" + cid, "span_id": "root", "name": "agent.run",
             "start_unix_nano": 1, "end_unix_nano": 9,
             "attributes": {"agent.id": "smoke", "model": "scripted", "source": "eval"}},
        ] + [
            {"trace_id": "smoke-" + cid, "span_id": "t%d" % i, "name": "tool.execute",
             "start_unix_nano": 2 + i, "end_unix_nano": 3 + i,
             "attributes": {"tool.name": tc[0], "tool.status": tc[1] if len(tc) > 1 else "ok"}}
            for i, tc in enumerate(spec.get("tool_calls", []))
        ]
    if not scripted:
        typer.echo("冒烟脚本里没有已填写的题目（answer_text 为空的题会被跳过）")
        raise typer.Exit(1)
    out_dir = out or ("%s/smoke-%s" % (CFG["results_root"], datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    summary = run_case_set(cases, MockTarget(responses=responses, spans=spans), out_dir,
                           case_ids=scripted)
    typer.echo("gate: %s | score: %s" % (summary["gate"]["decision"],
                                          summary["gate"].get("score", "n/a")))
    for r in summary["results"]:
        mark = "PASS" if (r["scores"]["deterministic_pass"] and r["scores"]["rule_pass"]) else                ("PENDING" if r["scores"].get("human_review") == "pending" else "FAIL")
        typer.echo("  [%s] %s | %s" % (mark, r["task_id"], r["ASI"][:80]))
    typer.echo("report: %s" % summary["paths"]["report"])


@app.command()
def autoadapt(source: str = typer.Option(..., help="外部数据集 jsonl 路径"),
              suite: str = typer.Option(..., help="套件名（如 FB）"),
              out: Optional[str] = typer.Option(None, help="题库输出 cases.jsonl 路径"),
              review: Optional[str] = typer.Option(None, help="人工检查清单输出路径"),
              id_field: str = typer.Option("financebench_id", help="ID 字段名"),
              question_field: str = typer.Option("question", help="问题字段名"),
              answer_field: str = typer.Option("answer", help="金标答案字段名"),
              doc_field: str = typer.Option("doc_name", help="文档名字段名（可选）"),
              profile: str = typer.Option("", help="运行时剖面（如 fb；留空则不带）"),
              level: str = typer.Option("L1", help="套内分级"),
              as_of: str = typer.Option(None, help="时间门（默认填入池日期）"),
              db: str = typer.Option("cases/cases.db", help="同时导入 SQLite 题库库（留空则只写 jsonl）"),
              llm_hint: bool = typer.Option(False, help="用 LLM 起草期望行为（供人工 check）")):
    """Auto-adapt an external dataset: bucket -> case bank jsonl + human-review checklist."""
    from ..control.autoadapt import auto_adapt
    summary = auto_adapt(source=source, id_field=id_field, question_field=question_field,
                         answer_field=answer_field, suite=suite,
                         out=out or ("cases/%s/cases.jsonl" % suite),
                         review=review or ("cases/%s/adapt-review.md" % suite),
                         doc_field=doc_field, profile=profile, level=level,
                         llm_hint=llm_hint, db=db, as_of=as_of)
    typer.echo("cases: %s | buckets: %s | review: %s" % (
        summary["cases"], summary["buckets"], summary["review"]))


@app.command()
def serve(port: int = typer.Option(CFG["rest_port"], help="REST 端口")):
    """Start the legacy minimal REST service (/health, /api/v1/runs)."""
    import uvicorn
    from ..server.app import app as fastapi_app
    uvicorn.run(fastapi_app, host="127.0.0.1", port=port)


@app.command()
def web(host: str = typer.Option("0.0.0.0", help="监听地址"),
        port: int = typer.Option(CFG["rest_port"], help="平台 API 端口"),
        data_dir: str = typer.Option("", help="数据目录（默认 agentgate.json 的 data_dir 或 ./data）"),
        no_services: bool = typer.Option(False, help="只起 REST API（不起常驻接收器/代理/worker，测试用）")):
    """Start the web platform: REST API + resident receiver (:4318) + resident recording
    proxy (:8300) + the global serial worker. First launch bootstraps the owner account
    (random password printed once; or inject via the OWNER_PASSWORD env var)."""
    import uvicorn
    from ..webapp.app import create_app
    application = create_app(data_dir=data_dir or None, start_services=not no_services)
    uvicorn.run(application, host=host, port=port)


@app.command("passwd")
def passwd(username: str = typer.Argument(..., help="用户名")):
    """Set a user's password directly on the deploy machine (no old password needed).

    This is the only password-change path for the owner account (no web UI, by design)."""
    import getpass
    from ..webapp import auth as webauth
    from ..webapp import db as webdb
    user = webdb.get_user_by_name(username)
    if not user:
        typer.echo("user not found: %s" % username)
        raise typer.Exit(1)
    pw = getpass.getpass("new password: ")
    pw2 = getpass.getpass("repeat password: ")
    if pw != pw2:
        typer.echo("passwords do not match")
        raise typer.Exit(1)
    if len(pw) < 6:
        typer.echo("password must be at least 6 characters")
        raise typer.Exit(1)
    webdb.update_user(username, password_hash=webauth.hash_password(pw))
    typer.echo("password updated for %s (role=%s)" % (username, user["role"]))


@app.command("llm-proxy")
def llm_proxy(upstream: str = typer.Option(..., help="上游 LLM 网关 base_url（OpenAI 兼容，含 /v1）"),
              port: int = typer.Option(8300, help="代理监听端口"),
              sink: str = typer.Option("results/llm_calls/current.jsonl",
                                       help="录制落盘 JSONL（也可用 AGENTGATE_PROXY_SINK）")):
    """LLM recording proxy: point the target agent's base_url at this proxy to fully record model calls
    (message history / available tools / generation params / outputs / tool_calls / usage /
    latency / errors — Inspect-ModelEvent-shaped, zero agent code change)."""
    import os
    import uvicorn
    from urllib.parse import urlparse
    if urlparse(upstream.strip()).port == port:
        typer.echo("error: upstream points at the proxy's own port (self-loop); pass the real gateway address")
        raise typer.Exit(1)
    os.environ["AGENTGATE_PROXY_UPSTREAM"] = upstream
    os.environ["AGENTGATE_PROXY_SINK"] = sink
    typer.echo("llm-proxy :%d → %s（录制 → %s）" % (port, upstream, sink))
    typer.echo("被测 Agent base_url 改为 http://127.0.0.1:%d/v1 即可，零代码改动" % port)
    from ..trace.llm_proxy import app as proxy_app
    uvicorn.run(proxy_app, host="0.0.0.0", port=port)


@app.command("probe")
def probe(url: str = typer.Argument(..., help="被测 Agent 基地址，如 http://127.0.0.1:8200"),
          timeout: float = typer.Option(60.0, help="/invoke 合成探测的最长等待（秒）")):
    """Pre-flight contract check for a target agent: /health + /capabilities + one synthetic
    /invoke round-trip, validated against the invoke contract. Run this BEFORE a real
    evaluation — a red check here means the run would break or misjudge."""
    from ..control.probe import probe_agent
    report = probe_agent(url, timeout=timeout)
    icon = {"pass": "✓", "warn": "!", "fail": "✗"}
    typer.echo("Probe %s -> %s" % (report["url"], "OK" if report["ok"] else "PROBLEMS FOUND"))
    for c in report["checks"]:
        typer.echo("  [%s] %-22s %s" % (icon[c["status"]], c["name"], c["detail"]))
    typer.echo("warn = the run completes but a dimension/signal degrades; "
               "fail = the run would break or misjudge.")


@app.command()
def compare(runs: List[str] = typer.Option(..., "--runs",
                                           help="2 个及以上 run 目录，重复传参：--runs a --runs b"),
            out: str = typer.Option("", help="对比报告输出路径（留空打印）")):
    """Cross-run comparison: verdict agreement / flip details / per-suite pass and cost deltas
    (component-scorecard ablation view; SEAGym-style transferability = control suites must not regress)."""
    from ..control.compare import compare_runs, render
    summary = compare_runs(list(runs))
    text = render(summary, lang="zh")
    if out:
        Path(out).write_text(text, encoding="utf-8")
        en = Path(out).with_name(Path(out).stem + "-en.md")
        en.write_text(render(summary, lang="en"), encoding="utf-8")
        typer.echo("对比报告：%s（+%s）" % (out, en.name))
    else:
        typer.echo(text)


@app.command()
def analyze(run: str = typer.Option(..., help="run 输出目录"),
            out: str = typer.Option("", help="分析报告路径（默认 run 目录下 analysis.md）")):
    """Re-run trajectory analysis on an archived run (long-horizon/loops, injection follow, dangerous ops, model errors)."""
    import json as _json
    from ..analysis.trajectory import analyze_case, render_section
    p = Path(run)
    results = _json.loads((p / "eval_results.json").read_text(encoding="utf-8"))
    mcalls_path = p / "llm_calls_raw.json"
    mcalls_by = {m["task_id"]: m["calls"] for m in
                 (_json.loads(mcalls_path.read_text(encoding="utf-8"))
                  if mcalls_path.exists() else [])}
    texts = {"zh": ["# 轨迹分析（补跑）", ""], "en": ["# Trajectory Analysis (offline)", ""]}
    for r in results:
        a = analyze_case({"input": {}}, {"answer_text": ""}, None,
                         mcalls_by.get(r["task_id"], []))
        for lang in ("zh", "en"):
            texts[lang].extend(render_section(r["task_id"], a, lang=lang))
            texts[lang].append("")
    out_zh = Path(out) if out else p / "analysis.md"
    out_zh.write_text(chr(10).join(texts["zh"]), encoding="utf-8")
    out_en = out_zh.with_name(out_zh.stem + "-en.md")
    out_en.write_text(chr(10).join(texts["en"]), encoding="utf-8")
    typer.echo("分析报告：%s（+%s）" % (out_zh, out_en.name))


if __name__ == "__main__":
    app()
