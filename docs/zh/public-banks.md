<div align="right">

[English](../en/public-banks.md) | 简体中文（本页）

</div>

# 开源题库下载使用

本文档说明平台内置的开源 benchmark 题库分别考察什么、怎么获取数据集并再生成、
原生口径（与论文/原生框架可比的第二套读数）怎么算、以及使用时的防污染约定。
自建题库的在线出题见 [新建题库](bank-create.md)。

## 平台里有哪些开源题库

| 题库 | 来源数据集 | 考察什么 | 平台口径判定 | 原生口径 |
|---|---|---|---|---|
| `fb`（150 题） | FinanceBench 真实财报问答 | 真实财报 PDF 检索、数值换算、拒答 | 数值/是非硬校验 + 证据引用（fb 领域包）+ free_text rubric | typed 硬校验 + judge 建议 |
| `bfcl`（529 题） | BFCL v4（gorilla，Apache-2.0） | 函数调用：单次/并行/多选/irrelevance | 原生 AST 等价匹配（平台 typed 判定同源） | AST-equivalent 准确率 |
| `spider`（654 题） | Spider dev 8 库全题（CC BY-SA 4.0） | 文本到 SQL（sql_query 工具 + schema） | 执行准确率（金标/Agent SQL 结果集比对） | execution accuracy |
| `gaia`（127 题） | GAIA validation 文本子集（Apache-2.0） | 通用助手（推理/计算；联网题如实暴露缺口） | 官方 EM 归一化精确匹配 | exact match |
| `airbench`（120 题） | AIR-Bench 24.05 qa/wiki/en dev（CC BY-NC-SA 4.0） | 检索（doc_search 工具 + 子集语料） | nDCG@10 ≥ 0.5 计 typed PASS | nDCG@10 / recall@5 |
| `agentdojo`（391 题） | AgentDojo v1_2_2 banking+workspace（MIT） | 工具任务 + 注入攻击（直接攻击模板） | 环境原生 utility/security 条件（agentdojo 官方代码） | utility / security 双指标 |
| `harmbench`（200 题） | HarmBench standard 行为（Apache-2.0） | 安全红队（有害请求顺从率） | 拒答模式 + judge 建议 → PENDING/人工 | ASR（攻击成功率，越低越好） |
| `locomo`（1986 题） | LoCoMo 多人长对话 | 长对话记忆问答、对抗拒答 | 数值硬校验 + 拒答关键词 + free_text rubric | typed 硬校验 + judge 建议 |
| `longmem`（500 题） | LongMemEval oracle | 跨会话记忆 | 数值硬校验 + free_text rubric | typed 硬校验 + judge 建议 |
| `tau-airline` / `tau-retail` | τ-bench 任务 | 工具编排与政策遵守 | 工具检查点（中间分）+ judge/人工 | typed 硬校验 + judge 建议 |
| `fineval`（42 题） | FinEval 考试题 | 金融专业知识 | 数值硬校验 + judge/人工 | typed 硬校验 + judge 建议 |
| `injection`（6 题） | 自建注入测试语料 | 注入抗性（金丝雀泄露检测） | 安全红线 + rubric | typed 硬校验 + judge 建议 |

## 双口径：平台分 与 原生分

每个 run 报告给出两套读数：

1. **平台口径**（六维雷达）：本平台自己的方法论——成功/质量/可靠/稳定/效率/成本/安全，
   所有题库统一口径，可横向对比。
2. **原生口径**（报告"原生口径"一节 + 结果页"原生口径"卡）：按数据集自己的评分规则计算——
   BFCL 用 AST 等价匹配、Spider 用执行准确率、GAIA 用官方 EM 归一化、AIR-Bench 用 nDCG@10、
   AgentDojo 用官方 utility/security 条件、HarmBench 用顺从率（ASR）。方便和论文、
   其他评测框架的公开数字对齐。

两种读数都**只做观测**：公开题可能已进过被测模型的训练集，分数不进入任何发布 accept 判据。
已知偏差都记录在题库 README 与题目的 adaptation 字段（如 AIR-Bench 子集语料、
HarmBench judge 模型与官方 Llama-13B 分类器不同）。

## 获取数据集并再生成

派生题库**不入 git**（第三方数据集内容）。构建前需要把数据集放到
`reference/refs/`（`code/` 与 `data/`），然后：

| 数据集 | 获取 | 再生成命令 |
|---|---|---|
| FinanceBench | github.com/ParetoIntel/financebench | `python -m agentgate.case.build_banks --fb` |
| BFCL v4 | github.com/ShishirPatil/gorilla（bfcl_eval/data） | `python -m agentgate.case.build_banks --bfcl` |
| Spider | HF `HAL-9001/spider-databases`（spider_data.zip）或官方 dev 包 | `python -m agentgate.case.build_banks --spider` |
| GAIA | HF `gaia-benchmark/GAIA`（门控；文本子集镜像 parquet） | `python -m agentgate.case.build_banks --gaia` |
| AIR-Bench | HF `AIR-Bench/qa_wiki_en` + `AIR-Bench/qrels-qa_wiki_en-dev` | `python -m agentgate.case.build_banks --airbench` |
| AgentDojo | github.com/ethz-spylab/agentdojo | `python -m agentgate.case.build_banks --agentdojo` |
| HarmBench | github.com/centerforaisafety/harmbench | `python -m agentgate.case.build_banks --harmbench` |
| LoCoMo | github.com/snap-research/locomo（data/locomo10.json） | `python -m agentgate.case.build_banks --locomo --locomo-data <路径>` |
| LongMemEval | github.com/xiaowu0162/LongMemEval（oracle 子集） | `python -m agentgate.case.build_banks --longmem --longmem-data <路径>` |
| τ-bench | github.com/sierra-research/tau-bench | `python -m agentgate.case.build_banks --tau --tau-repo <路径>` |
| FinEval | 自选"问题/答案"数据集（jsonl/csv） | 界面"外部数据集"或 `agentgate autoadapt`（自动分桶 + 人工检查清单） |

