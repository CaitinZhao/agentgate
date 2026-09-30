<div align="right">

[English](../en/developing.md) | 简体中文（本页）

</div>

# 测试指南（开发者）

本文档面向 agentgate 仓库的开发者：改了代码后跑哪些测试、测试分层是怎么设计的、
新增功能时怎么补测试。用户侧的验收走查见 [demo 演示](demo.md)。

## 测试分层

| 层 | 位置 | 跑法 | 覆盖什么 |
|---|---|---|---|
| 单元/管线测试 | `tests/` | `python -m pytest tests/ -q --ignore=tests/test_jiuwen_agent.py`（**在 agentgate/ 目录下跑**） | 判定管线（题型路由/红线/检查点）、六维计分、gold v2、Web API 全链路（TestClient + MockTarget）、Excel 往返、上下文展开 |
| 离线冒烟 | `agentgate smoke` CLI | 无网络、无 LLM：脚本化答复+spans → 判定 → 门禁 | 端到端核心流（run→judge→gate） |
| 真实链路冒烟 | `deploy/smoke_jiuwen.py` | 对着真容器跑 | /invoke 契约、剖面、轨迹上报 |
| 视觉审查 | `deploy/review_ui.py` | 截图喂给视觉模型逐页挑错 | UI 布局/文案/可读性 |

## 测试设计的关键约定

1. **不依赖网络与真实模型**：Web 链路测试用 `MockTarget`（脚本化答复+spans，见
   `run/targets/base.py`），monkeypatch 掉 `worker.make_target`；判定与计分是纯函数，
   直接构造 Case/trace 断言。
2. **每用户数据隔离**：Web 测试用 `AGENTGATE_DATA_DIR` 指向临时目录（`platform()` fixture），
   平台 db/题库/结果全在 tmp 下。
3. **夹具即文档**：样例 Agent（`tests/fixtures/jiuwen_server.py` + `fin_runtime/`）同时是
   [Agent 接入](agent-integration.md)的参考实现——改 `/invoke` 契约时先改夹具再改判定。
4. **构建脚本可重放**：派生题库由 `case/build_banks.py` 与 public_benchmarks 适配器生成
   （见 [开源题库下载使用](public-banks.md)），生成物不入 git，测试只测生成器。

## 常用测试场景速查

```bash
cd agentgate
python -m pytest tests/ -q --ignore=tests/test_jiuwen_agent.py   # 全量（约 30s）
python -m pytest tests/test_doc35.py -q                          # 只跑判定/计分/gold v2
python -m pytest tests/test_webapp.py -q                         # 只跑 Web API 链路
```

| 要改的东西 | 先看/要补的测试 |
|---|---|
| 判定规则（red line / 题型 / 检查点） | `tests/test_doc35.py::test_judging_by_type` |
| 六维口径（权重/档位/封顶） | `tests/test_doc35.py::test_scores_units` |
| Web API（角色/题库/run 生命周期） | `tests/test_webapp.py`（auth matrix / run e2e / SKIPPED 契约） |
| AI 增强（judge/摘要/起草） | `tests/test_doc35.py::test_ai_settings_and_pack_endpoints`（AI 端点用假配置，不发真请求） |
| 稳定性 repeat_k | `tests/test_doc35.py::test_stability_repeat_k` |
| Excel 往返 | `tests/test_webapp_content.py::test_excel_roundtrip` |
| 部署脚本 | 无单测（参数用例在 CI 外人工跑）；改完至少 `python -c "import ast; ast.parse(open(...).read())"` |

## 新增功能的测试要求

- 判定/计分类改动必须有单测（构造输入断言输出，跑在秒级）。
- 涉及 `/invoke` 契约、span 约定、领域包结构的改动：同步改样例 Agent 夹具与
  `agent-contracts` schema，并跑 `tests/test_imports.py` 与契约校验。
- 双语报告/界面的改动：zh/en 两侧都要有断言（现有测试对报告做文本包含检查，如"六维概览"）。
