<div align="right">[English](../en/trace-recording.md) | 简体中文（本页）</div>

# 轨迹录制：Agent OTLP 上报 + 消息级录制代理

本文档解释评测时平台从两条通道收集轨迹的原因、各自的分工，以及被测 Agent 两侧需要做什么配置。
接入层面的完整步骤（含样例代码）见 [Agent 接入](agent-integration.md)。

## 两条通道，各看一半事实

| 通道 | 端口 | 看到什么 | 看不到什么 |
|---|---|---|---|
| ① OTLP 上报（Agent 主动） | 4318 | span 级执行图：`agent.run / llm.call / tool.execute`，工具的 ok/denied/error 状态、父子关系、agent 身份与组件版本 | 消息内容（prompt/回复原文）、注入载荷、精确到每次调用的 token 明细 |
| ② 消息级录制代理（平台拦截） | 8300 | 每次 LLM 调用的完整消息流：请求/回复/工具调用参数/usage | 被权限层拦截的调用（请求根本没出去）、span 树完整性、Agent 身份 |

两条通道互补：①回答"**Agent 系统做了什么**"，②回答"**模型说了什么**"。评测用它们分别支撑：

- **① 独有**：违禁工具"尝试"检测（含被红线拦截的，安全维一票归零依赖它）、轨迹断链检测、
  报告里的 Agent 身份与组件版本。
- **② 独有**：注入跟随与金丝雀泄露检测、跳步直答（有消息流但零工具调用）、精确 token 成本、
  检查点的参数级命中。
- **重叠**：工具调用序列（开录制后优先用 ②，粒度到参数）。

## 发起评测时的通道开关

- ① 始终开启：平台常驻 OTLP 接收器（默认 :4318），Agent 侧配一个环境变量即可上报。
- ② 默认开启：发起页"消息级录制"勾选默认打开。开启时平台把录制代理地址
  （如 `http://agentgate:8300/v1`）随 `/invoke` 的 `llm_base_url` 字段下发给 Agent，
  Agent 把它当作自己的 LLM 网关使用；Agent 不支持该字段或未配置时自然降级为直连，
  此时报告的轨迹分析退化为 span 级、成本维显示 n/a。

## Agent 侧需要的配置（汇总）

```bash
# ① 轨迹上报（OTLP）
OTEL_EXPORTER_OTLP_ENDPOINT=http://<平台地址>:4318

# ② 无需配置密钥：录制时平台随 /invoke 下发 llm_base_url，
#    Agent 把 LLM 请求发到该地址即可（样例实现见 Agent 接入文档）
```

完整契约、样例代码与联调步骤见 [Agent 接入](agent-integration.md)；
录制的落盘位置与查看方式见 [诊断报告](diagnostic-report.md)。
