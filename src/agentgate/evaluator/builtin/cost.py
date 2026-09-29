"""Cost recording (no threshold judgment at L0; reported only: tokens / wall_time)."""


def record(case, response, trace, wall_time_s):
    return {"tokens": trace.usage_tokens, "wall_time_s": round(wall_time_s, 3),
            "external_calls": len(trace.tool_calls)}
