<div align="right">

[English](../en/run-eval.md) | 简体中文（本页）

</div>

# 发起评测与任务归档

本文档覆盖一次评测的完整生命周期：发起、排队与执行、逐题结果与人工判 pending 题、
报告查看、以及任务归档（生成了哪些文件、保留与删除规则）。
判分口径见 [评分规则](scoring.md)；报告怎么读见 [诊断报告](diagnostic-report.md)。

## 1. 发起（执行页 → 发起评测）

| 配置 | 说明 |
|---|---|
| 任务名 | 业务名即可（自动追加时间戳生成唯一名，可按前缀搜索） |
| 被测 Agent | `/invoke` 所在 base URL（同机样例 Agent：`http://127.0.0.1:8200`；compose：`http://jiuwen-agent:8200`） |
| 连接自检 | 目标填好后点 **测试连接（合约自检）**：平台真实打一次 `/invoke` 并逐项校验响应契约
  （✗ = 会跑失败/误判，! = 某维度降级）；也可用 `agentgate probe <URL>` |
| 题库与 level | 多选任意可见题库；level 过滤（L0/L1/L2）叠加在"我的跑法"之上 |
| case_ids | 可选：精确指定题目（逗号/换行分隔），抽样估成本用——**先小样本后全量** |
| 消息级录制 | 默认开：LLM 流量经平台代理留痕（轨迹分析/成本维依赖它） |
| ✨ AI 辅助判题 | 默认开（需在用户中心配置自己的 AI）：待判题自动出 AI judge 建议、失败题出根因、报告自动带 AI 摘要 |
| 稳定性验证 | 默认关：开启后同题重复 k 次、比对输出是否一致（结果一致性口径，与成功维正交；成本约 k 倍） |
| 预约执行 | 可指定未来时间 |

发起前平台会 dry-run 解析题目集合给出实时题数与预估耗时；能力握手在执行时进行
（Agent 缺剖面 → 题目 SKIPPED 带原因，不误判失败），握手前可先做连接自检）。

![发起评测](../images/06-run-create.png)

## 2. 排队与执行

worker 默认**串行**（同一时刻一个 run，其余排队，详情页显示队列位置）；部署时可用环境变量
`AGENTGATE_WORKER_CONCURRENCY` 开启 run 级并行（bank 之间互不影响，逐题行为不变）。
执行中逐题实时写入结果，页面的"预计还需约 X 秒"随进度更新。取消：排队中的立即取消，
执行中的在题目间响应取消。

## 3. 逐题结果与人工判 pending

执行详情页的逐题表实时刷新：每题 verdict（通过/失败/待判/跳过）、tokens、耗时、
诊断摘要（ASI）。四类 verdict 的含义：

| verdict | 含义 | 谁来定 |
|---|---|---|
| 通过 / 失败 | 硬校验唯一判定 | 平台自动 |
| **待判 PENDING** | free_text 等硬校验无结论的题 | AI judge **建议**（配置了 AI 时）→ 人工终裁 |
| 跳过 SKIPPED | 环境不满足（Agent 缺剖面等） | 平台自动（不算失败） |

PENDING 题的处理路径（都在逐题结果页完成，证据与裁决同屏）：
① 点「✧ 仲裁」打开详情弹窗——题目、Agent 回答、标准答案、工具序列、判定明细一目了然；
② 「✨ AI 判」出建议（含评分点覆盖 y/x），也可点「✨ 批量 AI 判」以后台任务消化全部待判题（入队 + 轮询进度，可中途停止）；
③ 人工终裁通过/失败（记录复核人与说明）——**全部待判题裁完后**总分与 final_gate 自动折算，
报告可用「↻ 重建报告」刷新。开启"judge 高置信自动采纳"时，run 执行中 confidence=high 的
建议会自动转正（judge.jsonl 留痕）。PENDING **不计入失败**，门禁显示 `PENDING(n)`。
目标不可达（如 Agent 地址写错）的 run 直接标记 **failed** 并给出原因，不给误导性分数，
相关维度 n/a。

## 4. 报告

跑完即出六维雷达、分维度诊断卡、双语报告与产物归档（细节见
[诊断报告](diagnostic-report.md)）。同类库再跑一次即可在对比页做雷达叠加与分维 Δ：

![对比](../images/09-compare.png)

## 5. 任务归档：生成哪些文件

run 完成后，全部产物归档到数据卷 `results/<run_id>/`（详情页"产物"页签可下载）：

| 文件 | 内容 |
|---|---|
| `report.md` / `report-en.md` | 双语完整报告 |
| `scores.json` | 六维分数机读版（run 级 + 逐题 + 诊断卡） |
| `eval_results.json` | 逐题完整判定记录 |
| `failures.jsonl` | 失败题清单（ASI） |
| `judge.jsonl` | AI judge 建议留痕（有 AI 参与时） |
| `answers.jsonl` | 每题 Agent 完整回答与 FINAL JSON（详情弹窗 / AI 改 gold 用） |
| `spans_raw.json` / `llm_calls_raw.json` | 原始轨迹与消息级调用记录 |
| `runs_meta.json` | 逐题元信息 |

## 6. 归档与删除规则

- **自动清理**：平台设置 `report_retention_days`（默认 30 天）——worker 每小时清理过期
  run 的**结果目录**；`runs` 表记录（任务名/结果摘要）保留，页面显示但产物已清。
- **手动删单个 run 的产物**：删数据卷 `results/<run_id>/` 目录即可（runs 记录仍在）。
- **彻底重置评测历史**：方式 B `docker compose down && rm -rf agentgate/data`；
  方式 A `rm -rf /opt/agentgate-platform/data`（**会连账号/题库一起删**，慎用）。
- **归档**：`tar -czf data-$(date +%F).tar.gz -C <数据卷目录> results`——run 结果含用户
  数据与 Agent 答复，属私有数据，**不上传公开仓库**。
- 运行时数据与代码的完整边界表见部署文档第 10 节。
