"""HTTP 层：请求处理器、页面渲染与可粘贴的 markdown 块。"""
import html
import json
import os
import shlex
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler

from . import DEFAULT_TTL, MAX_TTL, MAX_VALUE, TOKENFILE, VERSION, public_base
from .runner import run_request
from .store import STORE

PAGE_CSS = """
:root{color-scheme:light dark}
body{font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
     margin:0;padding:24px;background:#fafafa;color:#111}
@media(prefers-color-scheme:dark){body{background:#111;color:#eee}code,pre{background:#222!important}
  input{background:#222;color:#eee;border-color:#444}}
main{max-width:720px;margin:0 auto}
h1{font-size:18px;margin:0 0 4px}
.sub{color:#888;font-size:13px;margin-bottom:20px}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}
td{padding:6px 8px;vertical-align:top;border-top:1px solid #ddd}
td:first-child{width:120px;color:#888;white-space:nowrap}
pre{background:#f0f0f0;padding:10px;border-radius:6px;overflow:auto;font-size:13px;margin:0}
label{display:block;margin:14px 0 4px;font-size:13px;color:#888}
input[type=password],input[type=text]{width:100%;box-sizing:border-box;padding:8px 10px;
     font-size:15px;border:1px solid #ccc;border-radius:6px}
button{padding:7px 16px;font-size:14px;border-radius:6px;border:1px solid #ccc;background:#fff;cursor:pointer}
button.primary{background:#2563eb;border-color:#2563eb;color:#fff}
button.danger{color:#b91c1c}
.row{display:flex;gap:10px;align-items:center;margin-top:18px}
.note{color:#888;font-size:12px;margin-top:14px}
.err{color:#b91c1c;font-weight:600}
"""


def page(title, body, extra_head=""):
    return (
        "<!doctype html><html lang=zh><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{PAGE_CSS}</style>{extra_head}</head>"
        f"<body><main>{body}</main></body></html>"
    )


def render_review(item):
    left = max(0, int(item["expires"] - time.time()))
    argv = shlex.join(item["argv"])
    risk = item.get("risk") or {"level": "low", "signals": [], "hosts": [], "score": 0}
    level = risk["level"]
    badge = {"low": ("低", "#16a34a"), "medium": ("中", "#f59e0b"), "high": ("高", "#dc2626")}[level]
    rows = [
        ("动作", "<code>" + html.escape(argv) + "</code>"),
        ("理由", html.escape(item["reason"] or "")),
        ("注入方式", "stdin（命令从标准输入读取）" if item["stdin"]
         else "环境变量 " + " ".join(f"<code>{html.escape(n)}</code>" for n in item["envs"])),
        ("有效期", f"{left} 秒后作废"),
    ]
    if risk.get("signals"):
        rows.append(("风险信号", "、".join(html.escape(x) for x in risk["signals"])))
    if risk.get("hosts"):
        rows.append(("目标主机", " ".join(f"<code>{html.escape(h)}</code>" for h in risk["hosts"])))
    table = "<table>" + "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows) + "</table>"

    if item["stdin"]:
        fields = ('<label>密码</label>'
                  '<input type=password name=value autocomplete=off required '
                  'autofocus data-1p-ignore data-lpignore=true data-bwignore>')
    else:
        fields = "".join(
            f'<label>{html.escape(n)}</label>'
            f'<input type=password name="v_{html.escape(n)}" autocomplete=off required '
            f'data-1p-ignore data-lpignore=true data-bwignore>'
            for n in item["envs"])

    if level == "medium":
        extra = ('<label style="display:flex;gap:8px;align-items:center;color:inherit">'
                 '<input type=checkbox name=ack value=1 required>'
                 '我已核对上面的命令与目标主机</label>')
    elif level == "high":
        extra = ('<label>高风险：请输入 RUN 确认</label>'
                 '<input name=confirm placeholder="RUN" autocomplete=off required>')
    else:
        extra = ""

    body = (
        f"<h1>请求执行命令</h1>"
        f"<div class=sub>风险 <span style='color:{badge[1]};font-weight:600'>{badge[0]}</span>"
        f"（score {risk.get('score', 0)}）</div>"
        f"{table}"
        f"<form method=post action='/r/{item['id']}'>"
        f"{fields}{extra}"
        "<div class=row><button class=primary name=decision value=run type=submit>注入并执行</button>"
        "<button class=danger name=decision value=deny type=submit formnovalidate>拒绝</button></div>"
        "</form>"
        "<div class=note>密码只存在服务内存里，执行完即丢弃；不写盘、不进日志、不进对话。"
        "输出中出现的值会被替换为 [secret:NAME]。</div>"
    )
    return page("请求执行命令", body)


