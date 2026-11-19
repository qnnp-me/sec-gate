# 设计说明

本文档描述 `sec` 的架构、接口契约、安全模型与已知问题。它是实现说明，不是承诺：
未在此列出的控制项就是没实现。

## 1. 定位

单文件密码/凭据输入工具：agent 需要真实凭据执行命令时，由用户在一个一次性链接的网页里
输入值，命令在服务端执行，agent 只拿到脱敏后的输出。

### 目标

- 凭据不进入对话、不进入模型上下文、不落盘；
- 用户能看到将被执行的确切命令并按一次确认；
- 一条请求只执行一次、限时有效；
- 输出中出现的值自动替换为 `[secret:NAME]`。

### 非目标

- 不是通用密钥管理系统（不是 Vault / Infisical 的替代）；
- 不做多租户；
- 不防"同一 uid 下被攻破的 agent 主动读取内存/文件"（见第 4 节）；
- 不做命令 allowlist，命令由用户人工审阅。

## 2. 架构

```
浏览器 ──HTTPS──> 反向代理 ──HTTP──> sec serve（本机端口）
   ▲                                     │ 内存 session + 命令执行
   │ 一次性链接 /r/<id>                    ▼
   └────────────────────────────── 目标命令（env 或 stdin 注入值）
agent ──CLI/MCP──> sec（客户端，自动拉起 serve）──> 127.0.0.1:<port>/api/*
```

- 服务不是常驻的：第一次调用时 `ensure_server()` 用 `flock` + 二次检查 + 随机端口拉起并
  detach（`start_new_session`），pidfile 记在状态目录；
- 端口在 `SEC_PORT_MIN`–`SEC_PORT_MAX`（默认 30000–30999）随机，写入 `state/port`，
  客户端靠它定位；`SEC_PORT` 可固定端口；
- 反向代理按 `<SEC_PUBLIC_BASE>` 对外暴露，TLS 在代理终止。

## 3. 接口契约

### 3.1 CLI

```
sec request [--env NAME]... [--stdin] [--ttl 秒] [--reason 文本] -- <命令> [参数...]
sec wait [<id>] [--last] [--timeout 150]
sec status [<id>] [--last]
sec render [<id>] [--last]        # 只输出可粘贴的 markdown
sec list                          # 待处理请求（JSON）
sec guide                         # 使用规则原文
sec stop                          # 停服务（下次自动再起）
sec serve [--port N]              # 内部用
```

`request` 返回 JSON `{id, url, expires, ttl, instructions, render}`，并把 id 写入
`state/last`。

### 3.2 HTTP

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/healthz` | 无 | `{ok,pid,version}` |
| GET | `/r/<id>` | id 即能力 | 评审页 / 结果页；含风险等级、完整 argv、目标主机 |
| POST | `/r/<id>` | id 即能力 | `decision=run\|deny` + `v_<NAME>`/`value`；中风险需 `ack=1`，高风险需 `confirm=RUN` |
| POST | `/api/request` | `X-Sec-Token` | 创建请求 |
| POST | `/api/status` | 同上 | 查询状态与结果 |
| POST | `/api/pending` | 同上 | 列出待处理请求 |

错误码：`400` 参数缺失/缺确认、`403` token、`404` 未知 id、`410` 已使用、`413` 值超 16KB。

### 3.3 页面安全头

`Cache-Control: no-store`、`Referrer-Policy: no-referrer`、`X-Frame-Options: DENY`、
`X-Content-Type-Options: nosniff`、
`CSP: default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'`。

### 3.4 MCP（stdio，JSON-RPC 2.0）

工具：`request_secret(program, args?, envs?, stdin?, reason, ttl?)`、
`secret_status(id, wait_seconds?)`、`secret_guide()`。

返回永远是 `{content: [{type: "text", text: "<JSON + render>"}], isError}`，不含明文。
实现方式是子进程调用 `sec` CLI（可用 `SEC_BIN` 覆盖路径），不复用服务端代码，避免两套
逻辑漂移。

## 4. 安全模型

### 4.1 根本限制

> 只要执行凭据的进程与 agent 处于同一 uid/容器，agent 就能读到它。
> 因此"值不进 agent 领域"只有一种实现：**服务与执行都在 agent 无法访问的边界之外**（宿主或独立容器），容器内只留不含秘密的瘦客户端。

把 `sec serve` 跑在与 agent 相同的 uid/容器里时，本工具的所有控制都可以被绕过：
agent 能改代码、起假服务、读 `/proc/<pid>/environ`。第 4.2 节的清单只对"服务在边界之外、
客户端是瘦客户端"的部署成立。

