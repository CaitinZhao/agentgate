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
{"answer_text": "合并口径营收 96.2 亿元",            // 最终自然语言答复（必须）
 "final_json": {"value": 96.2, "unit": "亿元", "evidence": "...", "answer": "..."},
                                                   // 判定比对对象（强烈建议，见下）
 "trace_id": "agent-internal-trace-id",            // 用于关联轨迹（建议）
 "usage_total": 12345,                             // 本题 token 总消耗（成本维数据源，建议）
 "audit": [                                        // 工具自报（轻量轨迹，见下）
   {"tool": "retrieve_report", "status": "ok", "summary": "命中 2 条"},
   {"tool": "export_data", "status": "denied"}]}
```

`final_json` 是判定系统的比对对象：数值题至少给 `value`（建议带 `unit` 与 `evidence`），
是非题给 `value: "yes"/"no"`，拒答题答复文本里要出现拒答关键词。没有 `final_json` 时
判定退化到 `answer_text` 文本比对（抽取/拒答题可行，数值题会 FAIL）。

`audit` 是**没有 OTel 上报时的工具轨迹兜底**：数组每项可以是
`{"tool": 名称, "status": "ok|denied|error", "summary": 摘要}`（裸字符串也接受，
`denied/blocked/refused` 记为被拦截调用）。平台在轨迹里没有工具 span 时用它当工具
序列——工具类 checkpoint、红线扫描、轨迹"做了什么"摘要照常工作。有 OTel 上报时
以 OTel 为准，audit 不重复计。`usage_total` 则在 span 没带用量时兜底成本维。

### 2. `GET /capabilities`（推荐）

返回 Agent 支持的剖面列表。平台发起评测前与题库的领域包要求做**能力握手**：需要的剖面
Agent 没声明 → 对应题目 SKIPPED（带原因），不会误判失败。

```json
{"agent": "my-agent", "profiles": ["base", "bank"],
 "traces": true,                       // 是否上报 OTel span（影响平台等轨迹的窗口）
 "llm_base_url_supported": true}
```

`traces` 只影响等待窗口：声明 `true` 时平台每题最多等 15 秒收异步导出的 span；声明了
capabilities 但没写 `traces` 视为不上报（3 秒）；完全没有 `/capabilities` 的未知
Agent 取中间值 8 秒。不上报轨迹的 Agent 把它留空即可，不必假装上报。

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

## 两条快速接入路径（不改 Agent 也能评）

**① 最小示例起步**：`examples/external_agent_example.py` 是一个零依赖的单文件 Agent，
完整实现 /health、/capabilities、/invoke（含 audit 工具自报）。把它跑起来用 probe
自检，然后对着把 `_answer()` 换成你的真实逻辑：

```bash
python examples/external_agent_example.py --port 8220
agentgate probe http://127.0.0.1:8220
```

**② 操作员在环（人/LLM 会话作答）**：`tools/agent_relay.py` 把任何交互式答手
（人、ZCode、一个 LLM 会话）变成被测 Agent——invoke 到来时把题面落盘等待，操作员
写入答案文件后原样回传：

```bash
python tools/agent_relay.py --port 8210 --spool ./spool
# 目标填 http://127.0.0.1:8210；平台每题会停在 spool/pending/<stem>.json 等你
# 把 spool/answers/<stem>.json 按 invoke 响应格式写好
```

## 发起前先 probe（合约自检）

烧一轮全量跑测之前，先用内置探针验证目标端点（CLI、API、发起页"测试连接"按钮三种
入口等价）：

```bash
agentgate probe http://127.0.0.1:8200
#  ✓ health        HTTP 200
#  ✓ capabilities  profiles: base, bank …
#  ✓ invoke        HTTP 200 in 0.3s
#  ✓ answer_text   131 chars
#  ✗ final_json    missing — numeric/yes-no cases will FAIL without it
#  ! audit         empty — tool checkpoints need OTel spans or audit entries
```

`✗` 表示跑测会失败或误判（先修再跑）；`!` 表示能跑但某个维度/信号会降级
（可选端点缺失只 warn，与平台对未知 Agent 的宽松策略一致）。

## 接入自检清单

1. `agentgate probe <目标>`：没有 ✗ 项。
2. 平台发起 1 题的 run（用 `case_ids` 精确指定），在执行详情页看到 trace_id 与工具序列。
3. 勾选消息级录制重跑：报告出现轨迹分析节与成本维。
4. 全绿后再上全量题库（先抽样估成本）。

环境部署问题见 [环境准备与一键部署](deployment.md)；判定规则见 [评分规则](scoring.md)。

## 常见问题

- **全部题目 FAIL，run 状态是 failed，错误是 target unreachable**：被测地址不可达（agent 没起/端口写错）。平台不给分数——这类 run 的六维全是 n/a，修正地址后重跑。
- **我的 Agent 没有 /capabilities**：平台按未知 agent 处理（宽松：不跳题），但声明剖面的库会因无法握手而全部 SKIPPED——建议实现该端点。
- **工具类 checkpoint 全部失分 / 轨迹分析是空的**：Agent 没上报任何工具序列。要么按上面接 OTel（`OTEL_EXPORTER_OTLP_ENDPOINT` 指到平台 :4318），要么在响应 `audit` 里如实报告每次工具调用。`agentgate probe` 的 audit 检查项会提前提示这一点。
- **每题结果前有几秒固定延迟**：平台在等异步导出的 span。Agent 不上报 OTel 时在
  `/capabilities` 里如实声明（不写 `traces`），等待窗口会自动缩短。
- **probe 的 invoke 探测超时**：正常现象于"操作员在环"中继（没人写答案文件）。中继
  模式下跳过 probe 的 invoke 项、直接发起 run 用 spool 工作流即可。
