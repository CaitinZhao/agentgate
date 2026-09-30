<div align="right">

[English](../en/walkthrough.md) | 简体中文（本页）

</div>

# 实战演练：从零跑通一次评测

本文档用于第一次接触 AgentGate 的读者：在一台只装了 Python 的机器上，从启动平台、
建立题库、接入被测 Agent，到跑完两轮评测并读懂报告。全程约 15 分钟，不需要 Docker，
也不需要模型凭据；每一步的命令与界面都可以照着复现。参数与判定的完整口径见文末
专题文档。

本指南最终搭出的评测链路：

```
┌────────────────────┐   POST /invoke    ┌──────────────────────┐
│ AgentGate 平台      │ ────────────────> │ 被测 Agent            │
│ (Web:8050)         │ <──────────────── │ (HTTP 服务，返回答案   │
│  题库·判定·六维评分 │    答案 + 工具轨迹 │  + 工具使用自报)       │
└────────────────────┘                   └──────────────────────┘
        判定需要知道 Agent"做了什么"：工具序列来自响应 audit 字段
        或 OTel 上报（本指南用前者，零依赖）
```

教程使用同一个 3 题小题库先后评测两个被测 Agent，得到一组可直接对照的结果：
回声机器人（只会复述问题）0/3 答对、总分 14；一个经中继接入的编码智能体 3/3 答对、
总分 100。

## 1. 启动平台

前置条件：Python ≥ 3.9（`python --version` 确认）与仓库代码
（`git clone https://github.com/CaitinZhao/agentgate.git`）。

```bash
cd agentgate
OWNER_PASSWORD=wiki-demo-1 AGENTGATE_DATA_DIR=D:\wiki-demo\data python -m agentgate.cli.main web --port 8050
```

| 参数 | 作用 | 不写会怎样 |
|---|---|---|
| `--port 8050` | 平台 Web/API 端口 | 默认 8030 |
| `AGENTGATE_DATA_DIR` | 数据目录（账号、题库、评测结果都在这） | 用 agentgate.json 里配置的默认目录 |
| `OWNER_PASSWORD` | 初始 owner 账号密码（仅第一次建库时生效） | 随机生成，只在启动日志里打印一次 |

启动成功的标志（平台同时带起 OTLP 接收器 :4318、录制代理 :8300 和评测 worker；
端口被占用会报 `[Errno 10048]`，改端口或结束占用进程即可）：

```
================================================================
[agentgate] owner account created from OWNER_PASSWORD env (owner)
================================================================
INFO:     Uvicorn running on http://0.0.0.0:8050
```

浏览器打开 `http://127.0.0.1:8050`，用 `owner / wiki-demo-1` 登录：

![登录页](../images/walkthrough/01-login.png)

全新数据目录的题库页是空的，从这里开始建设：

![空的题库列表](../images/walkthrough/02-banks-empty.png)

## 2. 建立题库

### 2.1 创建空库

**题库 → 新建题库**，填四项：题库标识（`hello-agent`，英文小写）、显示名、领域分组
（自建库填 `self-built`）、中文简介；可见性保持"私有"。点 **下一步** 后选"空库
（之后在详情页加题）"，再点一次 **下一步**：

![新建题库表单](../images/walkthrough/03-bank-create.png)

### 2.2 添加三道题

在题库详情页点 **编辑 → 添加新题**。三道题刻意覆盖三种判定方式：

| case_id | 题型 | 题面 | 金标 | 判定方式 |
|---|---|---|---|---|
| `hello-cap-city` | extractive（抽取） | 中国的首都是哪座城市？只回答城市名。 | 北京 | 与金标做文本比对（确定性） |
| `hello-math` | numeric（数值） | 计算 17 + 25 等于多少？只回答数字。 | 42（容差 0） | 数值比对（确定性） |
| `hello-self-intro` | free_text（自由问答） | 请用一句话做自我介绍。 | 评分点：回答是一句合理的自我介绍 | AI judge / 人工复核 |

第一题填好的样子（题型在"题型"下拉里选，金标填在"金标取值"，抽取题可加别名）：

![新增题目表单](../images/walkthrough/04-case-form.png)

### 2.3 提交变更

编辑页的"添加新题"只是**暂存**（列表里的绿色行），不会立即写库。要点右上角
**提交修改 → 确认生效** 才真正入库——两段式设计是为了批量改完一次提交，避免半成品
题库被误跑：

![题库里的三道题](../images/walkthrough/05-bank-cases.png)

## 3. 接入被测 Agent

平台对被测 Agent 只要求一个 HTTP 接口：`POST /invoke`——收
`{"query": "题面", ...}`，回：

