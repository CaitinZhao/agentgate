"""LLM recording proxy: OpenAI-compatible /v1/chat/completions forwarding + full request/response recording.

Borrows Inspect AI's Model API boundary recording (ModelCall in model/_model_call.py:
request/response/error/time fully kept), but the target agent does NOT need to be rebuilt on
the eval framework — point the agent's base_url at this proxy and every model-call boundary
yields:

  the model, full message history (system / prior turns / tool results), the available tool
  set, generation params (temperature/max_tokens/stop/stream...), the model output
  (content + tool_calls + finish_reason), token usage, latency, upstream errors, retries
  (one record per HTTP attempt)

Sink: JSONL (one ModelCall record per line), default results/llm_calls/current.jsonl,
overridable via the AGENTGATE_PROXY_SINK env var. The evaluation orchestration collects
records into the owning case by call time window.

Run: agentgate llm-proxy --upstream https://... --port 8300
Zero agent-side change: switch base_url to http://<proxy>:8300/v1 (Authorization passes through).
"""
import datetime
import json
import os
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="agentgate-llm-proxy", version="0.1.0")

_raw_upstream = os.environ.get("AGENTGATE_PROXY_UPSTREAM", "")
# Runtime-mutable state: the web platform hosts this proxy permanently and admins can switch
# the upstream (real gateway) / sink without a restart. Initialized from the env (CLI mode);
# strip \r because Windows env files commonly end lines with it.
STATE = {
    "upstream": _raw_upstream.strip().strip("\r\n").rstrip("/").rstrip("\r"),
    "sink": os.environ.get("AGENTGATE_PROXY_SINK", "results/llm_calls/current.jsonl"),
}


def set_upstream(url: str):
    STATE["upstream"] = (url or "").strip().strip("\r\n").rstrip("/")


def set_sink(path: str):
    STATE["sink"] = str(path)


def _sink() -> Path:
    p = Path(STATE["sink"])
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _record(row: dict):
    with _sink().open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _base_row(request_body: dict, attempt: int) -> dict:
    """Inspect-ModelCall-shaped record: full request + response/error/time."""
    return {
        "ts": datetime.datetime.now().isoformat(timespec="milliseconds"),
        "attempt": attempt,
        "request": {
            "model": request_body.get("model"),
            "messages": request_body.get("messages", []),
            "tools": request_body.get("tools", []),
            "tool_choice": request_body.get("tool_choice"),
            "temperature": request_body.get("temperature"),
            "top_p": request_body.get("top_p"),
            "max_tokens": request_body.get("max_tokens"),
            "stop": request_body.get("stop"),
            "stream": request_body.get("stream", False),
        },
        "response": None,
        "error": None,
        "time_ms": None,
    }


@app.get("/health")
def health():
    return {"status": "ok", "upstream": STATE["upstream"] or "(unset)", "sink": str(_sink())}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    upstream = STATE["upstream"]
    if not upstream:
        return JSONResponse({"error": "proxy upstream is not configured "
                                      "(admin settings / AGENTGATE_PROXY_UPSTREAM)"},
                            status_code=500)
    body = await request.json()
    headers = {"Authorization": request.headers.get("Authorization", ""),
               "Content-Type": "application/json"}
    attempt = 0
    while True:
        row = _base_row(body, attempt)
        t0 = time.time()
        try:
            if body.get("stream"):
                return await _forward_stream(body, headers, row, t0)
            async with httpx.AsyncClient(timeout=600) as client:
                resp = await client.post(upstream + "/chat/completions",
                                         json=body, headers=headers)
            row["time_ms"] = int((time.time() - t0) * 1000)
            row["response"] = resp.json() if resp.status_code == 200 else None
            if resp.status_code != 200:
                row["error"] = {"status": resp.status_code, "body": resp.text[:2000]}
                _record(row)
                return JSONResponse(json.loads(resp.text) if resp.text else {},
                                    status_code=resp.status_code)
            _record(row)
            return JSONResponse(resp.json())
        except httpx.HTTPError as e:
            # network-level error: record it and return 502 (the client decides on retries;
            # every client-initiated retry lands as a new attempt record)
            row["time_ms"] = int((time.time() - t0) * 1000)
            row["error"] = {"status": 502, "body": repr(e)[:2000]}
            _record(row)
            return JSONResponse({"error": {"message": repr(e)[:500]}}, status_code=502)


async def _forward_stream(body, headers, row, t0):
    """SSE stream forwarding: yield chunks while accumulating deltas; write one full record at the end."""
    content_parts, tool_calls, finish, usage, upstream_err = [], {}, None, None, None

    async def gen():
        nonlocal finish, usage, upstream_err
        async with httpx.AsyncClient(timeout=600) as client:
            async with client.stream("POST", STATE["upstream"] + "/chat/completions",
                                     json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    raw = (await resp.aread()).decode("utf-8", "replace")
                    row["time_ms"] = int((time.time() - t0) * 1000)
                    row["error"] = {"status": resp.status_code, "body": raw[:2000]}
                    _record(row)
                    yield raw
                    return
                async for line in resp.aiter_lines():
                    yield line + "\n"
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(payload)
                    except ValueError:
                        continue
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for ch in chunk.get("choices", []):
                        delta = ch.get("delta") or {}
                        if delta.get("content"):
                            content_parts.append(delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            slot = tool_calls.setdefault(
                                tc.get("index", 0),
                                {"id": tc.get("id"), "type": "function",
                                 "function": {"name": "", "arguments": ""}})
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["function"]["name"] += fn["name"]
                            if fn.get("arguments"):
                                slot["function"]["arguments"] += fn["arguments"]
                        if ch.get("finish_reason"):
                            finish = ch["finish_reason"]

    def _assemble():
        row["time_ms"] = int((time.time() - t0) * 1000)
        row["response"] = {
            "choices": [{"message": {"role": "assistant",
                                     "content": "".join(content_parts),
                                     "tool_calls": [tool_calls[i] for i in sorted(tool_calls)]
                                     if tool_calls else None},
                         "finish_reason": finish}],
            "usage": usage,
            "_note": "assembled from stream deltas",
        }
        _record(row)

    async def gen_with_record():
        try:
            async for chunk in gen():
                yield chunk
        finally:
            _assemble()

    return StreamingResponse(gen_with_record(), media_type="text/event-stream")


@app.get("/v1/models")
async def models(request: Request):
    upstream = STATE["upstream"]
    if not upstream:
        return JSONResponse({"error": "proxy upstream is not configured"}, status_code=500)
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(upstream + "/models", headers={
            "Authorization": request.headers.get("Authorization", "")})
    return JSONResponse(resp.json() if resp.status_code == 200 else {},
                        status_code=resp.status_code)
