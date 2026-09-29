import json

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

_provider = None


def init_tracing(cfg):
    """初始化 OTel：OTLP/HTTP 导出到 agentgate 接收器（:4318）。"""
    global _provider
    if _provider is not None:
        return trace.get_tracer("fin-runtime")
    _provider = TracerProvider(resource=Resource.create(
        {"service.name": "fin-runtime", "agent.id": cfg.agent_id}))
    _provider.add_span_processor(SimpleSpanProcessor(
        OTLPSpanExporter(endpoint=cfg.otlp_endpoint + "/v1/traces")))
    trace.set_tracer_provider(_provider)
    return trace.get_tracer("fin-runtime")


def _attrs(**kv):
    out = {}
    for k, v in kv.items():
        if v is None:
            continue
        out[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
    return out


def agent_run_span(tracer, cfg, query):
    return tracer.start_as_current_span("agent.run", attributes=_attrs(
        **{"agent.id": cfg.agent_id, "model": cfg.llm_model, "source": "eval",
           "component.version": cfg.component_versions, "query.len": len(query)}))


def tool_span(tracer, name, args, status):
    return tracer.start_as_current_span("tool.execute", attributes=_attrs(
        **{"tool.name": name, "tool.args": args, "tool.status": status}))


def llm_span(tracer, model, tokens):
    return tracer.start_as_current_span("llm.call", attributes=_attrs(
        **{"llm.model": model, "llm.usage.total_tokens": tokens}))
