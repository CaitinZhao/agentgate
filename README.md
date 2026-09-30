<div align="right">

[English](README-en.md) | 简体中文（本页）

</div>

# AgentGate

AgentGate 是一个开源的企业 Agent 评测系统。

![诊断报告](docs/images/08-run-report.png)

## 它能做什么

| 能力 | 说明 |
|---|---|
| 题库管理 | 在线出题（数值/是非/抽取/自由文本/拒答/状态六种题型）、Excel 批量、开源 benchmark 一键改编 |
| 发起评测 | 多题库任意组合、level 过滤、消息级录制（默认开）、可选稳定性验证（同题重复 k 次） |
| 自动判定 | 三层判定：硬校验（红线/格式/终答）→ AI judge 建议 → 人工终裁；数值题支持**检查点中间分** |
| 六维评分 | 任务成功 / 结果质量 / 过程可靠 / 稳定 / 执行效率 / 成本 / 安全，雷达图 + 分维度诊断卡 |
| 轨迹与诊断 | OTLP span 轨迹 + 消息级录制，跳步直答 / 循环 / 断链 / 违规尝试 / 注入泄露全部进诊断 |
| AI 辅助（可选） | 报告 AI 摘要、失败题根因、free_text 题 judge 建议、出题与领域包起草 |
| 权限与多用户 | owner / admin / member / viewer 四级角色；公共题库上每人可有自己的跑法 |


## 5 分钟上手

前提：一台装了 Docker 的 Linux 服务器。两个仓库并排克隆：

```bash
git clone https://github.com/<org>/agent-contracts.git
git clone https://github.com/<org>/agentgate.git
cd agentgate
OWNER_PASSWORD='你的密码' docker compose up -d --build     # 平台（镜像内自建前端）
docker compose --profile agent up -d --build               # 可选：样例被测 Agent
```

浏览器打开 `http://服务器IP:8030` → 用 owner 登录 → 「发起评测」目标填
`http://jiuwen-agent:8200` → 提交 → 执行详情页看六维雷达。
完整的**建库 → 评测 → 报告**图文演示见 [demo 演示](docs/zh/demo.md)；
环境细节与离线部署见 [环境准备与一键部署](docs/zh/deployment.md)。

## 文档索引

| 文档 | 内容 |
|---|---|
| [实战演练（零基础）](docs/zh/walkthrough.md) | 从零启动平台 → 建题库 → 接入编码智能体/示例 Agent → 轨迹上报 → 两轮对照评测（全程截图） |
| [demo 演示](docs/zh/demo.md) | 用样例 Agent 完整走一遍：建库 → 发起评测 → 看报告（全程截图） |
| [环境准备与一键部署](docs/zh/deployment.md) | 零基础装 Docker、两种部署方式、端口配置、离线部署、常见问题 |
| [架构设计与目录结构](docs/zh/architecture.md) | 设计原则总览 + 子文档索引 + 仓库目录说明 |
| ├ [轨迹录制](docs/zh/trace-recording.md) | Agent OTLP 上报 + 消息级录制代理，为什么是两条通道 |
| ├ [评分规则](docs/zh/scoring.md) | 三层判定、gold v2、六维计分口径 |
| ├ [沙箱执行](docs/zh/sandbox.md) | 被测 Agent 的运行形态与隔离边界（现状 + 设计） |
| ├ [AI 辅助](docs/zh/ai-assist.md) | 报告摘要 / judge 建议 / 起草，凭据与边界 |
| └ [诊断报告](docs/zh/diagnostic-report.md) | 报告结构、诊断卡、产物文件、AI 摘要 |
| [发起评测与任务归档](docs/zh/run-eval.md) | 发起、排队、人工判 pending 题、任务归档与删除规则 |
| [新建题库](docs/zh/bank-create.md) | 私有/公共新建向导、开源 benchmark 改编、AI 起草 |
| [修改题库与出题字段](docs/zh/bank-edit.md) | 每个字段的含义与例子、在线修改、Excel 批量 |
| [Agent 接入](docs/zh/agent-integration.md) | /invoke 契约、轨迹上报、消息级录制、能力握手（附样例代码） |
| [开源题库下载使用](docs/zh/public-banks.md) | FinanceBench / LoCoMo / LongMemEval / τ-bench 的下载与再生成 |
| [权限管理](docs/zh/permissions.md) | 四级角色能做什么、公共库上"我的跑法" |
| [测试指南](docs/zh/developing.md) | 开发者视角：单测 / 离线冒烟 / Web 测试 / 样例夹具 |

## 版本与边界（诚实声明）

- 判定**分层诚实**：判不了的题标 PENDING 交人工/AI judge 建议，永不硬凑；
  数据缺失的评分维度显示 n/a，不做假分。
- 公开 benchmark 题目只做观测对标，**不作为发布 accept 判据**（防数据污染）。
- 评测只负责观测与判定；accept / rollback 决策归演进控制平面（路线图）。
