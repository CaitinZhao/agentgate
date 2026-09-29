"""Local sample-agent server (py3.9-compatible) — the same /invoke contract as
jiuwen_server (openJiuwen, deployed image), used for LOCAL full-run measurements when
the deploy credentials are not available in the session.

Purpose: local end-to-end runs (GLM5.3-Flash via tests/fixtures/.env) with OTel spans in
the same conventions the agentgate normalizer expects:
  agent.run (root: agent.id/model/source) -> llm.call (llm.model, llm.usage.total_tokens)
                                           -> tool.execute (tool.name/tool.args/tool.status)

Profiles supported locally: base, bank, fb, locomo, longmem, tau-airline, tau-retail,
bfcl, spider, gaia, airbench, harmbench. agentdojo is NOT supported here (its official
package needs py>=3.11 and runs inside the deployed agent image).

Run: python tests/fixtures/local_agent_server.py  (uvicorn :8200)
LLM config: tests/fixtures/.env (LLM_BASE_URL/LLM_API_KEY/LLM_MODEL/LLM_PROVIDER) or env.
"""
import contextvars
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from profile_prompts import PROMPTS, SYSTEM_PROMPT  # noqa: E402

# ---- LLM config (fixtures .env first, then real env vars) ----
def _load_env() -> dict:
    env = {}
    f = HERE / ".env"
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


_ENV = _load_env()


def _cfg() -> dict:
    return {
        "base": os.environ.get("LLM_BASE_URL") or _ENV.get("LLM_BASE_URL", ""),
        "key": os.environ.get("LLM_API_KEY") or _ENV.get("LLM_API_KEY", ""),
        "model": os.environ.get("LLM_MODEL") or _ENV.get("LLM_MODEL", "GLM5.3-Flash"),
    }


# ---- OTel (same span conventions as jiuwen_server) ----
from opentelemetry import trace, context as otel_context  # noqa: E402
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # noqa: E402
from opentelemetry.sdk.resources import Resource  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402

OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
AGENT_ID = "local-sample-agent"
_provider = TracerProvider(resource=Resource.create(
    {"service.name": AGENT_ID, "agent.id": AGENT_ID}))
_provider.add_span_processor(SimpleSpanProcessor(
    OTLPSpanExporter(endpoint=OTLP_ENDPOINT.rstrip("/") + "/v1/traces")))
trace.set_tracer_provider(_provider)
tracer = _provider.get_tracer("local-sample")

MAX_ITER = {"base": 2, "bfcl": 2, "harmbench": 3, "gaia": 6, "locomo": 2, "longmem": 2,
            "bank": 8, "fb": 12, "spider": 8, "airbench": 8,
            "tau-airline": 12, "tau-retail": 12}

# ---- tool implementations (plain py3.9 callables + OpenAI tool specs) ----
import sqlite3 as _sqlite3  # noqa: E402

_SPIDER_DBS = Path(os.environ.get(
    "AGENTGATE_SPIDER_DBS", HERE.parent.parent / "cases" / "spider" / "dbs"))
_AIR_CORPUS = Path(os.environ.get(
    "AGENTGATE_AIRBENCH_CORPUS", HERE.parent.parent / "cases" / "airbench" / "corpus.jsonl"))
# per-request isolation: concurrent runs (bank-level parallel workers) each get their own
# binding for the per-case environment state (contextvars propagate into the tool loop)
_CV = {"spider_db": contextvars.ContextVar("spider_db", default=None),
       "tau_data": contextvars.ContextVar("tau_data", default=None)}


def _sql_query(sql):
    db = _CV["spider_db"].get()
    path = _SPIDER_DBS / str(db) / ("%s.sqlite" % db)
    try:
        if not str(sql).lstrip().lower().startswith("select"):
            return {"error": "只允许 SELECT 查询"}
        con = _sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True, timeout=10)
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(30)
            return {"columns": cols, "rows": rows,
                    "note": "最多显示 30 行" if len(rows) == 30 else ""}
        finally:
            con.close()
    except Exception as e:
        return {"error": str(e)}


_AIR = {"search": None, "docids": None, "texts": None}
_AIR_LOCK = __import__("threading").Lock()


def _air_tokenize(text):
    return re.findall(r"[a-z0-9]+", str(text).lower())


def _air_search(query, top_k=10):
    if _AIR["search"] is None:
        with _AIR_LOCK:
            return _air_search_locked(query, top_k)
    return _air_search_locked(query, top_k)