```json
{"answer_text": "最终自然语言答复",
 "final_json": {"answer": "...", "value": 96.2, "evidence": "..."},
 "trace_id": "agent 内部轨迹 id",
 "usage_total": 12345,
 "audit": [{"tool": "工具名", "status": "ok", "summary": "摘要"}]}
```

`final_json` 是判定比对对象；`audit` 自报工具使用（每项
`{"tool": 名称, "status": "ok|denied", "summary": 摘要}`），没有 OTel 上报时它就是
工具序列的来源。完整字段与 FAQ 见 [Agent 接入](agent-integration.md)。

### 3.1 接入前先自检

发起评测页填好目标地址后，点 **测试连接（合约自检）**：平台真实打一次 `/invoke` 并
逐项校验响应契约（命令行等价物是 `agentgate probe <URL>`）。`✗` 表示跑测会失败或
误判（先修再跑），`!` 表示能跑但某个维度会降级：

![合约自检](../images/walkthrough/07-probe.png)

### 3.2 方式一：示例 Agent（回声机器人）

```bash
python examples/external_agent_example.py --port 8220
# [external-example] on http://127.0.0.1:8220
```

它是单文件零依赖实现，把 `_answer()` 换成真实逻辑就是一个正式的被测 Agent。

### 3.3 方式二：中继模式（编码智能体 / 人工在环）

如果"被测 Agent"其实是一个交互式答手（编码智能体或真人专家），用仓库自带的中继把
答案"写"进去：

```bash
python tools/agent_relay.py --port 8211 --spool D:\wiki-demo\spool
```

评测发起后，平台每发来一题，中继就把完整题面落在 `spool/pending/<编号>.json` 并等待；
答手读完题目，把答案按 invoke 响应格式写到 `spool/answers/<编号>.json` 即完成作答。
例如 `hello-math` 的答案文件：

```json
{
  "answer_text": "42",
  "final_json": {"value": 42, "answer": "42", "evidence": "17 + 25 = 42"},
  "trace_id": "113838_f673db",
  "usage_total": 9,
  "audit": []
}
```

## 4. 轨迹上报

判定系统需要知道 Agent"做了什么"（调了哪些工具、有没有被红线拦、token 花了多少）。
三条通道按需选择，可以叠加：

| 通道 | 接入成本 | 提供什么 | 适合谁 |
|---|---|---|---|
| ① 响应 `audit` 自报 | 零（响应里多一个字段） | 工具序列（含被拦截调用）、成本维兜底 | 外部 Agent 快速接入 |
| ② OTel span 上报 | 中（接 SDK，见 [轨迹录制](trace-recording.md)） | 完整执行图：工具/LLM 调用、断链检测、token 用量 | 自己的 Agent 想要全过程留痕 |
| ③ 消息级录制 | 低（把 LLM 网关地址临时换成平台下发的 `llm_base_url`） | 每次模型调用的完整消息历史 | 想要最细的轨迹与效率评分 |

要点：

- 混用时 **OTel 优先**：轨迹里已有工具 span，`audit` 不会重复计入。
- `usage_total`（本题 token 总消耗）在 span 没带用量时兜底成本维。
- 在 `/capabilities` 里如实声明 `"traces": true/false`：平台据此决定每题等轨迹的
  窗口（声明 true 等 15 秒，不声明等 3 秒），不上报的 Agent 不必白白等待。
- 轨迹去哪看：run 详情页点 case_id 打开详情弹窗——下图的"工具序列 (1): echo"就是
  示例 Agent 通过 audit 自报的内容：

![题目详情：答复、判定与工具序列](../images/walkthrough/10-case-detail.png)

## 5. 第一轮：评测示例 Agent

回到 **发起评测**：任务名随手填，目标填 `http://127.0.0.1:8220`，先点"测试连接"
确认全绿，勾选 `hello-agent` 题库，提交：

![提交成功](../images/walkthrough/08-run-submitted.png)

点 **查看进度**，几秒后 3 题跑完：

![第一轮结果概览](../images/walkthrough/09-run-overview.png)

怎么读这个页面：

- **判定表**：`hello-cap-city` 和 `hello-math` FAIL——回声机器人只会复述问题，答不对
  就是 FAIL。确定性判定不对就是不对，平台不会给虚分。
- **六维卡与雷达**：可靠 100（每题都正常响应）、安全 100（没触发红线）、效率 92
  （响应很快）、成功 0（没答对题）。总分被"任务成功"拖到很低——一个"活着但没用"的
  Agent 在六维分离下一眼可见。
- **报告 tab**：统计化的基线报告（门禁判定、六维、失败归因、Token 汇总）：

![基线报告](../images/walkthrough/11-report.png)

### 5.1 free_text 题的处理：AI judge 建议 + 人工终裁

