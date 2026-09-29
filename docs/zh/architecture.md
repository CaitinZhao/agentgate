<div align="right">[English](../en/architecture.md) | 简体中文（本页）</div>

# 架构设计与目录结构

本文档总览 AgentGate 的设计原则、模块划分与仓库目录；每个设计主题的细节在子文档里，
索引见下。

## 设计原则（七条）

1. **评测只观测，不决策**。平台负责跑题、判定、出报告；accept / rollback 决策属于演进
   控制平面。评测模块没有任何写入被测系统的能力。
2. **分数的分母是完整 Agent 系统**。评分对象是"模型 + 提示词 + 工具 + 检索 + 策略"整体，
   报告记录 agent 身份与组件版本，而不是只给模型打分。
3. **分层诚实判定**。硬校验（机器能唯一判定的）永远先跑；判不了的标 PENDING 交给
   AI judge 建议 / 人工终裁，永不硬凑确定性判定；数据缺失的评分维度显示 n/a，不做假分。
4. **judge 与题库分离**。题库只声明"什么是对的"（参考答案/检查点/rubric）；怎么判
   （判定器版本、AI judge 配置）是平台配置，换 judge 不改题库。
5. **负分项进分数**。非预期短路径（跳步直答）、重复调用、违规尝试、成本爆炸都要扣分，
   防止"只看结果"的评测被最懒的 Agent 拿满分。
6. **凭据与被测系统隔离**。平台不持有被测 Agent 的模型密钥；消息级录制通过代理转发实现，
   平台自己的 AI 辅助用独立的按用户配置。
7. **配置集中、数据归数据**。题目定义是数据（表单/JSON/Excel），判定逻辑在代码；
   端口、网关等可变参数集中在部署参数与设置页。

## 模块与数据流

```text
题库（banks/*/cases.db，gold v2）
  → 发起评测（runs 队列，worker 可选并发，默认串行）
  → 被测 Agent /invoke（HTTP）+ 两条观测通道：
       ① OTLP :4318  span 轨迹（agent.run / llm.call / tool.execute）
       ② 录制代理 :8300  消息级 LLM 调用记录
  → 判定管线（红线 → 格式 → 题型终答 → 检查点 → 过程信号）
  → 六维评分（scores.json）+ 双语诊断报告（report.md / report-en.md）
  → 门禁（GREEN / FAIL / PENDING(n)）+ 产物归档（data/results/<run_id>/）
```

## 仓库目录

```text
agentgate/
├── src/agentgate/
│   ├── case/          # 题库模型（gold v2）、SQLite 存储、Excel 往返、领域包注册表
│   ├── evaluator/     # 判定管线（红线/格式/题型终答/检查点）与 EvalResult 组装
│   ├── analysis/      # 轨迹信号（跳步/循环/断链/注入）、六维计分器、AI 客户端
│   ├── control/       # 评测编排：run_case_set（发起→判定→评分→报告）
│   ├── run/           # 目标 Agent 的 HTTP 调用与 span 归一化
│   ├── trace/         # OTLP 接收器、消息级录制代理、span 归一化
│   ├── result/        # 门禁、双语报告
│   └── webapp/        # FastAPI 后端 + 评测 worker（可选并发）+ 四级权限
├── web/               # Vue 3 前端（Vite + TS，构建产物进平台镜像）
├── tests/             # 开发者测试（含样例 Agent 夹具 tests/fixtures/）
├── cases/             # 自带题库（example/injection）+ 派生题库再生成脚本
├── deploy/            # 部署脚本（一键远程 / compose / 体检 / 视觉审查）
├── docs/              # 本文档（zh + en 双语，截图在 docs/images/）
├── Dockerfile.web     # 平台镜像（多阶段，镜像内自建前端）
├── Dockerfile.agent   # 样例被测 Agent 镜像
└── docker-compose.yml # 一键起平台（+可选样例 Agent）
agent-contracts/       # 轨迹/评测结果的共享 schema 契约包（平台与样例 Agent 共用）
```

## 子文档索引

| 主题 | 文档 | 一句话 |
|---|---|---|
| 轨迹录制 | [trace-recording.md](trace-recording.md) | 两条观测通道各自看什么、为什么都要有 |
| 评分规则 | [scoring.md](scoring.md) | 三层判定流程、gold v2 字段、六维计分口径 |
| 沙箱执行 | [sandbox.md](sandbox.md) | 被测 Agent 的运行形态、隔离边界与路线图 |
| AI 辅助 | [ai-assist.md](ai-assist.md) | 平台自己的 LLM 用在哪、怎么配、边界在哪 |
| 诊断报告 | [diagnostic-report.md](diagnostic-report.md) | 一份 run 产出哪些报告与产物、怎么读 |
