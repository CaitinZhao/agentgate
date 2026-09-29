import json

import httpx


class LLMError(RuntimeError):
    pass


class LLMClient:
    """OpenAI 兼容 /chat/completions 客户端（含 function calling）。"""

    def __init__(self, base_url, api_key, model, timeout=120):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def chat(self, messages, tools=None, temperature=0.2):
        body = {"model": self.model, "messages": messages, "temperature": temperature}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        r = httpx.post(
            self.base_url + "/chat/completions",
            headers={"Authorization": "Bearer " + self.api_key},
            json=body, timeout=self.timeout,
        )
        if r.status_code != 200:
            raise LLMError("LLM HTTP %s: %s" % (r.status_code, r.text[:300]))
        data = r.json()
        msg = data["choices"][0]["message"]
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {"_raw": fn.get("arguments")}
            tool_calls.append({"id": tc.get("id", ""), "name": fn.get("name", ""), "arguments": args})
        usage = data.get("usage") or {}
        return {
            "content": msg.get("content") or "",
            "tool_calls": tool_calls,
            "usage": {"total_tokens": usage.get("total_tokens", 0)},
        }
