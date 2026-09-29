"""Trajectory summary (goes into evidence: tool call sequence and block records)."""


def evidence(case, response, trace):
    return [
        {"claim": "tool_calls", "source_id": "trace:%s" % trace.trace_id,
         "location": ",".join(trace.tool_calls) or "none"},
        {"claim": "denied_tools", "source_id": "trace:%s" % trace.trace_id,
         "location": ",".join(trace.denied_tools) or "none"},
    ]