def render_done(item):
    result = item["result"] or {}
    state = item["state"]
    if state == "denied":
        return page("已拒绝", "<h1>已拒绝</h1><div class=note>没有执行任何命令。</div>")
    if state == "expired":
        return page("已过期", "<h1>已过期</h1><div class=note>请求已作废，没有执行。</div>")
    if not result:
        return page("执行中", "<h1>执行中…</h1><div class=note>可以关闭此页，结果会回给 agent。</div>")
    out = html.escape(result.get("stdout") or "")
    err = html.escape(result.get("stderr") or "")
    body = (f"<h1>已执行</h1><div class=sub>退出码 {result.get('exit')} · "
            f"{'完成' if result.get('outcome') == 'exited' else result.get('outcome')}</div>"
            + (f"<label>stdout</label><pre>{out}</pre>" if out else "")
            + (f"<label>stderr</label><pre>{err}</pre>" if err else "")
            + "<div class=note>上面的输出里，密码已被替换为 [secret:NAME]。</div>")
    return page("已执行", body)


# ---------------------------------------------------------------- 可粘贴的渲染块

GUIDE_SHORT = (
    "拿到 url 后：① 立刻把下面的 render 文本原样贴进回复（它是 markdown，不是 html）；"
    "② 同一回合执行 sec wait --last --timeout 150 并回报 result.render；"
    "③ 绝不回显值、绝不要求用户把密码发到聊天里；④ 用户拒绝或过期就如实报告，不重试绕过；"
    "⑤ 要重跑就新建请求。"
)

GUIDE_FULL = """使用 sec 的硬性规则（防漂移）

1. 链接：拿到 url 后立刻贴 `render` 里的 markdown，不要拖到下一轮，不要只给 id。
2. 等待：同一回合执行 `sec wait --last --timeout 150`；超时返回 pending 时下一轮用 `sec status --last` 续查（id 记在 ~/.local/state/sec/last），不要重复创建请求。
3. 渲染：`render` 字段是纯 markdown（标题、代码块、链接），逐字保留——命令、链接、退出码、脱敏标记都不要改写；不要套进 ```html 围栏。
4. 秘密：绝不打印、回显、转述密码，也不要把 `[secret:NAME]` 还原成值；不要要求用户把密码发到聊天里。
5. 拒绝/过期：如实报告，不得改命令或换参数重试；需要重跑就新建请求并说明原因。
6. 一次执行：一个请求只执行一次；重复提交会被 410 拒绝，不要试图绕过。
7. 说明：结果里 `redacted` 列出的字段，要在回复里明确告诉用户"已脱敏"。
"""


def _block(lang, text):
    fence = "~~~"
    return f"{fence}{lang}\n{text.rstrip()}\n{fence}"


def risk_line(item):
    risk = item.get("risk") or {}
    level = {"low": "低", "medium": "中", "high": "高"}.get(risk.get("level", "low"), "低")
    parts = [f"**{level}**（score {risk.get('score', 0)}）"]
    if risk.get("signals"):
        parts.append("、".join(risk["signals"]))
    if risk.get("hosts"):
        parts.append("目标主机：" + "、".join(f"`{h}`" for h in risk["hosts"]))
    return " · ".join(parts)


