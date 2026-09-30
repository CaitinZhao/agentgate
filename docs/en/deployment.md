<div align="right">

[简体中文](../zh/deployment.md) | English（this page）

</div>

# Deployment Guide (from zero)

> Assumes you have **never touched this repo and the machines are empty**. Two deployment
> modes — pick one:
> - **Mode B · clone on the server (GitHub flow, recommended)**: `git clone` + `docker compose`
>   on the server itself; no separate deploy machine (section 4).
> - **Mode A · one-click remote deploy**: run the script from your laptop; it drives the server
>   over SSH (section 5). The scripts contain **no credentials** — the target server is passed
>   via environment variables.

## 0. What you are deploying

Two Docker containers on one cloud server, plus one data volume:

| Container | Port | Purpose |
|---|---|---|
| `agentgate-web` (the platform) | 8030 | **Web UI**: manage banks, launch runs, read reports |
| same container | 4318 | **OTLP receiver**: collects execution traces from the agent |
| same container | 8300 | **Recording proxy**: forwards agent LLM calls to the real gateway and records them (trajectory analysis / cost dimension depend on it) |
| `jiuwen-agent` (sample agent, optional) | 8200 | a real ReAct agent with financial retrieval tools — or bring your own agent |

Data volume (`agentgate/data` for mode B, `/opt/agentgate-platform/data` for mode A):
accounts, banks, run history, reports — survives upgrades.

**Three different credentials, each serving its own consumer — not shared by default:**

| Credential | Used by | When needed | Where |
|---|---|---|---|
| ① Agent model credentials | the **agent under test** (it has to think) | deploying the sample agent; your own agent brings its own config | `agentgate/tests/fixtures/.env` (not in git) |
| ② Recording-proxy upstream | the platform's **recording proxy** | only for runs with message-level recording | Platform Settings `proxy_upstream`; editable anytime |
| ③ AI-enhancement credentials | the **platform's own** assist features | opt-in, **off by default** | User Center -> AI enhancements (per user) |

