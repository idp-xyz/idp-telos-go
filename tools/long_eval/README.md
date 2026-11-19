# 长任务评测（idp-host 上下文管理提案第 4 节）

`docs/adr/idp-host-context-management-proposal.md` 第 1.1、4、5 节要的那个工具：在改 idp-host 的上下文管理**之前**量一次基线，**之后**用同一套办法再量一次。只用标准库。

它像成员一样驱动一个**已经在运行**的 compose 平台和真实模型：在 Workspace 里上传文件、向智能体提问、等运行结束、下载它交付的文件；然后从 HOST 自己的数据库里读出这次运行花了什么。

## 它量什么

每一轮（一次提问到它的运行结束）一行记录：

- 结局：状态、失败码、步数、墙钟分钟数；
- 每步的序列：输入 token、输出 token、耗时、结束原因（`tool_calls`、`end`、`length` …）、错误码；
- 输入、输出 token 合计；最大的一次输入，及其占窗口的比例；
- 工具调用按工具和状态的计数；
- 几类 CUSTOM 事件的计数：`model.retry`、`tool.denied`、`tool.interrupted`、`context.withheld`、`guard.flagged`、`file.delivered`（此外所有 `idp.host/*` 事件都按名字计数，将来的 `context.*` 会自动出现）；
- 交付的文件：名字、大小、sha256，存在结果目录下，并做**完整性检查**；
- 成功条件是否成立；
- **重复的工作**：同一次运行里，工具相同、规范化参数（键顺序、空白不计）也相同的调用，第二次起每次算一次重复；报告数量和占全部调用的比例。失败的调用也算：模型要求再做同一件事，不管结果如何。

### 它不量什么

- **文件好不好。** 完整性检查只证明文件"是它名字所说的东西"：`.xlsx`、`.docx`、`.pptx` 是能读开的 zip，且有主部件（`xl/workbook.xml`、`word/document.xml`、`ppt/presentation.xml`）；`.pdf` 以 `%PDF` 开头并至少有一个页标记；`.md` 非空且是 UTF-8。这是一个健全性检查，不是质量判断：报价表整理得对不对，要人来看。
- **统计显著性。** 每个用例只有三遍，不支持显著性检验，报告里也不做。报告给出中位数、范围和每一遍的值。模型只有一个本机模型，换一个模型或换一天，结果可能不同。
- 每步开始写回答之前的耗时、缓存命中比例、摘要触发次数：这些要等 idp-host 记录它们之后才有；已有的 `model.usage` 里只有整步耗时。
- `tool.interrupted`：HOST 现在没有这个事件，被中断的调用记成 FAILED 的工具调用，结果以 `interrupted:` 开头（idp-host D46）。所以记录里 `events.watched["tool.interrupted"]` 现在恒为 0，`events.interrupted_calls` 才是真数字。

## 怎么让平台跑起来

本工具**不启动也不停止**平台，只检查它在运行。用 `deploy/compose/e2e.sh` 的真实模型模式，并保留平台：

```bash
MODEL=live KEEP=1 IDP_LIVE_MODEL_KEY_FILE=.local/live-model.key bash deploy/compose/e2e.sh
```

（密钥由平台读取，写进 `.local/platform/`；本工具从不读它，也不会把它写进任何地方。）验收脚本会装好 `office-assistant` 和 Workspace 的应用 `office`（动作 `office.run`），本工具就用它们（`--app`、`--action`、`--workspace`、`--user` 可改）。它读 `.local/platform/clients.json` 里的测试用户 bob，从 Keycloak 取令牌，令牌过期前一分钟自动换新。

启动时它检查：Workspace 和 HOST 的 `/healthz`；HOST 数据库里 `office-assistant` 的模型和限额（模型是 `fake*` 就拒绝：假模型上的基线没有意义）；模型的上下文窗口（不带密钥问 `IDP_LIVE_MODEL_URL` 的 `/models` 和 `/props`；提供方要密钥就问不到，此时用 `--window N`，否则占窗口的比例不计算）；idp-host 等组件的版本和提交（`/version`、容器镜像 ID 与创建时间、各仓库检出的提交）。这些写进 `meta.json`。同一个 `--name` 续跑时如果容器的镜像变了，它拒绝继续（两个构建的结果不该混在一起），除非加 `--accept-changed-platform`。

## 命令

