"""公司级红线 PEP（P0 版）：违禁工具硬拦截 + 审计留痕。PDP 接入点预留。"""

FORBIDDEN = {"export_data"}
AUDIT = []


def check(tool_name):
    """返回 (allowed, reason)；拦截必留审计。"""
    if tool_name in FORBIDDEN:
        reason = "红线：数据导出被公司策略禁止"
        AUDIT.append({"tool": tool_name, "action": "denied", "reason": reason})
        return False, reason
    return True, None