抽样/子集口径（可复现，seed 与数量记录在题库 README）：BFCL 分层抽样（每类有 cap，seed=42）；
Spider 取 8 个 dev 库的**全部**题目（wta_1 因单库 105MB 不纳入）；AIR-Bench 语料为
qrels 文档 + 随机补样 5000 段；GAIA 为 validation 文本子集全量 127 题。

**工具依赖分级（env_scope）**：`agent-side`（默认）= 环境由被测 agent 提供，缺剖面时整库
SKIPPED（公平握手）；`judge-only` = 环境资产只用于**判定**（如 Spider 的 SQLite），agent 可以
用任何方式作答——缺剖面只会降级剖面提示词，不会跳过（spider 库即此口径）。

**部署注意**：`spider` 的 SQLite 与 `airbench` 的语料子集是**随镜像分发**的 agent 资产
（`Dockerfile.agent` COPY `cases/spider/dbs` 与 `cases/airbench/corpus.jsonl`）——
构建镜像前必须先跑 `build_banks`；`fb` 剖面还需要财报 PDF 语料（挂载说明见
`deploy/fb_pdfs/README.md`）；`agentdojo` 剖面由 agent 容器内 pip 安装的 agentdojo
官方包提供环境与判定条件。

运行这些题库的环境要求见各库的领域包（题库详情页"领域包"卡）：如 `fb` 需要 Agent 声明
`fb` 剖面并挂载财报语料；Agent 不支持时对应题目 SKIPPED，不会误判失败。

## 接入一个新的开源题库

分两条路，按数据集形态与判定要求选：

**路径 A：界面/CLI 自动改编（简单问答类，零代码）**。数据集是 jsonl/csv 的
"问题/答案"对即可走这条路：题库页"外部数据集"或 `agentgate autoadapt --source <path>`
——自动分桶、生成题目与人工检查清单，你逐条确认后即可评测。适合 FinEval 这类
考试型数据集；需要自定义判定逻辑或环境时走路径 B。

**路径 B：完整接入（需要自定义判定/环境/官方口径）**。以接入一个假想的
`mybench` 为例，六步，每步都是真实文件：

1. **写适配器** `src/agentgate/case/public_benchmarks/mybench_adapter.py`：提供
   `build(...) -> Dict`，负责定位/下载数据集 → 逐条映射为平台题目 → 写
   `cases/mybench/cases.jsonl`。题目遵循 gold v2：`case_id / type / level /
   input.query（及可选 context）`、`gold.final + checkpoints + rubric`、
   `source`（seed 与 provenance 必填——复现与防污染都靠它）。最简参考实现：
   `gaia_adapter.py`；官方评分含参数校验的参考：`bfcl_adapter.py`。
2. **注册构建入口** `src/agentgate/case/build_banks.py`：加 `--mybench` 参数与分支
   （数据集路径参数的写法见 `--locomo-data`）。
3. **领域包** `src/agentgate/case/packs/mybench.json`：声明剖面要求、红线、
   judge 提示与各题型默认，模板见 `gaia.json`。题库 requirements 写
   `{"profile": "...", "pack": "mybench"}`；能力握手时 Agent 缺剖面 → 整库 SKIPPED
   （不误判失败）。
4. **原生口径（有官方指标时）**：`src/agentgate/analysis/native_scoring.py` 的
   `FAMILIES` / `FAMILY_LABELS` 各加一条，实现
   `score_mybench(case, response, trace, pack) -> Dict`，并在 `compute_native`
   分发链加一个分支——judge 随即把它当作 typed check（单一事实来源），报告与
   对比页自动多出"原生分"一行。自测写进 `tests/test_native_scoring.py`。
5. **环境剖面（题目需要环境时）**：样例 Agent `tests/fixtures/jiuwen_server.py`
   注册剖面与工具；资产只用于判定时改用 `"env_scope": "judge-only"`（如 spider
   的 SQLite）。
6. **建库与导入**：本地 `python -m agentgate.case.build_banks --mybench` 生成
   cases.jsonl；在 `deploy/import_banks.py` 的 `BANKS` 表加一行（显示名/类别/
   默认 level/requirements/本地 cases.jsonl/资产目录），部署机上
   `python agentgate/deploy/import_banks.py --banks mybench` 即完成建库+灌题+资产。

**接入后验证清单**：原生评分器单测全绿 → 用 `case_ids` 抽 3-5 题估成本跑小样本 →
全量；抽样口径与 seed 记进题库 README；遵守下方防污染约定（两种口径都只做观测）。

## 防污染约定

- 开源题库的得分（两种口径）只用于**跨 Agent / 跨版本观测对标与趋势**，不进入任何发布
  accept 判据——公开数据可能已进过被测模型的训练集。
- 数据集改编全部保留 `source.seed` 溯源（题库详情页可见），金标来自数据集原文，
  改编记录（剖面/环境替换/抽样口径）写在题目的 adaptation 字段。
- 自建核心题库与公开题库分开管理：发布门禁看自建库 + 回归集。
