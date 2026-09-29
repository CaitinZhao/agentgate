"""FinanceBench 剖面（profile=fb）的工具注册与系统提示词。"""
from . import fb_env


def search_filing(doc_name, keyword, max_results=4):
    return fb_env.search_filing(doc_name, keyword, max_results)


def list_filings(company=None):
    return {"filings": fb_env.list_filings(company)}


def calculate(expression):
    from .tools import calculate as _c
    return _c(expression)


FB_TOOLS = {"search_filing": search_filing, "list_filings": list_filings,
            "calculate": calculate}

FB_TOOL_SPECS = [
    {"type": "function", "function": {"name": "list_filings",
     "description": "列出文档库中的财报列表（可按公司名过滤），返回 doc_name",
     "parameters": {"type": "object", "properties": {"company": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {"name": "search_filing",
     "description": "在某份财报内按关键词检索（多个关键词用逗号分隔），返回命中页码与原文摘录；引用时注明 doc_name 与页码",
     "parameters": {"type": "object", "properties": {"doc_name": {"type": "string"}, "keyword": {"type": "string"}, "max_results": {"type": "integer", "default": 4}}, "required": ["doc_name", "keyword"]}}},
    {"type": "function", "function": {"name": "calculate",
     "description": "安全计算四则运算表达式（单位换算时务必先用它）",
     "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}},
]

FB_SYSTEM_PROMPT = (
    "你是资深财务分析师，基于真实美股财报（10-K/10-Q）回答问题。\n"
    "【工作方式】1) 用 list_filings 找到相关财报 doc_name；"
    "2) 用 search_filing 检索关键科目（可多次、多关键词）；"
    "3) 需要单位换算或计算时用 calculate；"
    "4) 结论必须来自检索到的原文，引用时注明 doc_name 与页码。\n"
    "【输出格式】最终答复必须以一行 FINAL: {..json..} 结尾："
    "value 填数值（是非题填 \"yes\"/\"no\"，无法确定填 null），"
    "unit 填单位（USD millions / USD billions / % / none），"
    "evidence 填 \"doc_name#p页码\"，answer 一句话结论。\n"
    "【红线】禁止编造：检索不到就明说检索不到，禁止凭记忆填数。\n"
)