**Deploying the platform requires no credentials at all**, and neither does launching a run.
② and ③ are semantically different (money spent by the system under test vs by the
platform's assist features); they never depend on each other.

```
browser ──► :8030 platform ──► agent ──► :8300 recording proxy ──► real LLM gateway
                ▲   :4318 ◄── trace export ─────┘
                └── data volume (banks / history / reports)
```

## 1. Repositories (clone both, side by side)

```bash
git clone https://github.com/<org>/agent-contracts.git
git clone https://github.com/<org>/agentgate.git
# both must sit next to each other (compose builds the platform image from both)
```

- **agent-contracts**: the shared schema package for traces / eval results (installed into
  the platform image at build time).
- **agentgate**: the platform itself + test fixtures (incl. the sample agent) + deploy scripts.

Not in the repo (fill in after cloning, all documented): the sample agent's model
credentials (step 2), the FinanceBench PDF corpus (only affects fb-profile cases — see
`agentgate/deploy/fb_pdfs/README.md`), dataset-derived banks (regeneration commands in
`agentgate/cases/README.md`; the self-contained `example` / `injection` banks work out of
the box).

## 2. The agent's model credentials (sample agent only)

```bash
cd agentgate
cat > tests/fixtures/.env <<'EOF'
LLM_BASE_URL=https://your-gateway/v1
LLM_API_KEY=sk-xxxxxxxx
LLM_MODEL_NAME=GLM5.3-Flash
EOF
```

OpenAI-compatible (`/chat/completions`) only; gitignored. Evaluating your own agent:
skip this — wire it into the compose network and point the platform at it. The recording
upstream (②) and AI-enhancement credentials (③) are configured in the UI afterwards.

## 3. Prepare the server

Only Docker (+ git) is mandatory:

```bash
git --version   || (apt-get install -y git || yum install -y git)
docker version  || curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
df -h /opt                          # >= 5 GB free
docker pull hello-world             # egress check (offline: section 7)
```

**Dedicated account and permissions**: create a deploy-only account (example `agent`)
and add it to the docker group; a sudo/root-equivalent account works as-is (the
`agent` account on an openEuler 22.03 host was uid=0 in practice):

```bash
useradd -m -s /bin/bash agent && passwd agent
usermod -aG wheel agent       # wheel on openEuler/RHEL; the sudo group on Debian/Ubuntu
usermod -aG docker agent      # run docker without sudo (re-login to apply)
```

One-line preflight (all should pass before deploying): `sudo -n true; docker ps; df -h /opt; ss -tlnp | grep -E ':(8030|4318|8300|8200) '`

Security group: open **8030** (Web UI); 4318/8300 only for cross-machine agents; keep
**8200 off the public internet** (the sample agent has no auth). All ports are
configurable (section 6) — update the security group when you change them.

## 4. Mode B · clone on the server (GitHub flow, recommended)

With both repos side by side:

```bash
cd agentgate
# 1) agent credentials (step 2; skip when evaluating your own agent)
# 2) platform up — the image builds the frontend itself (no Node needed on the server)
OWNER_PASSWORD='your-owner-password' AGENTGATE_PROXY_UPSTREAM='https://your-gateway/v1' \
  docker compose up -d --build
# 3) optional sample agent
docker compose --profile agent up -d --build
# 4) verify
curl -s http://127.0.0.1:8030/api/v1/health       # {"status":"ok",...}
docker compose logs agentgate | grep -i password  # owner password printed on first init
```

- `OWNER_PASSWORD` only bootstraps an empty data volume; recovery:
  `docker exec agentgate-web agentgate passwd owner`.
- Launch runs against `http://jiuwen-agent:8200` (compose network name); message-level
  recording defaults on with the proxy address pre-wired to `http://agentgate:8300/v1`.
- Upgrade: `git pull && docker compose up -d --build` (data persists; interrupted runs are
  re-queued automatically).

## 5. Mode A · one-click remote deploy (with a deploy machine)

Laptop needs Python 3.9+, `pip install -r agentgate/deploy/requirements.txt` (paramiko).
**Server credentials come from the environment — nothing is committed:**

```bash
export AGENTGATE_DEPLOY_HOST=1.2.3.4 AGENTGATE_DEPLOY_USER=root AGENTGATE_DEPLOY_PASSWORD='...'
python agentgate/deploy/deploy_all.py                     # agent + platform + verify
python agentgate/deploy/deploy_all.py --skip-agent        # platform only
python agentgate/deploy/deploy_all.py --skip-build        # restart without rebuild
```

Stages: local preflight (paramiko / credential file / env vars) -> remote preflight
(docker / disk / busy ports) -> agent deploy (124MB bundle upload, 3 retries) -> platform
deploy -> four-endpoint health check. If the sample agent is included but
`tests/fixtures/.env` is missing, the preflight fails with clear instructions.

### 5.1 Full pipeline: deploy -> import six banks -> full agentdojo -> health

Deploying is only step one. The four-stage pipeline for "six open benchmarks + the full
agentdojo run" (example host 8.92.9.37; commands run on the deploy machine):

```bash
export AGENTGATE_DEPLOY_HOST=8.92.9.37 AGENTGATE_DEPLOY_USER=agent AGENTGATE_DEPLOY_PASSWORD='...'
cd agentgate/deploy

# 1) one-click deploy (target agent + platform + health check; 10-25 min)
python deploy_all.py --owner-password <owner initial password>

# 2) import the six banks (create + cases + assets: bfcl/spider/gaia/airbench/agentdojo/harmbench)
python import_banks.py                       # subset: --banks agentdojo,gaia

# 3) queue the full agentdojo run (391 cases; target = the sample agent on the server)
T=$(curl -s -X POST http://$AGENTGATE_DEPLOY_HOST:8030/api/v1/auth/login      -H 'Content-Type: application/json'      -d '{"username":"qa-robot","password":"qa-robot-pass-1"}'      | python -c 'import json,sys;print(json.load(sys.stdin)["token"])')
curl -s -X POST http://$AGENTGATE_DEPLOY_HOST:8030/api/v1/runs      -H "Authorization: Bearer $T" -H 'Content-Type: application/json'      -d '{"task_name":"agentdojo full","banks":[{"bank":"agentdojo","levels":[]}],           "target_url":"http://127.0.0.1:8200","proxy_enabled":true,"ai_assist":false}'

# 4) health & progress (127.0.0.1 works too when run on the server)
curl -s http://$AGENTGATE_DEPLOY_HOST:8030/api/v1/health     # platform
curl -s http://$AGENTGATE_DEPLOY_HOST:8030/api/v1/runs       # queue & results (same source as the Web UI)
```

Prerequisites: step 2 needs the six generated case files on the deploy machine
(`cases/*/cases.jsonl`, see [Public banks](public-banks.md)); the agentdojo profile in
step 3 comes from the official package pip-installed inside the agent container (the
one-click deploy ships it); agentdojo is judged by its native reading, so `ai_assist`
can stay off; the 391-case run scales with model speed — sample 5 cases via `case_ids`
first.

## 6. Ports and configuration: what to change where

| What | Default | Where | Effective |
|---|---|---|---|
| Platform Web/API port | 8030 | compose `ports` / `--web-port` | re-deploy |
| OTLP receiver port | 4318 | compose `ports` / `--receiver-port` (env overrides the settings table every boot) | re-deploy |
| Recording proxy port | 8300 | compose `ports` / `--proxy-port` | re-deploy |
| Agent port | 8200 | compose `ports` / `--agent-port` | re-deploy |
| Real gateway URL (proxy upstream) | empty | Platform Settings `proxy_upstream` (owner) | immediate |
| proxy_agent_url | mode B pre-wires `http://agentgate:8300/v1` | Platform Settings | next run |
| Retention / registration | 30 days / open | Platform Settings | immediate |
| Data dir, default bank (CLI side) | — | `agentgate.json` in the working dir | next CLI process |

### Can the data directory be configured?

Two layers, configured differently:

| Layer | Default | How to configure |
|---|---|---|
| In-container (/app/data, what the platform reads/writes) | /app/data | AGENTGATE_DATA_DIR env, `agentgate web --data-dir`, or `data_dir` in agentgate.json (mode B: edit the compose mount) |
| Host mount directory (mode A deploy scripts) | /opt/agentgate-platform/data | deploy_all.py / deploy_platform.py --data-dir <path>, or env AGENTGATE_DEPLOY_DATA_DIR; import_banks.py --data-dir (the in-container path is auto-detected; override with --container-data-dir) |

The data directory is fixed **at deploy time** — the running platform binds it on
startup, so it is not editable in the web UI; owner-only settings show the actual path
read-only. After changing the host directory, re-run the deploy; old data is not
migrated automatically (stop the container, copy, restart with the new --data-dir).


## 7. Offline deployment (server without internet)

Build images on an internet-connected machine (**same CPU architecture** as the server),
export, load, then start without building:

```bash
# online side (both repos side by side):
docker build -f agentgate/Dockerfile.web  -t agentgate-platform:0.1 .
cd agentgate && docker build -f Dockerfile.agent -t jiuwen-agent:0.1 . && cd ..
docker save agentgate-platform:0.1 | gzip > platform.tar.gz
docker save jiuwen-agent:0.1        | gzip > agent.tar.gz
scp *.tar.gz user@offline-server:/opt/
# offline server:
docker load < /opt/platform.tar.gz && docker load < /opt/agent.tar.gz
docker run -d --name agentgate-web --network host -v /opt/agentgate-platform/data:/app/data \
  -e AGENTGATE_PROXY_UPSTREAM='https://internal-gateway/v1' agentgate-platform:0.1
```

## 8. Logs: what to look at and where

| Log | Where | Contents | Healthy looks like |
|---|---|---|---|
| Platform container | `docker logs agentgate-web` | startup banner, HTTP access, worker tracebacks | `[agentgate] platform ready \| data: /app/data \| receiver :4318 \| proxy :8300`; `re-queued N run(s) interrupted by restart` after restarts is normal |
| Agent container | `docker logs jiuwen-agent` | uvicorn access log, agent-side errors | `POST /invoke HTTP/1.1" 200 OK` |
| Why a run failed | run detail red error box (runs.error) | the direct cause | absent on successful runs |
| Per-model-call records | volume `llm_proxy_sink/current.jsonl` | one JSON per recorded call | present for recorded runs |
| Run artifacts | volume `results/<run_id>/` | report.md, scores.json, judge.jsonl, spans_raw.json | downloadable from the run detail "Artifacts" tab |

Useful: `docker ps`, `docker compose logs -f agentgate`, `docker restart agentgate-web`,
`curl -s http://127.0.0.1:8030/api/v1/health`.

## 9. Troubleshooting (symptom -> cause -> action)

| Symptom | Cause | Action |
|---|---|---|

| Agent container Exited(1), log shows `from profile_prompts import ... ModuleNotFoundError` | deploy_eval.py's embedded JIUWEN_DOCKERFILE drifted from the repo's Dockerfile.agent — the new fixture file was not COPYed | add `COPY agentgate/tests/fixtures/profile_prompts.py /app/profile_prompts.py` to BOTH Dockerfiles and redeploy the agent |
| agentdojo sample run all PENDING, answers.jsonl says `agentdojo environment error: No module named 'agentdojo'` | the agent container lacks the agentdojo package (the /capabilities list is hardcoded, so the handshake still passes — a declared profile is not an installed dependency) | add `agentdojo==0.1.35` to JIUWEN_REQUIREMENTS in deploy_eval.py and rebuild; verify by an actual invoke, not the handshake |
| import_banks.py db-import fails with a truncated traceback | docker exec only sees volume paths: the script used the host's /opt/agentgate-platform/data and host /tmp as container paths | stage under the volume (host /opt/.../data/tmp-import/<bank> = container /app/data/tmp-import/<bank>) and pass --db as the container path /app/data/banks/public/<bank>/cases.db; rerun the command manually once for an untruncated stderr |
| the 124MB SFTP upload drops mid-transfer (`Server connection dropped`) | long-haul link flakiness | `for i in 1 2 3; do python deploy_eval.py --skip-run && break; sleep 15; done` (script stages retry 3 times internally too) || 8030 unreachable | security group / container down / port changed | open the port; `docker ps` + `docker logs agentgate-web`; confirm the actual port |
| all cases SKIPPED | handshake: the bank requires a profile the agent does not declare | compare `/capabilities` with the bank requirements; rebuild the agent or edit the bank |
| recording on but cost n/a | proxy_upstream unset, or the agent ignores llm_base_url | configure ② in Platform Settings; `/capabilities` should show `llm_base_url_supported: true` |
| fb-profile cases return empty retrieval | PDF corpus not mounted | see `deploy/fb_pdfs/README.md`, then restart the agent container |
| locomo/longmem banks missing | dataset-derived banks are not in git | regenerate per `cases/README.md` (download the datasets first) |
| run stuck "running" | a restart killed the worker | new builds re-queue automatically; manual: `docker exec agentgate-web python -c "from agentgate.webapp import db; db.requeue_interrupted_runs()"` |
| port busy during deploy | stale host process | mode A kills precisely by port; manual: `ss -tlnp \| grep :8300` then kill that pid (never `pkill -f`) |
| mode A: "missing deploy target env vars" | `AGENTGATE_DEPLOY_*` unset | export the three variables (credentials never enter the repo) |
| empty-looking report | recording unchecked (on by default) | re-run with recording on; configure AI in User Center for summaries |
| forgot owner password | — | `docker exec agentgate-web agentgate passwd owner` |

## 10. Code vs data boundary (read before archiving/uploading)

| Location | Belongs to | Destination |
|---|---|---|
| The two git working trees | code / docs / self-contained banks | GitHub (the gitignore excludes runtime artifacts) |
| Local `agentgate/data/` (created by running the platform) | runtime data | **never into git**; tar separately if needed |
| Server `/opt/agentgate-platform/data` (mode A volume) | production data (accounts/banks/runs/reports) | **never to GitHub**; archive: `tar -czf data-$(date +%F).tar.gz -C /opt/agentgate-platform data` (private) |
| `deploy/*.tar.gz`, screenshots, logs | local artifacts | gitignored |
| Deploy bundles | code + self-contained banks + docs only | never contain run results (results live only in the data volume) |

Cleaning old eval data: the platform sweeps result dirs after `report_retention_days`;
per-run artifacts: `rm -rf data/results/<run_id>`; full reset (destroys accounts too):
mode B `docker compose --profile agent down && rm -rf agentgate/data`; mode A
`rm -rf /opt/agentgate-platform/data`.

## 11. Security notes

- The sample agent (:8200) has **no auth** — keep it off the public internet; put the
  platform (:8030) behind a reverse proxy / VPN when public.
- Deploy-target server credentials live only in `AGENTGATE_DEPLOY_*` env vars; no real
  credentials exist anywhere in the repos or docs.
- autoadapt's "dataset URL" is a **server-side fetch** — for public deployments restrict
  it to trusted users (the edit page is admin/owner only by default).
- CORS defaults to local dev origins only; configure `AGENTGATE_CORS_ORIGINS` explicitly.
- Model credentials (①②③) live server-side only; they never appear in code, reports, or
  anything delivered to the agent.

## 12. Upgrade / rollback / uninstall

- **Upgrade**: `git pull` + rebuild (mode B) or re-run the deploy script (mode A). Data
  persists; bank schema upgrades migrate on first load; interrupted runs re-queue.
- **Rollback**: check out the old code and rebuild — the data volume is decoupled from images.
- **Uninstall (destroys all data)**: mode B `docker compose --profile agent down &&
  rm -rf agentgate/data`; mode A `docker rm -f agentgate-web jiuwen-agent &&
  rm -rf /opt/agentgate-platform /opt/agentgate`.

Chinese version: [deployment.md](./deployment.md).
