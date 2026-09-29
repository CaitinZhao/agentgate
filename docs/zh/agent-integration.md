<div align="right">[English](../en/agent-integration.md) | 简体中文（本页）</div>

# Agent 接入：让平台评测你的 Agent

本文档面向被测 Agent 的开发者：先跑通仓库自带的样例 Agent，再按同一个契约接入你自己的
Agent。评测原理与两条观测通道的分工见 [轨迹录制](trace-recording.md)。

## 第一步：先跑样例 Agent（jiuwen）

样例 Agent 是本仓库自带的真实 ReAct Agent（openJiuwen），带银行财报/财报 PDF/记忆/τ
工具环境，用于联调与演示：

```bash
# compose 方式（推荐，见部署文档方式 B）
docker compose --profile agent up -d --build

# 起来后自检：/health 与能力剖面
curl -s http://127.0.0.1:8200/health
curl -s http://127.0.0.1:8200/capabilities
# {"agent":"jiuwen-sample-agent","profiles":["base","bank","locomo","longmem","fb","bfcl",
#  "spider","gaia","airbench","harmbench","agentdojo","agentdojo-banking",
#  "agentdojo-workspace","tau-airline","tau-retail"],...}
```

它的模型凭据在 `tests/fixtures/.env`（部署文档第 2 步）；发起评测时目标填
`http://jiuwen-agent:8200`（compose 网络）或 `http://127.0.0.1:8200`（同机）。

**参考实现（接入时对着抄）**：

| 文件 | 内容 |
|---|---|
| `tests/fixtures/jiuwen_server.py` | `/invoke`、`/capabilities`、`/health` 三个接口；把工具注册进 openJiuwen 并包装成 span |
| `tests/fixtures/jiuwen_agent.py` | ReAct Agent 主循环（openJiuwen SDK 用法） |
| `tests/fixtures/fin_runtime/` | 领域工具集（财报检索/红线拦截/轨迹上报的包装方式） |

## 第二步：你的 Agent 要实现什么

平台对被测 Agent 只有两个硬性接口 + 一个软性约定：

### 1. `POST /invoke`（必须）

```json
// 请求（平台发来）
{"query": "银行A 2026H1 营收是多少（亿元）？",      // 题面
 "profile": "bank",                                // 能力剖面（领域包要求时下发）
 "context": "……（长文本材料，记忆类剖面才有）",
 "llm_base_url": "http://平台:8300/v1"}             // 消息级录制开启时下发

// 响应（Agent 返回）
{"answer_text": "合并口径营收 96.2 亿元",            // 最终自然语言答复
 "final_json": {"value": 96.2, "unit": "亿元", "evidence": "...", "answer": "..."},
 "trace_id": "agent-internal-trace-id",            // 用于关联轨迹
 "usage_total": 12345,                             // 本题 token 总消耗（成本维数据源）
 "audit": []}
```

`final_json` 是判定系统的比对对象：数值题至少给 `value`（建议带 `unit` 与 `evidence`），
是非题给 `value: "yes"/"no"`，拒答题答复文本里要出现拒答关键词。没有 `final_json` 时
判定退化到 `answer_text` 文本比对（抽取/拒答题可行，数值题会 FAIL）。

### 2. `GET /capabilities`（推荐）

返回 Agent 支持的剖面列表。平台发起评测前与题库的领域包要求做**能力握手**：需要的剖面
Agent 没声明 → 对应题目 SKIPPED（带原因），不会误判失败。

```json
{"agent": "my-agent", "profiles": ["base", "bank"], "llm_base_url_supported": true}
```

### 3. OTLP 轨迹上报（推荐，安全维与可靠维的数据源）

Agent 把执行过程以 span 上报到平台的接收器（环境变量
`OTEL_EXPORTER_OTLP_ENDPOINT=http://平台:4318`）。span 约定二选一或混用：

- 私有约定：`agent.run`（根，带 `agent.id`/`model`）、`llm.call`（带
  `llm.usage.total_tokens`）、`tool.execute`（带 `tool.name`/`tool.status`：ok/denied/error）。
- openJiuwen 原生：`llm.<Class>` + `gen_ai.*` 属性、`tool.<name>` + `gen_ai.tool.name`。

没有 OTLP 上报时评测仍可运行（答案级判定），但违禁工具"尝试"检测、断链检测、组件版本
会缺失——见 [轨迹录制](trace-recording.md) 的分工表。

## 第三步：消息级录制（推荐，默认开启）

发起评测勾选"消息级录制"时，`/invoke` 请求会带 `llm_base_url`：Agent 把自己的 LLM
网关地址**临时替换**为该值即可（录完后下次调用平台会继续下发）。不替换则录制降级、
成本维 n/a。样例实现：`fin_runtime/llm.py` 读取 llm_base_url 并把请求发往该地址。

## 接入自检清单

1. `curl /health` 通；`/capabilities` 列出支持的剖面。
2. 平台发起 1 题的 run（用 `case_ids` 精确指定），在执行详情页看到 trace_id 与工具序列。
3. 勾选消息级录制重跑：报告出现轨迹分析节与成本维。
4. 全绿后再上全量题库（先抽样估成本）。

环境部署问题见 [环境准备与一键部署](deployment.md)；判定规则见 [评分规则](scoring.md)。

## 常见问题

- **全部题目 FAIL，run 状态是 failed，错误是 target unreachable**：被测地址不可达（agent 没起/端口写错）。平台不 gave 分数——这类 run 的六维全是 n/a，修正地址后重跑。
- **我的 Agent 没有 /capabilities**：平台按未知 agent 处理（宽松：不跳题），但声明剖面的库会因无法握手而全部 SKIPPED——建议实现该端点。