def _air_search_locked(query, top_k=10):
    if _AIR["search"] is None:
        from collections import Counter
        docs = []
        with _AIR_CORPUS.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    docs.append((str(d.get("id", "")), str(d.get("text", ""))))
                except ValueError:
                    continue
        n = len(docs)
        avgdl = sum(len(_air_tokenize(t)) for _, t in docs) / max(n, 1)
        df, tf = Counter(), []
        for _, t in docs:
            c = Counter(_air_tokenize(t))
            tf.append(c)
            df.update(c.keys())
        import math
        idf = {w: math.log(1.0 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

        def _search(q, k=10):
            qt = _air_tokenize(q)
            out = []
            for i, counts in enumerate(tf):
                dl = sum(counts.values())
                s = 0.0
                for w in qt:
                    if w in counts:
                        s += idf.get(w, 0.0) * counts[w] * 2.5 / (
                            counts[w] + 1.5 * (1 - 0.75 + 0.75 * dl / max(avgdl, 1e-9)))
                out.append((s, i))
            out.sort(reverse=True)
            return out[:k]
        _AIR.update({"search": _search, "docids": [d for d, _ in docs],
                     "texts": [t for _, t in docs]})
    hits = _AIR["search"](query, int(top_k or 10))
    return {"results": [{"id": _AIR["docids"][i], "snippet": _AIR["texts"][i][:300]}
                        for s, i in hits if s > 0]}


def _tau_base_data(profile):
    import copy
    import importlib
    mod = {"tau-airline": "tau_envs.airline.data", "tau-retail": "tau_envs.retail.data"}
    return copy.deepcopy(importlib.import_module(mod[profile]).load_data())


def _tau_tools(profile):
    """tau_envs tool classes -> OpenAI specs + bound impls over the fresh per-case data."""
    import importlib
    mod = {"tau-airline": "tau_envs.airline.tools", "tau-retail": "tau_envs.retail.tools"}
    all_tools = importlib.import_module(mod[profile]).ALL_TOOLS
    specs, impls = [], {}

    def _make(name, cls):
        def _impl(**kwargs):
            try:
                return cls.invoke(_CV["tau_data"].get(), **kwargs)
            except Exception as e:
                return {"error": str(e)}
        return _impl

    for cls in all_tools:
        info = cls.get_info()["function"]
        specs.append({"type": "function", "function": info})
        impls[info["name"]] = _make(info["name"], cls)
    return specs, impls


def _simple_tool(name, description, params, fn):
    return ({"type": "function", "function": {"name": name, "description": description,
                                              "parameters": params}},
            fn)


def _toolset(profile: str):
    """(system_prompt, [openai tool specs], {name: fn}) for one profile."""
    import importlib
    if profile == "bank":
        from fin_runtime import tools as T
        specs = T.TOOL_SPECS
        fns = {"retrieve_report": T.retrieve_report, "revision_check": T.revision_check,
               "calculate": T.calculate}

        def _export(payload):
            return {"denied": True, "reason": "export_data 被公司策略禁止（红线）"}
        fns["export_data"] = _export
        return PROMPTS["bank"], specs, fns
    if profile == "fb":
        from fin_runtime import tools_fb as FB
        return FB.FB_SYSTEM_PROMPT + (
            "【格式要求】FINAL 后面的 JSON 必须压缩成单行（不要换行/缩进），例如："
            "FINAL: {\"value\": 2278, \"unit\": \"USD millions\", \"evidence\": \"doc#p60\", \"answer\": \"...\"}\n"
        ), FB.FB_TOOL_SPECS, {"list_filings": FB.list_filings, "search_filing": FB.search_filing,
                              "calculate": FB.calculate}
    if profile == "gaia":
        from fin_runtime.tools import calculate as _calc
        spec, _ = _simple_tool("calculate", "安全计算四则运算表达式",
                               {"type": "object", "properties": {"expression": {"type": "string"}},
                                "required": ["expression"]}, None)
        return PROMPTS["gaia"], [spec], {"calculate": _calc}
    if profile == "spider":
        spec, _ = _simple_tool(
            "sql_query", "对当前题目的 SQLite 数据库执行只读 SELECT 查询，返回列名与行（最多 30 行）",
            {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
            None)
        return PROMPTS["spider"], [spec], {"sql_query": _sql_query}
    if profile == "airbench":
        spec, _ = _simple_tool(
            "doc_search", "在评测语料中按关键词检索，返回最相关的文档（id + 摘要）",
            {"type": "object", "properties": {"query": {"type": "string"},
                                              "top_k": {"type": "integer"}},
             "required": ["query"]}, None)
        return PROMPTS["airbench"], [spec], {"doc_search": _air_search}
    if profile.startswith("tau-"):
        wiki = (HERE / "tau_envs" / profile.replace("tau-", "") / "wiki.md").read_text(
            encoding="utf-8")
        prompt = ("你是客服 Agent。请按【政策】使用工具帮助用户办理业务；用户约束都在任务描述里。\n"
                  "【输出格式】完成（或无法完成）时以一行 FINAL: {..json..} 结尾，"
                  "json 包含 answer（一句话结果）。\n【政策】\n" + wiki)
        specs, fns = _tau_tools(profile)
        return prompt, specs, fns
    # base / bfcl / harmbench / locomo / longmem: no tools
    return PROMPTS.get(profile, SYSTEM_PROMPT), [], {}


# ---- chat loop ----

def _parse_final(answer: str) -> dict:
    idx = answer.rfind("FINAL:")
    if idx == -1:
        return {}
    start = answer.find("{", idx)
    if start == -1:
        return {}
    depth, in_str, esc = 0, False, False
    for i in range(start, len(answer)):
        ch = answer[i]
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(answer[start:i + 1])
                except ValueError:
                    return {}
    return {}


def _run_agent(profile: str, query: str) -> tuple:
    """The ReAct loop against the OpenAI-compatible gateway. Returns (answer, tokens)."""
    system, specs, fns = _toolset(profile)
    cfg = _cfg()
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": query}]
    tools_kw = {"tools": specs} if specs else {}
    total_tokens, max_iter = 0, MAX_ITER.get(profile, 6)
    answer = ""
    for _ in range(max_iter):
        with tracer.start_as_current_span("llm.call") as span:
            span.set_attribute("llm.model", cfg["model"])
            r = httpx.post(cfg["base"].rstrip("/") + "/chat/completions",
                           headers={"Authorization": "Bearer " + cfg["key"]},
                           json={"model": cfg["model"], "temperature": 0.0,
                                 "messages": messages, **tools_kw},
                           timeout=300)
            r.raise_for_status()
            d = r.json()
            usage = (d.get("usage") or {}).get("total_tokens") or 0
            total_tokens += usage
            span.set_attribute("llm.usage.total_tokens", usage)
            msg = (d.get("choices") or [{}])[0].get("message", {})
        calls = msg.get("tool_calls") or []
        if calls and specs:
            messages.append({"role": "assistant",
                             "content": msg.get("content") or "",
                             "tool_calls": calls})
            for tc in calls:
                fn = (tc.get("function") or {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                with tracer.start_as_current_span("tool.execute", attributes={
                        "tool.name": name,
                        "tool.args": json.dumps(args, ensure_ascii=False),
                        "tool.status": "ok"}):
                    impl = fns.get(name)
                    if impl is None:
                        out = {"error": "unknown tool %s" % name}
                    else:
                        try:
                            out = impl(**args)
                        except Exception as e:
                            out = {"error": str(e)}
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                 "content": json.dumps(out, ensure_ascii=False, default=str)[:6000]})
            continue
        answer = msg.get("content") or ""
        break
    if not answer:
        answer = "(no final answer within %d iterations)" % max_iter
    return answer, total_tokens


app = FastAPI(title="local-sample-agent", version="0.1.0")


class InvokeBody(BaseModel):
    query: str
    profile: str = "base"
    context: str = ""
    llm_base_url: str = ""
    db: str = ""
    schema: str = ""
    needs_web: bool = False

    class Config:
        extra = "ignore"


@app.get("/health")
def health():
    c = _cfg()
    return {"status": "ok", "model": c["model"], "agent_id": AGENT_ID}


@app.get("/capabilities")
def capabilities():
    return {"agent": AGENT_ID,
            "profiles": ["base", "bank", "locomo", "longmem", "fb", "bfcl", "spider",
                         "gaia", "airbench", "harmbench", "tau-airline", "tau-retail"],
            "llm_base_url_supported": False}


@app.post("/invoke")
def invoke(body: InvokeBody):
    profile = body.profile
    if profile == "spider" and body.db:
        _CV["spider_db"].set(body.db)
    if profile.startswith("tau-"):
        _CV["tau_data"].set(_tau_base_data(profile))
    context = body.context
    if profile == "spider" and body.db:
        context = ("当前数据库：%s\n表结构（表(列, ...)）：\n%s" % (body.db, body.schema or "")
                   + (("\n\n" + context) if context else ""))
    query = (context.rstrip() + "\n\n---\n\n问题：" + body.query) if context else body.query
    with tracer.start_as_current_span("agent.run", attributes={
            "agent.id": AGENT_ID, "model": _cfg()["model"], "source": "eval",
            "profile": profile, "query.len": len(query)}) as root:
        t0 = time.time()
        try:
            answer, usage = _run_agent(profile, query)
        except Exception as e:
            answer, usage = "agent error: %s" % e, 0
        trace_id = format(root.get_span_context().trace_id, "032x")
    return {"answer_text": answer, "final_json": _parse_final(answer),
            "trace_id": trace_id, "usage_total": usage, "audit": [],
            "wall_s": round(time.time() - t0, 2)}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("LOCAL_AGENT_PORT", "8200")),
                log_level="warning")
