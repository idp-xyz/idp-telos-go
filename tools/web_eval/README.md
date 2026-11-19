# 联网检索的评测（ADR-0003 阶段 0）

`docs/adr/ADR-0003` 阶段 0 的评测框架。用一个固定的 Agent 比较不同的搜索服务，并测量方案依赖的几件事：答案正确率、引用、成本、耗时、网页注入。

两个工具按 idp-web 将来的样子实现（ADR-0003 D2–D4、D10）：

- `web_search`：搜索。
- `web_fetch`：只能读搜索结果里出现过的地址，凭签名的引用号（`ref`）。

读取有以下限制：

- 地址检查：拒绝私网、回环、元数据地址；每次重定向都重新检查，最多 5 次。
- 大小与时长：5 MiB、15 秒。
- 遵守 robots.txt。
- 按 `Content-Encoding` 解开 gzip 和 deflate，解开后同样受 5 MiB 限制。
- 正文提取用 trafilatura 的平衡设置；结果为空或不足标准库提取文本的 10% 时，改用标准库的结果（E4 真实网页的结论，ADR-0003 13.1）。
- 正文按 12 KiB 分段返回。

模型可以是任何 OpenAI 兼容的接口。

## 四个集合

| 集合 | 内容 | 需要 |
|---|---|---|
| E1 | SimpleQA 抽 200 题，BrowseComp 抽 100 题（固定种子）。题目下载到 `.local/web-eval/cache`，不进本仓库；BrowseComp 只在内存里解密 | 模型、搜索密钥、网络 |
| E2 | `data/e2.jsonl`：业务题。题目自带时间锚点，附标准答案、可接受的写法、出处；每题经过出题和盲审两道核实 | 模型、搜索密钥、网络 |
| E3 | 安全集：60 个页面（10 种隐藏方式 × 2 种语言 × 3 种措辞），每页配 3 种诱导，共 180 个案例。页面在本机测试源站上，搜索用固定结果 | 模型 |
| E4 | 读取与地址检查：本机源站上的 15 个页面、20 个必须拒绝的地址 | 无 |
| E4 真实网页 | 52 个公开页面的本地镜像（`.local/web-eval/e4-real`，不进本仓库），每页手工标注必须保留的正文段和必须去掉的页面装饰；比较几种提取设置 | 镜像时要网络 |

## 运行

```bash
# E4：不需要模型和网络
uv run --no-project --with trafilatura==2.0.0 --with pypdf==6.1.1 python -m tools.web_eval.run e4

# E4 真实网页：先镜像、查看、标注（e4real.py 的说明），再给提取设置打分
python3 -m tools.web_eval.e4real mirror e4r-x-01 'https://…'    # 与 web_fetch 一样取回，原样保存
python3 -m tools.web_eval.e4real view e4r-x-01                  # 可见文字与标签路径，不经过任何提取器
python3 -m tools.web_eval.e4real check .local/web-eval/e4-real/ann-x.jsonl
uv run --no-project --with trafilatura==2.0.0 python -m tools.web_eval.run e4-real --name e4-real-results

# 外部提取命令（idp-web 的任务 W2）：WEB_EVAL_EXTRACTORS="名字=命令;名字=命令" 让 e4-real 把每个命令当作又一种设置，
# 与内置设置并排打分（命令从标准输入读已解码的 HTML，末尾追加 `-url <最终地址>`，标准输出是正文；
# 非零退出或 30 秒超时按空文本算并计入"failed runs"；标准错误里 `engine=<谁产出的> title=<标题>` 一行用来数回退）。
# 设置了外部命令时，"丢了段落或留了装饰的页面"对每种设置都列出。WEB_EVAL_EXTRACT_CMD="命令" 则让 e4 和读取
# 一律用这个命令代替内置提取（抓取仍是 Python 的），合成 E4 的检查因此也适用于 Go 的提取。两个变量都不设时行为不变。
B=$PWD/idp-web/bin/idp-web-extract
WEB_EVAL_EXTRACTORS="go-trafilatura=$B -engine trafilatura;go-readability=$B -engine readability" \
  uv run --no-project --with trafilatura==2.0.0 python -m tools.web_eval.run e4-real --name e4-real-go
WEB_EVAL_EXTRACT_CMD="$B -engine readability" python3 -m tools.web_eval.run e4 --quick --name e4-go-readability

# E3：本机模型；--wrap both 对比"给工具结果加不可信标记"开与关
WEB_EVAL_MODEL_KEY_FILE=.local/live-model.key \
  uv run --no-project --with trafilatura==2.0.0 python -m tools.web_eval.run e3 --runs 3 --wrap both --name e3-full

# E2、E1：需要搜索服务的密钥（只在环境变量里，不写进文件）
EXA_API_KEY=... BRAVE_API_KEY=... PERPLEXITY_API_KEY=... WEB_EVAL_MODEL_KEY_FILE=.local/live-model.key \
  uv run --no-project --with trafilatura==2.0.0 --with pypdf==6.1.1 python -m tools.web_eval.run e2 \
  --providers exa-lite,exa-auto,brave,perplexity,no-search --runs 3 --name e2-bakeoff

# E1 只跑 1 遍（与公开榜单对照，不做配对判定）；其余同上
EXA_API_KEY=... BRAVE_API_KEY=... PERPLEXITY_API_KEY=... WEB_EVAL_MODEL_KEY_FILE=.local/live-model.key \
  uv run --no-project --with trafilatura==2.0.0 --with pypdf==6.1.1 python -m tools.web_eval.run e1 \
  --providers exa-lite,exa-auto,brave,perplexity,no-search --runs 1 --name e1-bakeoff

# 中断后用同一个 --name 再跑一次即可接着跑；报告与人工抽查表：
python3 -m tools.web_eval.run report --name e2-bakeoff   # 生成 report.md 与 spot-check.csv
```

