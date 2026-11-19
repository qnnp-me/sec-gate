# sec-gate

一次性凭据注入：你在网页里输入值，命令在服务端执行，agent 只拿到脱敏后的输出。

## 定位

`sec` 是一个零运行期依赖（仅标准库）的凭据输入工具，代码在 `src/sec_gate/` 包内。agent 需要真实凭据去执行命令时，由用户在一个
一次性链接的网页里输入，命令在服务端执行，agent 只拿到**脱敏后的输出**。

目标：

- 凭据不进入对话、不进入模型上下文、不落盘；
- 用户能看到将被执行的**确切命令**，并按一次确认；
- 一条请求只执行一次、限时有效；
- 输出中出现的值自动替换为 `[secret:NAME]`。

## 根本限制（请先读这一节）

> 只要执行凭据的进程与 agent 处于同一 uid/容器，agent 就能读到它。
> 因此"值不进 agent 领域"只有一种实现：**服务与执行都在 agent 无法访问的边界之外**（宿主或独立容器），容器内只留不含秘密的瘦客户端。

在这一边界之外本工具提供不了任何保证：同一 uid 下的 agent 可以改代码、起假服务、
读服务进程的内存与环境变量。只有在服务本身跑在 agent 无法到达的宿主或独立容器里时，
"值不进 agent 领域"才成立。

## 非目标

- 不是通用密钥管理系统（不是 Vault / Infisical 的替代）；
- 不做多租户（当前假设单用户）；
- 不防"同一 uid 下被攻破的 agent 主动读取内存/文件"（见上一节）；
- 不做命令 allowlist：命令能不能跑由用户在页面上人工审阅决定，工具只做风险分级与二次确认。

## 快速开始

要求 Python 3.11+，只依赖标准库，从**本地目录**安装（本项目不发 PyPI / Release，命令里不写裸包名）。
装到当前 venv，会得到 `sec` / `sec-mcp` 两个入口：

```sh
python3 -m venv .venv
.venv/bin/pip install .
```

也可以把入口装进用户级路径（本机未安装 pipx / uv，这两条未验证，CI 也未覆盖）：

```sh
pipx install .
uv tool install .
```

不安装时也可以直接从源码运行：

```sh
PYTHONPATH=src python3 -m sec_gate request --env DEMO --ttl 120 -- sh -c 'echo "len=${#DEMO}"'
```

创建请求（不阻塞，立即打印 `id` 与 `url`）：

```sh
sec request --env DEMO --ttl 120 --reason "演示：只打印长度" -- sh -c 'echo "len=${#DEMO}"'
```

把打印出的 `url` 发给用户，用户在同一回合内打开网页、输入值、确认执行：

```sh
sec wait --last --timeout 150    # 等结果，stdout/stderr 里的值已替换为 [secret:DEMO]
```

其他子命令：`sec status <id>`、`sec render <id>`（只输出可粘贴的 markdown）、`sec list`、
`sec guide`（使用规则原文）、`sec stop`。服务不是常驻的：第一次调用时自动拉起并 detach，
端口在 30000–30999 里随机选。

MCP 包装 `sec-mcp` 用 stdio JSON-RPC 暴露 `request_secret` / `secret_status` /
`secret_guide`，内部只调用 `sec` CLI，不复制服务端逻辑；`sec` 不在 PATH 上时用
`SEC_BIN=/path/to/sec` 指定。

## 注册到 Multica（`sec setup`）

`sec setup` 把 MCP 入口与 skill 注册到当前 Multica workspace 的库，**默认直接执行**，
`--dry-run` 不写 workspace：只在本机临时目录生成归档（供打印的两条命令直接引用），
并打印这两条命令与归档路径：

```sh
sec setup --dry-run    # 预览：两条命令 + 归档路径
sec setup              # 执行注册
```

它做两件事：

1. MCP 条目：`multica workspace mcp add sec --server-config '{"command":"<sec-mcp 绝对路径>","args":[]}'`。
   JSON 由 setup 按 `shutil.which("sec-mcp")` 填入；找不到时回落裸名 `sec-mcp`。
2. skill：把包内唯一的 `skills/sec/SKILL.md` 打成临时归档（内层 `sec/SKILL.md`），执行
   `multica skill import --file <归档> --on-conflict overwrite`。只有服务端拒绝该归档形态时，
   才回退 `multica skill create/update --content-file`。

