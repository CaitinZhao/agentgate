<div align="right">

[English](../en/sandbox.md) | 简体中文（本页）

</div>

本文档说明评测时被测 Agent 跑在哪里、平台与 Agent 的隔离边界是什么，以及 P1 沙箱
（平台托管执行环境 + 终态断言）的用法。

## 现状：Agent 自带运行环境，平台只观测

AgentGate 的评测模型是"**平台在门外观测**"：

- 被测 Agent 是一个**独立部署的服务**（自己的容器/进程/模型凭据），暴露 `/invoke` 接口。
  样例 Agent 由本仓库的 `Dockerfile.agent` / compose `jiuwen-agent` 服务提供。
- 平台对 Agent 只有三个动作：HTTP 调用 `/invoke`、在 :4318 接收它上报的轨迹、
  在 :8300 转发并录制它的 LLM 流量。**平台不进入 Agent 的进程空间，也不代持它的密钥。**
- 由此得到的评分是**栈级分数**（模型+提示词+工具+检索+策略整体），报告里记录 Agent 身份
  与组件版本，用于跨版本对比。

这个形态的边界：Agent 跑什么工具、访问什么数据，由 Agent 的部署方负责——评测平台
不为其兜底，因此**不要把样例 Agent 和平台暴露给不可信的调用方**（见部署文档安全须知）。

## 评测环境与真实环境要一致

评分规则里的"环境缺口与 Agent 过错分开归因"依赖环境声明：题库通过**领域包**声明
profile（如 fb 剖面需要财报语料）与材料来源；发起评测时平台与 Agent 做**能力握手**
（`/capabilities`），Agent 没有声明所需剖面时对应题目全部 SKIPPED（带原因），不会误判失败。
这保证了"环境缺数据"不会算成"Agent 做错"。

## P1 已落地：平台托管沙箱 + 终态断言

对 `state` 题型，平台现在可以**代管一个执行环境**：Agent 的终端命令在平台持有的沙箱里
执行，评测结束后平台直接检查**环境的终态**——不再依赖 Agent 自己在 `final_json` 里
申报结果。一个 `state` 题的写法（都在 `gold.final` 自由字典里，无 schema 变更）：

```json
"final": {
  "sandbox": {
    "image": "python:3.11-slim",
    "setup": [{"cmd": "mkdir -p /app/in /app/out"},
              {"write_file": {"path": "/app/in/task.txt", "content": "monthly data"}}]
  },
  "assertions": [
    {"read_file": "/app/out/report.json", "json_path": "status", "equals": "done"},
    {"exec": "test -f /app/out/task.bak", "exit_code": 0}
  ]
}
```

运行流程（`run_case_set` 内自动完成）：

1. 平台按 `sandbox` 规格创建沙箱并执行 `setup`（建目录、放输入文件）。
2. 把**执行端点与一次性 token** 注入该题的 `input.context`——Agent 用
   `POST /api/v1/sandbox/exec`（body `{"token", "cmd"}`）执行终端命令。
3. invoke 结束后，平台逐条求值 `assertions`（全部通过 = PASS；任一不过 = FAIL），
   然后销毁沙箱、注销 token。

判定语义（诚实分层）：

- 带环境断言的 state 题：沙箱可用 → 确定性判定；**沙箱不可用（provider 未配置/创建
  失败）→ PENDING**，绝不拿 Agent 的自报答案硬凑。
- 不带 `read_file`/`exec` 的旧式断言（对 Agent `final_json` 的路径比对）保持原语义。

### 配置与 Provider

`agentgate.json`：

```json
"sandbox": {
  "provider": "docker",            // off（默认，= 未启用）| docker | subprocess
  "image": "python:3.11-slim",     // 题目 sandbox.image 未指定时的默认镜像
  "network": "",                   // 可选：docker --network
  "exec_base_url": "",             // Agent 侧可达的平台地址；空 = http://127.0.0.1:8030
  "ttl_s": 1800                    // exec token 有效期
}
```

| Provider | 用途 | 隔离 |
|---|---|---|
| `docker`（生产推荐） | 每题一个容器，`docker exec` 执行 | 容器隔离 |
| `subprocess`（开发/演示） | 本机临时目录内跑 POSIX 命令（需要 bash） | **无隔离**，只用于可信环境 |
| `off` | 关闭：state 题回落 PENDING | — |

安全边界：exec 端点只暴露"按 token 执行命令"这一个动作；token 每沙箱随机、case 结束
即注销（另有 TTL 兜底），Agent 无法触及其他 case 的环境或平台其他资源。但沙箱内的
命令由被测 Agent 发起——**沙箱镜像里不要放密钥**，`network` 不必要时留空。

示例题库：`cases/sandbox-demo/cases.json`（文件读写 + 命令断言），测试：
`tests/test_sandbox.py`。

## 路线图（P1 之后）

- **平台材料服务**：由平台托管领域材料（如条款库）并随 `/invoke` 注入 context，
  替代当前"Agent 自带材料"的模式。
- **轨迹 provenance 补齐**：沙箱内每个工具执行的输入/输出回填 span 属性。
- **稳定性验证与沙箱的组合语义**（repeat_k>1 时环境复用还是每轮重置）。
