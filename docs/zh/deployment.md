<div align="right">

[English](../en/deployment.md) | 简体中文（本页）

</div>

# 环境准备与一键部署

> 读者假设：**没接触过本仓库、机器上啥都没有**。按顺序照做即可完成部署。
> 读完这篇你会知道：要装哪些软件、为什么要装、每一步在干什么、失败了看什么日志。
>
> 两种部署方式，按你的场景选：
> - **方式 B · 服务器直接拉取（GitHub 流程，推荐）**：服务器上 `git clone` + `docker compose`，
>   全程不需要另一台"部署机"（见第 4 节）。
> - **方式 A · 一键远程部署**：在自己的电脑上跑脚本，通过 SSH 遥控服务器（适合批量/内网，
>   见第 5 节；脚本内**不含任何凭据**，目标服务器用环境变量传入）。

## 0. 先搞懂：你要部署的是什么

评测环境由 **两个 Docker 容器** 组成，跑在同一台云服务器上：

| 容器 | 端口 | 作用 |
|---|---|---|
| `agentgate-web`（评测平台） | 8030 | **Web 界面**：管题库、发起评测、看报告。你在浏览器里用的就是它 |
| 同上（容器内另开两个端口） | 4318 | **OTLP 接收器**：接收被测 Agent 上报的执行轨迹（span） |
| 同上 | 8300 | **消息级录制代理**：Agent 的每次大模型调用经它转发给真实网关并留痕（轨迹分析、成本维依赖它） |
| `jiuwen-agent`（被测 Agent 样例，可选） | 8200 | 一个真实的 ReAct Agent（含财报检索工具集），用来被评测；你也可以评自己的 Agent |

另有**一个数据卷**（方式 B 是 `agentgate/data`，方式 A 是服务器上的
`/opt/agentgate-platform/data`）：账号、题库、评测历史、报告全在这里，
**升级重装都不丢**（除非你手动删除）。

**凭据有三种，各管各的事，默认互不共用**：

| 凭据 | 谁在用 | 什么时候需要 | 放在哪 |
|---|---|---|---|
| ① 被测 Agent 的模型凭据 | **被测 Agent**（它要思考） | 部署样例 Agent 时（它离了模型起不来）；评自己的 Agent 则是它自己的配置 | `agentgate/tests/fixtures/.env`（不入 git） |
| ② 录制代理上游（网关地址+Key） | 平台的**录制代理**（把 Agent 流量转发回真实网关并留痕） | 只在勾选"消息级录制"的 run 实际发生时用到 | 平台设置页 `proxy_upstream`；部署后随时可配 |
| ③ AI 增强凭据 | **平台自己**的辅助功能（AI 摘要 / judge 建议 / 起草） | 用到才配，**默认关闭** | 用户中心 → AI 增强（按用户各自配） |

**平台部署本身零凭据**。发起评测时也不需要配任何 Key。
②和③语义不同：②是"被测系统花的钱"，③是"平台辅助功能花的钱"，可以相同也可以不同。

```
浏览器 ──► :8030 评测平台 ──► 被测 Agent ──► :8300 录制代理 ──► 真实 LLM 网关
                ▲    :4318 ◄── 轨迹上报 ──────┘
                └── 数据卷（题库/历史/报告）
```

## 1. 仓库与代码（两个 GitHub 仓库，克隆后并排放）

```bash
git clone https://github.com/<org>/agent-contracts.git
git clone https://github.com/<org>/agentgate.git
ls
# agent-contracts/  agentgate/     ← 必须并排（compose 构建平台镜像时要用到两个目录）
```