`multica` 只从 `PATH` 解析，**不读** `MULTICA_CLI_PATH`。两种降级都不算失败（退出码 0）：

- `multica workspace mcp list` 本身失败 → 只打印两条手工命令，不调用 `workspace mcp add`
  也不调用 `skill import`；
- `list` 正常但 `workspace mcp add` 被拒（agent 无权写 workspace MCP 库，退出码 3；`--debug` 下
  服务端报文是 `agents cannot modify the workspace MCP servers`）→ 打印手工命令，skill 照常写入。

在 agent 运行时里执行 `sec setup` 一般落在上面第二个分支：skill 会写进 workspace，MCP 条目
需要你在**自己的终端**里复制打印出来的 `multica workspace mcp add ...` 执行一次。

注册只写 workspace 库，**不分配给任何 agent**。需要时手动分配（`server-id` 取自
`multica workspace mcp list`，`skill-id` 取自 `multica skill list`）：

```sh
multica agent mcp add <agent-id> <server-id>
multica agent skills add <agent-id> --skill-ids <skill-id>
```

回滚：

```sh
multica skill delete <id> --yes          # 无 --yes 在无 TTY 环境会 Aborted. 且退出码为 0
multica workspace mcp remove sec         # agent 无权执行，需人工在自己的终端跑
```

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `SEC_HOME` | `~/.local/state/sec` | 状态目录：`port`、`sec.pid`、`api.token`、`last`、`sec.log` |
| `SEC_PORT` | 未设（随机） | 固定服务端口，取值范围受 `SEC_PORT_MIN` / `SEC_PORT_MAX` 限制（默认 30000–30999） |
| `SEC_PUBLIC_BASE` | 未设 | 页面 URL 的对外前缀。**未设时 url 回落到 `http://127.0.0.1:<port>/r/<id>`**，只有本机能打开 |
| `SEC_TTL` | `300` | 请求默认有效期（秒），单条请求可设 30–1800 |
| `SEC_CMD_TIMEOUT` | `120` | 命令执行超时（秒） |

### `SEC_PUBLIC_BASE`

把服务放到反向代理后面时，必须设置对外地址，否则用户拿到的链接打不开：

```sh
SEC_PUBLIC_BASE=https://sec.example.com sec request ...   # → https://sec.example.com/r/<id>
```

这个变量由**服务进程**读取，url 也在服务进程里计算。因此它必须设在拉起服务的那次调用上；
对一个已经在跑的实例，改客户端的环境变量不起作用——要改就 `sec stop` 后用新值重新拉起。
代理需要保留 `Host` 头，并且 TLS 应在代理终止。

## 安全模型（摘要）

- 192-bit 一次性 id（`secrets.token_urlsafe(24)`），GET 从不返回值，只接受表单 POST；
- 单次执行，重复提交返回 `410`；TTL 到期自动作废；
- 值只进环境变量或 stdin，**不进 argv**，不写盘、不写日志；
- 输出脱敏：任意长度的值都会被替换，长值优先匹配；
- 中风险（score 30–59）要勾选确认，高风险（≥60）要输入 `RUN`；
- Host 校验、页面安全头（`no-store` / `no-referrer` / `DENY` / 严格 CSP）。

已知未解决项（同一信任域、无 allowlist、无 `mlock`、无审计、无速率限制等）见
`docs/DESIGN.md` 的「已知问题」一节。

## 开发

```sh
python3 -m venv .venv
.venv/bin/pip install ruff==0.17.5

.venv/bin/ruff check .                        # 全量 lint（含 src）
./scripts/privacy_scan.sh                     # 隐私守卫，命中即失败
PYTHONPATH=src python3 -m unittest discover -s tests -v
.venv/bin/pip install .                       # 冒烟用装好的入口
SEC_BIN=.venv/bin/sec ./scripts/smoke.sh      # 端到端冒烟，用隔离 SEC_HOME + 独立端口
```

`scripts/smoke.sh` 不会碰任何已有实例：它用 `mktemp -d` 当 `SEC_HOME`、随机空闲端口，
结束时 `sec stop` 并删掉临时目录；`SEC_BIN` 指定要测的可执行文件（默认 PATH 上的 `sec`）。

## 许可证

MIT，见 `LICENSE`。