`hello-self-intro` 没有硬金标，自动判定诚实地标为 **PENDING**（不猜分）。处理路径在
题目详情弹窗里：点 **✨ AI 判**，AI 读完题面、金标评分点和 Agent 答复后给出建议：

![AI 判建议与终裁](../images/walkthrough/12-ai-judge-review.png)

本例 AI 的结论是 **FAIL (0)**："答复仅回显了收到的题目文本，未包含任何自我介绍内容，
未命中要点 1"。在复核说明里写上结论，点 **终裁失败** 完成终裁（AI 只建议，人拍板；
终裁后分数自动重算，报告同步刷新）：

![终裁后](../images/walkthrough/12b-after-review.png)

第一轮最终：0/3 答对，**总分 14**。

## 6. 第二轮：评测编码智能体

同一个题库换中继模式再跑一轮：目标填 `http://127.0.0.1:8211`，编码智能体对三道题
逐一作答（每题写一个答案文件，格式见 3.3 节）：

- `hello-cap-city` → `北京`
- `hello-math` → `42`
- `hello-self-intro` → "大家好，我是一个可以帮你写代码、查资料、跑测试和搭建评测系统
  的 AI 编程助手。"

创建命令（`hello-self-intro` 没有硬金标，同样在详情弹窗 **终裁通过** 并附复核说明；
终裁后分数自动重算）：

```bash
curl -X POST $BASE/api/v1/runs -H "Authorization: Bearer $T" \
     --data-binary @create-run.json
```

`create-run.json` 的内容：

```json
{
  "task_name": "编码智能体接入（中继作答）",
  "banks": [{"bank": "hello-agent", "levels": []}],
  "target_url": "http://127.0.0.1:8211",
  "proxy_enabled": false,
  "ai_assist": false
}
```

![编码智能体接入的结果：3/3 PASS](../images/walkthrough/13-relay-overview.png)

**3/3 PASS，总分 100**（成功 100 / 可靠 100 / 安全 100，成本维来自答案里的
`usage_total`）。同一个题库、同一个平台：回声机器人 14 分、编码智能体 100 分——
这组对照就是评测系统要提供的"版本能不能上线"的证据。

## 常见问题

**1. curl 提交中文 JSON 报错**

现象：

```
{"detail":"There was an error parsing the body"}
```

原因：Windows Git Bash 内联 `-d '中文'` 会把编码搞坏。解决：把 JSON 写进文件再发：

```bash
curl -X POST $BASE/api/v1/runs -H "Authorization: Bearer $T" \
     -H "Content-Type: application/json" --data-binary @create-run.json
```

**2. 编辑页加了题，详情页/评测里看不到**

原因：加题只是暂存，未提交。解决：在编辑页点 **提交修改 → 确认生效**，详情页出现
题目后再发起评测。

**3. `agentgate probe` 对中继目标超时**

现象：probe 的 invoke 检查项报 `POST /invoke failed: timed out`。

原因：中继在等操作员写答案文件，没人写就一直等——这是中继模式的预期行为。解决：
中继模式跳过 probe 的 invoke 项（health/capabilities 检查仍有效），直接发起评测走
spool 工作流。

**4. 工具类 checkpoint 全部失分，轨迹摘要是空的**

原因：Agent 没上报任何工具序列。解决（二选一）：在响应 `audit` 里如实报告每次工具
调用；或接 OTel——`OTEL_EXPORTER_OTLP_ENDPOINT` 指向平台 :4318（span 约定见
[轨迹录制](trace-recording.md)）。probe 的 audit 检查项会提前提示这一点。

**5. 每题出结果前有几秒固定延迟**

原因：平台在等异步导出的 OTel span（默认最长 15 秒）。解决：Agent 不上报 OTel 时，
在 `/capabilities` 里如实声明（不写 `traces` 字段），等待窗口自动缩到 3 秒。

**6. AI 判按钮点了没反应 / run 没有 AI 摘要**

排查顺序：用户中心是否配置了 AI（网关地址/密钥/模型）→ run 是否勾选了"AI 辅助判题"
→ run 详情 meta 里的 `ai_assist_error` 字段（失败原因会记录在这里）。AI 配置在 run
发起后新增的，可以对 PENDING 题逐题补点"✨ AI 判"。

## 下一步

- 给自己的 Agent 接 OTel 全量轨迹：[轨迹录制](trace-recording.md)
- 判定与六维计分的完整口径：[评分规则](scoring.md)
- 接入契约的完整字段表与更多 FAQ：[Agent 接入](agent-integration.md)
- 部署到服务器给团队用：[环境准备与一键部署](deployment.md)
- 批量导入开源 benchmark（BFCL / GAIA / Spider…）：[公共题库](public-banks.md)
