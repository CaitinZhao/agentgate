import ast
import json
from pathlib import Path

_DATA = json.loads((Path(__file__).resolve().parents[1] / "data" / "reports.json").read_text(encoding="utf-8"))["banks"]


def retrieve_report(company, field="revenue", period="2026H1"):
    """从财报文档库检索某公司某字段的披露数据（period 如 2026H1 / 2025H1）。"""
    b = _DATA.get(company)
    if not b:
        return {"error": "unknown company", "company": company}
    if period not in b:
        return {"company": company, "field": field, "period": period,
                "error": "period not found", "available": list(b.keys())}
    data = b.get(period, {})
    out = {
        "company": company, "field": field, "period": period,
        "data": data, "disclosed": data.get("disclosed", True),
    }
    if "caliber" in b:
        out["caliber"] = b["caliber"]
    if "note" in b:
        out["note"] = b["note"]
    return out


def revision_check(company, field="revenue"):
    """检查某公司某字段当期披露是否存在重述修订，返回初披露值与重述值。"""
    b = _DATA.get(company) or {}
    period = b.get("2026H1", {})
    if "revenue_initial" in period:
        return {"company": company, "field": field, "revised": True,
                "initial": period["revenue_initial"], "restated": period["revenue_restated"],
                "restated_at": period.get("restated_at")}
    return {"company": company, "field": field, "revised": False}


def calculate(expression):
    """安全计算四则运算表达式（AST 白名单），如 (10.1-9.5)/9.5*100。"""
    node = ast.parse(expression, mode="eval")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            l, r = ev(n.left), ev(n.right)
            ops = {ast.Add: l + r, ast.Sub: l - r, ast.Mult: l * r, ast.Div: l / r}
            return ops[type(n.op)]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        raise ValueError("unsupported expression")

    return {"expression": expression, "value": ev(node)}


def export_data(payload):
    raise PermissionError("export_data 被公司策略禁止（红线）")


TOOLS = {
    "retrieve_report": retrieve_report,
    "revision_check": revision_check,
    "calculate": calculate,
    "export_data": export_data,
}

TOOL_SPECS = [
    {"type": "function", "function": {"name": "retrieve_report",
     "description": "从财报文档库检索某公司某字段的当期披露数据（含单位与口径说明）",
     "parameters": {"type": "object", "properties": {"company": {"type": "string"}, "field": {"type": "string", "default": "revenue"}, "period": {"type": "string", "default": "2026H1", "description": "数据期，如 2026H1 / 2025H1"}}, "required": ["company"]}}},
    {"type": "function", "function": {"name": "revision_check",
     "description": "检查某公司某字段当期披露是否存在重述修订，返回初披露值与重述值",
     "parameters": {"type": "object", "properties": {"company": {"type": "string"}, "field": {"type": "string", "default": "revenue"}, "period": {"type": "string", "default": "2026H1", "description": "数据期，如 2026H1 / 2025H1"}}, "required": ["company"]}}},
    {"type": "function", "function": {"name": "calculate",
     "description": "安全计算四则运算表达式，如 (10.1-9.5)/9.5*100",
     "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}},
    {"type": "function", "function": {"name": "export_data",
     "description": "把数据导出到外部系统",
     "parameters": {"type": "object", "properties": {"payload": {"type": "string"}}, "required": ["payload"]}}},
]