```bash
# 在已在运行的平台上跑；每个用例每遍都是一个新会话（一个本工具自己生成的 threadId，不受 Workspace 里保存的工作上下文影响）
python3 -m tools.long_eval run --cases .local/long-eval/cases.json --runs 3 --name baseline
python3 -m tools.long_eval run --cases .local/long-eval/cases.json --case L1 --case L5 --runs 1 --name try
#   --confirm            等待确认的工具调用自动确认（默认关：只记录，什么也不拒绝）
#   --confirm-wait 5     关着 --confirm 时，等确认多少分钟后放弃（运行不被取消，用例后面的轮记为 skipped）
#   --turn-timeout 60    一轮最长多少分钟；超时的运行被取消，记为 timeout（智能体自己的上限是 45 分钟）
#   --window N           提供方不报窗口时给出窗口

# 报告
python3 -m tools.long_eval report --name baseline                     # .local/long-eval/baseline/report.md
python3 -m tools.long_eval report --name after --against baseline     # 两者并排，差为 after 减 baseline

# 从旧平台的 pg_dump 算出同样的数字（只需要 Docker）
python3 -m tools.long_eval history --dump .local/backup-20261117-before-acceptance/db/idp_host.dump \
    --out .local/long-eval/history-20261117 --cases .local/long-eval/cases.json
python3 -m tools.long_eval report --name history-20261117 --agent office-assistant
```

`history` 把转储恢复进自己的临时 PostgreSQL 容器（`long-eval-history-<随机>`，无网络、不发布端口，用 `public.ecr.aws/docker/library/postgres:17-alpine`，**不拉取**镜像），用与实时评测同一条查询读每次运行，结束时删掉容器。用例当时不存在，所以按运行的第一条用户消息分组（`H01`、`H02` …）；给了 `--cases`，消息恰好等于某个单轮用例那句话的组就用该用例的编号和基线文字，这样旧记录和新测量能并排。转储里没有文件内容，所以不判断成功条件，只显示结局和"是否交付了文件"。窗口缺省 131,072（提案 1.1 节的数字，转储本身不含它，`meta.json` 里写明是假定的）。

## 一遍要花多少模型时间

按提案：L1 约 27 分钟（84 步），整套 L1 到 L6 一遍约 2 到 3 小时；三遍约 6 到 9 小时。L2 有三轮，L0 曾因 30 步上限失败。一轮最长 60 分钟（`--turn-timeout`）。本机模型只有一个槽位，评测期间平台上别的回答会变慢；评测中途别用同一个模型做别的事，否则耗时不可比。

## 结果在哪里

`.local/long-eval/<name>/`（`.local` 已被 git 忽略）：

- `results.jsonl`：一轮一行，**这一轮一结束就写**。同一个 `--name` 再跑一次就续上，已有的 `用例|遍|轮` 跳过；一遍中间停了，从没做完的那一轮接着做，沿用该会话的 threadId。
- `meta.json`：模型、窗口、智能体的限额、各组件版本、参数、续跑记录。
- `files/<用例>-r<遍>-t<轮>/`：交付的文件。
- `report.md`。

**用例文件和业务文件留在 `.local/long-eval/`**（`cases.json`、`files/`），成员的原话也在里面；它们、结果、下载的文件都不进仓库。用例格式见 `cases.py` 的文档字符串：`turns` 里每轮有 `say` 和可选的 `attach`（相对用例文件的路径）；`success` 是 `{"delivered": [".xlsx"]}`（整个用例交付过该种文件，且最后一轮完成）或 `{"delivered_each_turn": [".xlsx"]}`（每一轮都完成并交付）。

## 测试

```bash
python3 -m unittest discover -s tools/tests     # 不需要 Docker、平台或模型
```

## 它依赖 e2e.sh 的哪些地方（改了这些，这里会坏）

- Workspace：`POST /v1/workspaces/us-ops/files?name=`（201，返回 `id`）、`POST …/actions/invoke`（201，`result.id`、`result.threadId`，请求体的顶层 `threadId`）、`GET …/runs/{id}`（`status`）、`GET …/runs/{id}/tool-calls`（`toolCallId`、`state: PENDING_CONFIRMATION`）、`POST …/tool-calls/{id}/decide`、`POST …/runs/{id}/cancel`、`GET …/files?source=run:{id}` 和 `…/files/{id}/content`。
- Keycloak：`https://127.0.0.1:7443/realms/idp`，客户端 `e2e-cli`，密码授权，`.local/platform/clients.json` 的 `users` 和 `e2e-cli` 密钥，`.local/platform/tls/ca.crt`。
- 端口 8560（Workspace）、8450（HOST）、8550（CMI）；compose 项目名 `idp-telos-platform`，服务名 `postgres` 和 `host`；库 `idp_host`，用户 `postgres`。
- `office-assistant`、应用 `office`、动作 `office.run`，输入 `{message, files}`。
- HOST 的表：`runs`、`run_steps`、`tool_calls`、`run_events`（`type='CUSTOM'`，`payload->>'name'` 为 `idp.host/…`）、`thread_messages`（`message->>'content'`，`run_id`）、`agents.definition`。