- **agent-contracts**：轨迹/评测结果的共享 schema 契约包（平台镜像构建时安装）。
- **agentgate**：评测平台本体 + 测试夹具（含样例 Agent）+ 部署脚本。
- 仓库里**不会有的东西**（首次克隆后需要你补齐，都有文档）：
  样例 Agent 的模型凭据（第 2 步）、FinanceBench PDF 语料（只影响 fb 剖面题目，见
  `agentgate/deploy/fb_pdfs/README.md`）、第三方数据集改编的题库（见
  `agentgate/cases/README.md` 的重新生成命令；自带的 `example`/`injection`
  两个题库开箱即用）。

## 2. 被测 Agent 的模型凭据（仅样例 Agent 需要）

```bash
cd agentgate
cat > tests/fixtures/.env <<'EOF'
LLM_BASE_URL=https://你的网关地址/v1
LLM_API_KEY=sk-xxxxxxxx
LLM_MODEL_NAME=GLM5.3-Flash
EOF
```

- 必须是 **OpenAI 兼容** 接口（`/chat/completions`）；该文件已被 .gitignore 排除。
- 评自己的 Agent：跳过这步——把你的 Agent 接入 compose 网络或给平台一个可达地址即可，
  它用你自己的模型配置。
- 录制代理上游（②）与 AI 增强（③）都不在这里配，部署后在界面配（见第 6 节）。

## 3. 准备服务器（一台全新的 Linux 云主机）

唯一必装：**Docker**（外加 git 拉代码）。

```bash
git --version   || (apt-get install -y git || yum install -y git)
docker version  || curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
df -h /opt                                              # ≥5GB 可用
docker pull hello-world                                 # 出网检查（离线见第 7 节）
```

**专用账号与权限**：建一个部署专用账号（示例 `agent`）并加入 docker 组；有 sudo 的
root 等价账号也可直接用（实测 openEuler 22.03 的 `agent` 账号 uid=0）：

```bash
useradd -m -s /bin/bash agent && passwd agent
usermod -aG wheel agent       # openEuler/RHEL 用 wheel 组；Debian/Ubuntu 用 sudo 组
usermod -aG docker agent      # 免 sudo 跑 docker（重新登录生效）
```

预检一行（应全部通过再进部署）：`sudo -n true; docker ps; df -h /opt; ss -tlnp | grep -E ':(8030|4318|8300|8200) '`

**安全组放行**：`8030`（Web UI，对使用者开放）；`4318/8300` 仅跨机 Agent 时需要；
`8200`（样例 Agent）**不建议对公网开放**（无鉴权）。所有端口可用 compose/部署参数改
（见第 6 节），改了记得同步安全组。

## 4. 方式 B · 服务器直接拉取部署（GitHub 流程，推荐）

仍在服务器上，`agent-contracts/` 与 `agentgate/` 并排的目录里：

```bash
cd agentgate

# ① 创建 Agent 凭据（第 2 步；评自己的 Agent 则跳过）

# ② 一键起平台（首次构建约 5-10 分钟：镜像内自建前端，服务器不需要 Node）
OWNER_PASSWORD='你的owner初始密码' \
AGENTGATE_PROXY_UPSTREAM='https://你的网关/v1' \
docker compose up -d --build

# ③ （可选）顺带起样例 Agent
docker compose --profile agent up -d --build

# ④ 体检
curl -s http://127.0.0.1:8030/api/v1/health       # {"status":"ok",...}
docker compose logs agentgate | grep -i password  # 首次初始化打印的 owner 密码
```

- 两个环境变量写入容器环境；`OWNER_PASSWORD` 只在数据卷首次初始化时生效，
  忘了可 `docker exec agentgate-web agentgate passwd owner` 重设。
- 样例 Agent 起来后，平台「发起评测」目标填 `http://jiuwen-agent:8200`
  （compose 网内域名）；消息级录制默认开启，代理地址已自动指向 `http://agentgate:8300/v1`。
- 升级：`git pull && docker compose up -d --build`（数据卷持久；平台重启会把中断的
  run 自动重排队）。

## 5. 方式 A · 一键远程部署（有部署机时）

