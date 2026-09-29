# cases/ — 题库目录

两类内容，**提交策略不同**：

| 目录 | 来源 | 是否提交 git |
|---|---|---|
| `example/` | 自建核心题（5 题：数值/拒答，含检查点与口径） | ✅ 提交（自带全部数据，可直接跑） |
| `injection/` | 自建注入抗性题（6 题 + 上下文文件） | ✅ 提交（自带全部数据） |
| `locomo/` `longmem/` | LoCoMo / LongMemEval 官方数据集改编 | ❌ 忽略（含第三方数据集渲染出的对话上下文，共 ~18MB） |
| `FB/` `FB-150/` | FinanceBench 数据集改编 | ❌ 忽略（题目与金标派生自第三方数据集） |
| `tau-airline/` `tau-retail/` | τ-bench 任务改编 | ❌ 忽略（同上） |
| `fineval/` | FinEval 数据集 autoadapt 产物 | ❌ 忽略（同上） |
| `bfcl/` `spider/` `gaia/` `airbench/` `agentdojo/` `harmbench/` | 公开 benchmark 数据集改编（W21） | ❌ 忽略（同上；spider 还含 SQLite / airbench 含语料子集） |
| `cases.db` | 旧版集中式题库（迁移演示用） | ❌ 忽略 |

## 重新生成被忽略的题库（需要先获取对应数据集）

```bash
pip install -e .   # agentgate 包

# FinanceBench（数据集：github.com/ParetoIntel/financebench）
python -m agentgate.case.build_banks --fb          # 150 题 → cases/FB-150/

# BFCL v4（数据集：github.com/ShishirPatil/gorilla 的 bfcl_eval/data）
python -m agentgate.case.build_banks --bfcl        # 529 题（seed=42 分层抽样）→ cases/bfcl/

# Spider（数据集：HF HAL-9001/spider-databases 的 spider_data.zip 解压到 reference/refs/data/spider/）
python -m agentgate.case.build_banks --spider      # 654 题 + 8 库 SQLite → cases/spider/

# GAIA（validation 文本子集 parquet → reference/refs/data/gaia/）
python -m agentgate.case.build_banks --gaia        # 127 题 → cases/gaia/

# AIR-Bench（HF AIR-Bench/qa_wiki_en 的 queries/corpus + qrels-qa_wiki_en-dev → reference/refs/data/airbench/）
python -m agentgate.case.build_banks --airbench    # 120 题 + 语料子集 → cases/airbench/

# AgentDojo（数据集：github.com/ethz-spylab/agentdojo 源码仓库）
python -m agentgate.case.build_banks --agentdojo   # 391 题（用户任务×(1+全部注入)）→ cases/agentdojo/

# HarmBench（数据集：github.com/centerforaisafety/harmbench）
python -m agentgate.case.build_banks --harmbench   # 200 题（standard 行为）→ cases/harmbench/

# LoCoMo（数据集：github.com/snap-research/locomo 的 data/locomo10.json）
python -m agentgate.case.build_banks --locomo \
  --locomo-data <path/to/locomo10.json>            # 1986 题 + 上下文 → cases/locomo/

# LongMemEval（数据集：github.com/xiaowu0162/LongMemEval 的 oracle 子集）
python -m agentgate.case.build_banks --longmem \
  --longmem-data <path/to/longmemeval_oracle.json> # 500 题 + 上下文 → cases/longmem/

# τ-bench（数据集：github.com/sierra-research/tau-bench）
python -m agentgate.case.build_banks --tau \
  --tau-repo <path/to/tau-bench>                   # airline 50 / retail 115 题

# FinEval 等“问题/答案”两列数据集（任选 jsonl/csv）：
python -m agentgate.cli autoadapt --source <file.jsonl> --id-field id \
  --question-field question --answer-field answer --suite fineval --out cases/fineval/cases.jsonl
```

生成后这些目录就在本地可用了（平台部署打包 / compose 都会带上）。
所有题库均为 **gold v2** 结构（题型 / gold.final / 检查点 / rubric），见
`docs/zh/case-editing.md` 与 `reference/落地方案v2/35_*.md`。
