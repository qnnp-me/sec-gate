"""MCP stdio 工具：把 sec 包成薄客户端，不碰任何秘密。

工具：
  request_secret  需要真实凭据时调用；返回 {id, url, render}
  secret_status   查询/等待结果；返回脱敏后的输出
  secret_guide    返回使用规则原文

约定：永远不返回任何秘密值，只返回脱敏后的 render 文本。
"""
import json
import os
import shutil
import subprocess
import sys

from . import __version__

SEC = os.environ.get("SEC_BIN") or shutil.which("sec") or "sec"
VERSION = __version__


def run_sec_raw(argv, timeout):
    try:
        proc = subprocess.run(
            [SEC] + argv, check=False, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return 1, f"sec 调用超时（{timeout}s）"
    return proc.returncode, (proc.stdout or proc.stderr or "").strip()


def run_sec(argv, timeout):
    try:
        proc = subprocess.run(
            [SEC] + argv, check=False, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return 1, {"error": f"sec 调用超时（{timeout}s）"}
    out = (proc.stdout or "").strip()
    try:
        data = json.loads(out) if out else {}
    except json.JSONDecodeError:
        data = {"error": out or (proc.stderr or "").strip() or "无法解析 sec 输出"}
    if proc.returncode != 0 and "error" not in data:
        data = {"error": (proc.stderr or "").strip() or "sec 退出码非零", "data": data}
    return proc.returncode, data


TOOLS = [
    {
        "name": "request_secret",
        "description": (
            "当一条命令需要真实的 API key / token / 密码，而该值不在环境变量里时使用。"
            "它给用户发一个只读一次、限时作废的链接：用户在网页里输入值并确认后，命令在服务端执行，"
            "本工具只拿到脱敏后的输出。绝不向用户索要明文，也不要把值写进聊天。"
            "拿到返回的 url 后立刻贴进回复，并在同一回合调用 secret_status 等待结果。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "program": {"type": "string", "description": "要执行的程序（不带 shell）；脚本用 program=sh, args=[\"-c\", \"...\"]"},
                "args": {"type": "array", "items": {"type": "string"}, "description": "参数数组"},
                "envs": {
                    "type": "array", "items": {"type": "string"},
                    "description": "命令从哪些环境变量读取秘密（推荐，值不进 argv）"
                },
                "stdin": {"type": "boolean", "description": "改为把秘密写到命令的 stdin（与 envs 二选一）"},
                "reason": {"type": "string", "description": "给用户看的理由：要做什么、为什么需要这个值"},
                "ttl": {"type": "integer", "minimum": 30, "maximum": 1800, "description": "链接有效期秒数，默认 150"}
            },
            "required": ["program", "reason"]
        },
    },
    {
        "name": "secret_status",
        "description": (
            "查询某个请求的状态，或等待它完成。返回 state（pending/running/ran/denied/expired）"
            "和 render（可原样贴给用户的 markdown，值已脱敏）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 150, "description": "0 = 只查一次；>0 = 阻塞等待"}
            },
            "required": ["id"]
        },
    },
    {
        "name": "secret_guide",
        "description": "返回 sec 的使用规则（什么时候用、怎么贴链接、哪些绝不能做）。第一次使用前读一次。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def text_content(text):
    return [{"type": "text", "text": text}]


def call_tool(name, args):
    if name == "request_secret":
        program = (args.get("program") or "").strip()
        if not program:
            return True, "缺少 program"
        envs = [str(x) for x in (args.get("envs") or []) if str(x).strip()]
        stdin_mode = bool(args.get("stdin"))
        if bool(envs) == bool(stdin_mode):
            return True, "envs 与 stdin 必须二选一"
        ttl = int(args.get("ttl") or 150)
        argv = ["request", "--reason", str(args.get("reason") or ""), "--ttl", str(ttl)]
        argv += ["--env", *envs] if envs else ["--stdin"]
        argv += ["--", program, *[str(a) for a in (args.get("args") or [])]]
        code, data = run_sec(argv, 30)
        if code != 0 or data.get("error"):
            return True, json.dumps(data, ensure_ascii=False)
        body = json.dumps({k: data.get(k) for k in ("id", "url", "expires", "ttl")},
                          ensure_ascii=False, indent=2)
        return False, body + "\n\n" + data.get("render", "")

    if name == "secret_status":
        rid = str(args.get("id") or "").strip()
        if not rid:
            return True, "缺少 id"
        wait = int(args.get("wait_seconds") or 0)
        argv = ["wait", rid, "--timeout", str(wait)] if wait > 0 else ["status", rid]
        code, data = run_sec(argv, max(30, wait + 20))
        if data.get("error"):
            return True, json.dumps(data, ensure_ascii=False)
        body = json.dumps({"id": data.get("id"), "state": data.get("state")},
                          ensure_ascii=False, indent=2)
        return False, body + "\n\n" + (data.get("render") or "")

    if name == "secret_guide":
        code, text = run_sec_raw(["guide"], 20)
        if code != 0:
            return True, text or "读取 guide 失败"
        return False, text

    return True, f"未知工具：{name}"


def handle(msg):
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "sec", "version": VERSION},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        is_error, text = call_tool(name, params.get("arguments") or {})
        return {"content": text_content(text), "isError": is_error}
    raise ValueError(f"不支持的方法：{method}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in msg:          # notification
            continue
        try:
            result = handle(msg)
            response = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        except Exception as exc:      # noqa: BLE001
            response = {"jsonrpc": "2.0", "id": msg["id"],
                        "error": {"code": -32603, "message": str(exc)}}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()