部署机（你的电脑）需要：Python 3.9+、`pip install -r agentgate/deploy/requirements.txt`
（paramiko）、Node.js（可选）。**服务器地址/账号/密码一律走环境变量，仓库内不含任何凭据**：

```bash
export AGENTGATE_DEPLOY_HOST=1.2.3.4 AGENTGATE_DEPLOY_USER=root AGENTGATE_DEPLOY_PASSWORD='...'
python agentgate/deploy/deploy_all.py                       # Agent + 平台 + 体检
python agentgate/deploy/deploy_all.py --skip-agent          # 只更平台
python agentgate/deploy/deploy_all.py --skip-build          # 不重建镜像，仅重启
```

脚本五步：本地预检（paramiko/凭据文件/环境变量）→ 远程预检（docker/磁盘/端口）→
部署 Agent（打包上传约 124MB，断线自动重试 3 次）→ 部署平台 → 四端点体检。
若目标服务器要跑样例 Agent 但 `tests/fixtures/.env` 没配，预检会明确报错并给指引。

### 5.1 全量流水线：部署 → 六库灌入 → agentdojo 全量 → 体检

部署只是第一步。完整跑通"六个开源 benchmark + agentdojo 全量"的四段式（示例以
8.92.9.37 为例；命令均在部署机执行）：

```bash
export AGENTGATE_DEPLOY_HOST=8.92.9.37 AGENTGATE_DEPLOY_USER=agent AGENTGATE_DEPLOY_PASSWORD='...'
cd agentgate/deploy

# ① 一键部署（被测 Agent + 平台 + 体检；含打包上传与镜像构建，10-25 分钟）
python deploy_all.py --owner-password <owner 初始密码>

# ② 六库灌入：建库 + 导题 + 资产（bfcl/spider/gaia/airbench/agentdojo/harmbench）
python import_banks.py                       # 子集用 --banks agentdojo,gaia

# ③ agentdojo 全量排队（391 题；目标 = 服务器上的样例 Agent）
T=$(curl -s -X POST http://$AGENTGATE_DEPLOY_HOST:8030/api/v1/auth/login      -H 'Content-Type: application/json'      -d '{"username":"qa-robot","password":"qa-robot-pass-1"}'      | python -c 'import json,sys;print(json.load(sys.stdin)["token"])')
curl -s -X POST http://$AGENTGATE_DEPLOY_HOST:8030/api/v1/runs      -H "Authorization: Bearer $T" -H 'Content-Type: application/json'      -d '{"task_name":"agentdojo 全量","banks":[{"bank":"agentdojo","levels":[]}],           "target_url":"http://127.0.0.1:8200","proxy_enabled":true,"ai_assist":false}'

# ④ 体检与进度（在服务器上执行 127.0.0.1 版本亦可）
curl -s http://$AGENTGATE_DEPLOY_HOST:8030/api/v1/health     # 平台
curl -s http://$AGENTGATE_DEPLOY_HOST:8030/api/v1/runs       # 队列与结果（Web UI 同源）
```

前置条件与说明：② 依赖部署机本地已生成六个题库文件（`cases/*/cases.jsonl`，生成方式见
[公共题库](public-banks.md)）；③ 的 agentdojo 剖面由 agent 容器内 pip 安装的官方包提供
（一键部署已带上）；agentdojo 由原生口径判定，`ai_assist` 关闭即可；全量 391 题耗时与
模型速度线性相关——先用 `case_ids` 抽 5 题估成本再全量。

## 6. 端口与配置：在哪改什么