def md_pending(item, url):
    left = max(0, int(item["expires"] - time.time()))
    inject = "stdin" if item["stdin"] else "env " + ", ".join(f"`{n}`" for n in item["envs"])
    return "\n".join([
        "### 🔐 sec · 等待确认执行",
        "",
        f"**{item['reason'] or '执行命令'}**",
        "",
        _block("sh", shlex.join(item["argv"])),
        f"- 注入：{inject}",
        f"- 风险：{risk_line(item)}",
        f"- 有效期：{left}s（成功后链接立即作废）",
        "- 密码只提交到该页面，不进对话、不发模型",
        "",
        f"**[打开并输入密码 →]({url})**",
    ])


def md_result(item):
    result = item["result"] or {}
    state = item["state"]
    cmd = _block("sh", shlex.join(item["argv"]))
    if state == "denied":
        return f"### ⛔ sec · 已拒绝\n\n{cmd}\n没有执行任何命令。"
    if state == "expired":
        return f"### ⌛ sec · 已过期\n\n{cmd}\n链接到期作废，命令没有执行。"
    if not result:
        return f"### ⏳ sec · 执行中\n\n{cmd}\n结果稍后返回。"
    ok = result.get("exit") == 0 and result.get("outcome") == "exited"
    redacted = result.get("redacted") or []
    meta = (f"`exit {result.get('exit')}` · `{result.get('outcome')}` · "
            f"`{result.get('duration_ms')}ms`")
    if redacted:
        meta += " · 已脱敏：" + ", ".join(f"`{n}`" for n in redacted)
    lines = [f"### {'✅' if ok else '⚠️'} sec · {'执行完成' if ok else '执行结束'}", "", meta, "", cmd]
    for label in ("stdout", "stderr"):
        text = result.get(label) or ""
        if text.strip():
            lines += ["", f"**{label}**", _block("text", text)]
    return "\n".join(lines)


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "sec/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("{} {}\n".format(time.strftime("%Y-%m-%dT%H:%M:%S"), fmt % args))

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _auth_ok(self):
        token = TOKENFILE.read_text().strip() if TOKENFILE.exists() else ""
        return bool(token) and self.headers.get("X-Sec-Token") == token

    def _base(self):
        return public_base(self.server.server_address[1])

    def _host_ok(self):
        host = self.headers.get("Host", "")
        if host.startswith(("127.0.0.1", "localhost")):
            return True
        base = urllib.parse.urlsplit(self._base())
        return host == base.netloc

    def _body(self, limit=MAX_VALUE * 2):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("请求体过大")
        return self.rfile.read(length).decode("utf-8", "replace")

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/healthz":
            return self._json(200, {"ok": True, "pid": os.getpid(), "version": VERSION})
        if not self._host_ok():
            return self._send(404, page("未找到", "<h1>404</h1>"))
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "r":
            item = STORE.get(parts[1])
            if not item:
                return self._send(404, page("无效链接", "<h1>无效或已失效的链接</h1>"))
            if item["state"] == "pending":
                return self._send(200, render_review(item))
            if item["state"] in ("denied", "expired"):
                return self._send(200, render_done(item))
            return self._send(200, render_done(item))
        return self._send(404, page("未找到", "<h1>404</h1>"))

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/request":
            if not self._auth_ok():
                return self._json(403, {"error": "forbidden"})
            try:
                payload = json.loads(self._body())
                argv = [str(a) for a in payload.get("argv") or []]
                if not argv:
                    raise ValueError("argv 不能为空")
                envs = [str(n) for n in payload.get("envs") or []]
                stdin_mode = bool(payload.get("stdin"))
                if not envs and not stdin_mode:
                    raise ValueError("必须指定 --env 或 --stdin")
                if envs and stdin_mode:
                    raise ValueError("--env 与 --stdin 不能同时使用")
                for name in envs:
                    if not name.replace("_", "").isalnum() or not name[0].isalpha():
                        raise ValueError(f"非法环境变量名：{name}")
                ttl = max(30, min(MAX_TTL, int(payload.get("ttl") or DEFAULT_TTL)))
                rid = STORE.new(argv, str(payload.get("reason") or ""), envs, stdin_mode, ttl)
            except Exception as exc:  # noqa: BLE001
                return self._json(400, {"error": str(exc)})
            item = STORE.get(rid)
            url = f"{self._base()}/r/{rid}"
            return self._json(200, {
                "id": rid, "url": url,
                "expires": int(item["expires"]), "ttl": ttl,
                "instructions": GUIDE_SHORT,
                "render": md_pending(item, url),
            })
        if path == "/api/pending":
            if not self._auth_ok():
                return self._json(403, {"error": "forbidden"})
            items = [{
                "id": i["id"], "argv": i["argv"], "reason": i["reason"],
                "expires": int(i["expires"]), "risk": i["risk"],
            } for i in STORE.pending()]
            return self._json(200, {"pending": items})
        if path == "/api/status":
            if not self._auth_ok():
                return self._json(403, {"error": "forbidden"})
            try:
                rid = str(json.loads(self._body()).get("id") or "")
            except Exception:  # noqa: BLE001
                return self._json(400, {"error": "bad body"})
            item = STORE.get(rid)
            if not item:
                return self._json(404, {"error": "not found"})
            url = f"{self._base()}/r/{rid}"
            if item["state"] == "pending":
                return self._json(200, {
                    "id": rid, "state": "pending", "expires": int(item["expires"]), "url": url,
                    "instructions": GUIDE_SHORT,
                    "render": md_pending(item, url),
                })
            return self._json(200, {
                "id": rid, "state": item["state"], "expires": int(item["expires"]),
                "result": item["result"] or {},
                "render": md_result(item),
            })
        parts = [p for p in path.split("/") if p]
        if len(parts) == 2 and parts[0] == "r":
            item = STORE.get(parts[1])
            if not item:
                return self._send(404, page("无效链接", "<h1>无效或已失效的链接</h1>"))
            if item["state"] != "pending" or item["used"]:
                return self._send(410, page("已作废", "<h1>这个链接已经用过了</h1>"))
            try:
                form = urllib.parse.parse_qs(self._body(), keep_blank_values=True)
            except Exception as exc:  # noqa: BLE001
                return self._send(400, page("格式错误", f"<h1>格式错误</h1><p>{html.escape(str(exc))}</p>"))
            decision = (form.get("decision") or [""])[0]
            item["used"] = True
            if decision != "run":
                item["state"] = "denied"
                return self._send(303, "", extra={"Location": f"/r/{item['id']}/done"})
            risk = item.get("risk") or {}
            if risk.get("level") == "medium" and (form.get("ack") or [""])[0] != "1":
                item["used"] = False
                return self._send(400, page("需要确认",
                    "<h1>请先勾选「我已核对命令」</h1><p>返回上一页重新提交。</p>"))
            if risk.get("level") == "high" and (form.get("confirm") or [""])[0].strip() != "RUN":
                item["used"] = False
                return self._send(400, page("需要确认",
                    "<h1>高风险命令：请在确认框输入 RUN</h1><p>返回上一页重新提交。</p>"))
            if item["stdin"]:
                value = (form.get("value") or [""])[0]
                if not value.strip():
                    item["used"] = False
                    return self._send(400, page("缺少密码", "<h1>密码为空</h1>"))
                values = {"VALUE": value}
            else:
                values = {}
                for name in item["envs"]:
                    value = (form.get("v_" + name) or [""])[0]
                    if not value.strip():
                        item["used"] = False
                        return self._send(400, page("缺少密码", f"<h1>{html.escape(name)} 为空</h1>"))
                    if len(value) > MAX_VALUE:
                        item["used"] = False
                        return self._send(413, page("过大", "<h1>值过长</h1>"))
                    values[name] = value
            item["state"] = "running"
            started = time.time()
            def work():
                result = run_request(item, values)
                result["duration_ms"] = int((time.time() - started) * 1000)
                item["result"] = result
                item["state"] = "ran"
            threading.Thread(target=work, daemon=True).start()
            return self._send(303, "", extra={"Location": f"/r/{item['id']}/done"})
        return self._json(404, {"error": "not found"})