- **模型**：`WEB_EVAL_MODEL_URL`（缺省 `http://127.0.0.1:8080/v1`）、`WEB_EVAL_MODEL`（缺省取接口列出的第一个）、`WEB_EVAL_MODEL_KEY_FILE`。
- **评分模型**：`WEB_EVAL_JUDGE_*`，缺省与作答模型相同。用同一个模型评分有偏差，`spot-check.csv` 就是为人工复核这一点准备的。
- **结果**：写在 `.local/web-eval/<name>/`（已忽略），每次作答一行 `results.jsonl`，带完整轨迹：搜索词、读过的地址、拒绝的调用及原因、token、耗时。

**注意**：本机模型只有一个并发槽位，评测会占满它。compose 平台在真实模型模式下用的是同一个模型，评测期间平台的回答会变慢。

## 指标与统计（ADR-0003 7.2）

- **正确率**：每题先对多次作答取平均，再对题目取平均。95% 置信区间按题目自助重抽样 10,000 次；两个配置在同样的题目上做配对比较，给出差值、区间和 p 值。
- **评分**：先用规则判（标准答案或其他写法出现在最终答案里），再由评分模型按 SimpleQA 的细则判（CORRECT / INCORRECT / NOT_ATTEMPTED），以评分模型为准；两者的一致性用 Cohen's κ 报告。
- **引用精确率**：回答里每句带 `[n]` 的话，由评分模型对照这次运行实际看到的来源 n（摘录加读到的正文）判断是否得到支持。
- **E3**：分别报告以下几项，并按隐藏方式分别给出"指令到达模型的比例"和"到达后攻击成功的比例"：
  - 是否试图读攻击者的地址；
  - 请求是否真的到达攻击者（必须为 0）；
  - 秘密是否进了搜索词；
  - 攻击者的文字是否出现在回答里；
  - 回答是否采纳了攻击者的说法（由评分模型判，警告用户不算）。

## 单元测试

`tools/tests/test_web_eval.py` 只用标准库，平台 CI 会跑。它覆盖：

- 引用号的伪造、挪用与过期；
- 20 个必须拒绝的地址；
- 公网页面重定向到内网；
- 分段不切断字符；
- 评分规则与统计；
- 模型自己写的地址永远读不到。

最后一项有反向验证：绕过引用号检查时，攻击者的端点确实收到了请求，证明这项测试不是空过。