| 想改的东西 | 默认 | 在哪改 | 什么时候生效 |
|---|---|---|---|
| 平台 Web/API 端口 | 8030 | compose `ports` 或部署参数 `--web-port` | 重跑部署 |
| OTLP 接收器端口 | 4318 | compose `ports` / 部署参数 `--receiver-port`（env 每次启动覆盖设置表） | 重跑部署 |
| 录制代理端口 | 8300 | 同上 `--proxy-port` | 重跑部署 |
| 被测 Agent 端口 | 8200 | compose `ports` / `--agent-port` | 重跑部署 |
| 真实网关地址（录制代理上游） | 空 | 平台设置页 `proxy_upstream`（owner） | 保存即时生效 |
| agent 侧录制地址 proxy_agent_url | 方式 B 自动指向 `http://agentgate:8300/v1` | 平台设置页 | 下一次 run 生效 |
| 报告保留天数 / 开放注册 | 30 天 / 开 | 平台设置页 | 即时 |
| 数据目录、默认题库（CLI 侧） | — | 工作目录 `agentgate.json` | CLI 下次启动 |

## 7. 离线部署（服务器不能出公网）

有网侧构建镜像（**CPU 架构必须与服务器一致**），导出后离线侧 load + 启动：

```bash
# 有网侧（仓库根，agent-contracts 与 agentgate 并排）：
docker build -f agentgate/Dockerfile.web  -t agentgate-platform:0.1 .
cd agentgate && docker build -f Dockerfile.agent -t jiuwen-agent:0.1 . && cd ..
docker save agentgate-platform:0.1 | gzip > platform.tar.gz
docker save jiuwen-agent:0.1        | gzip > agent.tar.gz
scp *.tar.gz user@离线服务器:/opt/

# 离线侧：
docker load < /opt/platform.tar.gz && docker load < /opt/agent.tar.gz
docker run -d --name agentgate-web --network host -v /opt/agentgate-platform/data:/app/data \
  -e AGENTGATE_PROXY_UPSTREAM='https://内网网关/v1' agentgate-platform:0.1
```

## 8. 日志：出了问题看什么、在哪、长什么样

| 日志 | 位置 | 里面有什么 | 正常长什么样 |
|---|---|---|---|
| 平台容器日志 | `docker logs agentgate-web`（`--tail 200`） | 启动 banner、HTTP 访问、worker 异常栈 | `[agentgate] platform ready \| data: /app/data \| receiver :4318 \| proxy :8300`；重启后出现 `re-queued N run(s) interrupted by restart` 属正常恢复 |
| Agent 容器日志 | `docker logs jiuwen-agent` | uvicorn 访问日志、Agent 侧报错 | `POST /invoke HTTP/1.1" 200 OK` |
| 单次 run 失败原因 | Web 执行详情页红色 error 框（即 runs 表 error 字段） | 直接原因，如 `FileNotFoundError("context_file not resolvable...")` | 成功的 run 不出现 |
| 每次模型调用的留痕 | 数据卷 `llm_proxy_sink/current.jsonl` | 一行一条 JSON：ts/model/request/response/usage | 勾选消息级录制的 run 才有 |
| 单次 run 的完整产物 | 数据卷 `results/<run_id>/` | report.md（六维+诊断）、scores.json、judge.jsonl、spans_raw.json | 执行详情页"工件"页签可下载 |

常用命令：`docker ps`（两容器 Up）、`docker compose logs -f agentgate`、
`docker restart agentgate-web`、`curl -s http://127.0.0.1:8030/api/v1/health`。

## 9. 常见问题（现象 → 原因 → 怎么办）