### 4.2 已实现

| 控制 | 实现位置 |
|---|---|
| 一次性 192-bit id（`secrets.token_urlsafe(24)`） | `Store.new` |
| TTL 30–1800s，默认 300 | `MAX_TTL` / 请求参数 |
| 单次执行（重放 410） | `do_POST` |
| GET 从不返回值 | 所有 GET 分支 |
| 值只进 env 或 stdin，不进 argv | `run_request` |
| 输出脱敏（任意长度，长值优先） | `redact` |
| 人工确认（中风险勾选、高风险输入 `RUN`） | `render_review` + `do_POST` |
| Host 校验（公开域名或 loopback） | `_host_ok` |
| 日志不含请求体 | `log_message` 只记方法与路径 |

### 4.3 风险评分

`risk_profile` 是静态、本地计算，只影响"要几次确认"，不影响能否执行：

| 信号 | 分值 |
|---|---|
| 网络工具（curl/wget/nc/ssh/rsync…） | +40 |
| 编码/变换（base64/xxd/openssl/iconv/gpg…） | +30 |
| shell 拼接（`sh -c` 内含 `;` `\|` `$(` `&&` `>`） | +25 |
| 敏感路径（`~/.ssh`、`/etc/`、`~/.config`、`/proc/`…） | +20 |
| 非惯用程序 | +15 |
| 命令过长（>40 token） | +10 |

低 <30 · 中 30–59 · 高 ≥60。

### 4.4 已知问题

| 级别 | 问题 | 方向 |
|---|---|---|
| P0 | 工具与被防护对象同一信任域 | 把服务与执行移到宿主或独立容器，容器内只留瘦客户端 |
| P0 | 无 allowlist，靠人工审命令 | 不做 allowlist（会毁掉通用性）；缓解 = 风险分级 + 目标主机展示 + 高危二次确认。残余风险：`base64` 等变换可绕过脱敏外带 |
| P1 | 内存无 `mlock` | 值可能被换出到磁盘；Python 无法真正擦除。方向：Go/Rust 重写或 `ctypes` mlock |
| P1 | 无审计 | 只有访问日志；缺"谁在何时批准了什么"。建议 append-only `audit.log`：id/argv/决定/退出码，不含值 |
| P1 | 日志含能力 id | `sec.log` 记录完整 `/r/<id>` 路径；建议只记前缀或哈希 |
| P1 | 凭据可能落盘 | 取决于凭据来源（浏览器密码管理器、CLI session 等）；需要按来源单独处理 |
| P2 | 无速率限制 | 仅 `MAX_PENDING=8`；对外应加 per-IP 限流 |
| P2 | TTL 偏长 | 生产建议 120s，并限制同时只有一个活跃请求 |
| P2 | 服务监听 `0.0.0.0` | 同网络的其他容器可直连；应按代理地址做防火墙或改造为 unix socket |
| P2 | `/healthz` 公开 | 暴露版本与 pid |
| P2 | 值来源无 TTL | 根治方向：动态短命凭据（Vault / Infisical / 1Password SA 等），泄露后损失可控 |

## 5. 目录

| 路径 | 内容 |
|---|---|
| `sec` | 服务端 + CLI，单文件、仅标准库 |
| `sec-mcp` | MCP stdio 薄包装，子进程调用 `sec` |
| `tests/test_privacy.py` | 隐私守卫自测（标准库 unittest） |
| `scripts/privacy_scan.sh` | 隐私守卫：扫描真实主机名/域名/路径/邮箱/用户名 |
| `scripts/smoke.sh` | 端到端冒烟：隔离 `SEC_HOME` + 独立端口 |
| `.github/workflows/ci.yml` | ruff（pin 0.17.5）+ 隐私守卫 + 单测 + 冒烟 |

## 6. 演进方向

当前是两个单文件原型，行为已由 `scripts/smoke.sh` 与 `tests/` 兜住。后续按独立可验收的
阶段演进：

1. **拆包**：`store / runner / risk / http / cli / mcp` 六个模块，行为不变，拆包前后同一组
   端到端输出一致；存量 lint 告警清零并纳入 CI。
2. **接入**：MCP 注册、skill、一条命令完成安装；MCP 不可用时回落到 CLI。
3. **部署与凭据**：宿主/独立容器部署、凭据来源、审计与限流——每条都是独立决策，需要单独
   确认，不混在代码重构里做。