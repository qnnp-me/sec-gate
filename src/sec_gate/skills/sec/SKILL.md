---
name: sec
description: 通过一次性链接让用户在网页里输入凭据，命令在服务端执行，agent 只拿到脱敏后的输出。
---

# sec：一次性凭据注入

用 sec 处理"命令需要真实 API key / token / 密码，而值不在环境变量里"的场景：值只进网页表单，
命令在服务端执行，你只拿到把值替换成 `[secret:NAME]` 的输出。

## 什么时候用

- 命令必须读一个真实凭据，而对话里没有、也不该有它的明文；
- 用户愿意在一个一次性链接页面里自己输入值；
- 只需要"跑一次命令并看脱敏结果"，不需要长期保管秘密。

不要用它传普通配置或非秘密参数，也不要把它当密钥仓库长期保存。

## 流程（一条请求）

1. **请求**：MCP 入口调用 `request_secret`，CLI 入口执行 `sec request`；写清要执行的命令与理由。
2. **同回合贴链接**：拿到 `url` 后立刻把返回的 `render` markdown 原样贴进回复，不要只给 id，也不要拖到下一轮。
3. **等待**：同一回合调用 `secret_status`（`wait_seconds: 150`），或执行 `sec wait --last --timeout 150`。
   超时返回 pending 时，下一轮用 status 续查，不要重复创建请求。
4. **回报**：`render` 里的命令、退出码、脱敏标记逐字保留；`redacted` 列出的字段要明确告诉用户"已脱敏"。

一次请求只执行一次；重复提交会被服务拒绝（410），不得重试绕过。需要重跑就新建请求并说明原因。

## 回退顺序

1. 优先用 MCP：`request_secret` → `secret_status`。
2. MCP 不可用（工具缺失、`sec-mcp` 起不来、返回 `[Errno 2]`）→ 改用 CLI 完成同一个请求：

```sh
sec request --env DEMO --reason "为什么需要这个值" -- sh -c 'echo "len=${#DEMO}"'
sec wait --last --timeout 150
```

3. CLI 也不可用（`sec: command not found`）→ 先安装，再回到第 2 步：

```sh
python3 -m venv .venv
.venv/bin/pip install .
```

回退只换入口，不换规则：链接照样同回合贴，等待照样同回合做。

## 禁止事项

- 不向用户索要明文（"把 token 发给我"一律不行）；
- 不回显值，也不把 `[secret:NAME]` 还原成值；
- 不为了绕过拒绝、过期或 410 而改命令、换参数重试；
- 一个请求只执行一次，不并发提交同一请求；
- 不把值写进文件、日志、提交或任何持久化位置。