| 现象 | 原因 | 处理 |
|---|---|---|
| 浏览器打不开 8030 | 安全组没放行 / 容器没起 / 端口改过 | 放行端口；`docker ps` + `docker logs agentgate-web`；确认实际端口 |
| run 里题目全是 SKIPPED | 能力握手失败：库要求 profile=fb 而 Agent `/capabilities` 没声明 | `curl -s http://127.0.0.1:8200/capabilities` 对照题库 requirements |
| 勾了消息级录制但成本维 n/a | proxy_upstream 未配 / Agent 不支持 llm_base_url | 设置页配 ②；`/capabilities` 里 `llm_base_url_supported` 应为 true |
| fb 剖面题目检索结果为空 | PDF 语料没挂载（仓库不含语料） | 按 `deploy/fb_pdfs/README.md` 下载后重启 Agent 容器 |
| locomo/longmem 题库不存在 | 派生题库不入 git | 见 `cases/README.md` 重新生成（需先下载对应数据集） |
| run 一直"运行中" | 重启杀了执行进程 | 新版自动重排队；手工：`docker exec agentgate-web python -c "from agentgate.webapp import db; db.requeue_interrupted_runs()"` |
| 部署时端口被占 | 上次残留进程 | 方式 A 脚本自动按端口精确清理；手动 `ss -tlnp \| grep :8300` 后 kill（勿 `pkill -f`） |
| 方式 A 报"缺少部署目标环境变量" | `AGENTGATE_DEPLOY_*` 未设置 | 按报错示例 export 三个变量（凭据不进仓库） |
| 报告/轨迹看着空 | 没勾消息级录制（默认勾选） | 重新发起并保持勾选；可在用户中心配置 AI 生成摘要 |
| 忘记 owner 密码 | — | `docker exec agentgate-web agentgate passwd owner`（唯一改密途径） |

## 10. 代码与数据的边界（归档/上传前必读）

| 位置 | 属于 | 去向 |
|---|---|---|
| 两个 git 仓库的工作树 | 代码/文档/自带题库 | 提交 GitHub（.gitignore 已排除运行时产物） |
| 本地 `agentgate/data/`（跑平台产生的） | 运行时数据 | **不入 git**；需要归档单独 tar，别混进代码 |
| 服务器 `/opt/agentgate-platform/data`（方式 A 数据卷） | 生产数据（账号/题库/run/报告） | **不上传 GitHub**；归档：`tar -czf data-$(date +%F).tar.gz -C /opt/agentgate-platform data`（含用户数据，私有保存） |
| `deploy/*.tar.gz`、截图、日志 | 本地中间产物 | 已 gitignore |
| 部署 bundle | 只打包代码+自带题库+文档 | 天然不含 run 结果（结果只存在于数据卷） |

清理旧评测数据：设置页 `report_retention_days` 自动清理结果目录；手动删单个 run 产物：
`rm -rf data/results/<run_id>`；彻底重置（删号删库，慎用）：
方式 B `docker compose --profile agent down && rm -rf agentgate/data`；
方式 A `rm -rf /opt/agentgate-platform/data`。

## 11. 安全须知

- 样例 Agent（:8200）**无鉴权**，不要暴露公网；平台（:8030）暴露公网时建议加反代/VPN。
- 部署目标服务器凭据只走环境变量（`AGENTGATE_DEPLOY_*`），仓库与文档中不存在任何真实凭据。
- autoadapt 的"数据集 URL"是**服务端发起的下载**，公网部署时仅建议可信用户使用
  （编辑页默认 admin/owner 才能进）。
- 平台 API 的 CORS 默认只放行本地开发端口；跨域部署用 `AGENTGATE_CORS_ORIGINS` 显式配置。
- 模型凭据（①②③）只存服务器文件/数据库，不出现在代码、报告或下发给 Agent 的内容中。

## 12. 升级 / 回滚 / 卸载

- **升级**：`git pull` + 重跑 compose build（方式 B）或 deploy 脚本（方式 A）。数据卷持久；
  中断的 run 自动重排队。
- **回滚**：切回旧版本代码重新构建即可，数据卷与镜像解耦。
- **卸载（销毁全部数据）**：
  方式 B：`docker compose --profile agent down && rm -rf agentgate/data`；
  方式 A：`docker rm -f agentgate-web jiuwen-agent && rm -rf /opt/agentgate-platform /opt/agentgate`。

英文版：[deployment-en.md](../en/deployment.md)。

部署完成后，从 [demo 演示](demo.md) 开始熟悉平台；返回 [架构总览](architecture.md)。
