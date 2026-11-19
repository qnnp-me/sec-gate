"""CLI：argparse 子命令与客户端命令实现。"""
import argparse
import json
import os
import signal
import sys
import time

from . import DEFAULT_TTL, LASTFILE, PIDFILE, PORTFILE, STATE, configured_port
from .httpapi import GUIDE_FULL
from .service import api, ensure_server, healthy, pick_port, read_port, serve


def remember(rid):
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        LASTFILE.write_text(rid)
        os.chmod(LASTFILE, 0o600)
    except OSError:
        pass


def last_id():
    try:
        rid = LASTFILE.read_text().strip()
    except OSError:
        rid = ""
    return rid or None


def die(msg, code=1):
    print(json.dumps({"error": msg}, ensure_ascii=False))
    sys.exit(code)


def cmd_request(args):
    argv = list(args.command)
    if not argv:
        die("缺少命令：在 -- 之后写")
    if bool(args.env) == bool(args.stdin):
        die("必须且只能选一种注入方式：--env NAME（可重复）或 --stdin")
    port = ensure_server()
    payload = {"argv": argv, "envs": args.env or [], "stdin": args.stdin,
               "reason": args.reason or "", "ttl": args.ttl}
    try:
        out = api(port, "/api/request", payload)
    except Exception as exc:  # noqa: BLE001
        die(f"创建请求失败：{exc}")
    remember(out["id"])
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_wait(args):
    rid = args.id or last_id()
    if not rid:
        die("没有请求 id，也没有 --last 记录")
    port = ensure_server()
    deadline = time.time() + args.timeout
    last = None
    while time.time() < deadline:
        try:
            out = api(port, "/api/status", {"id": rid})
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if msg == "not found":
                die("请求不存在：服务重启会丢弃内存中的请求，请重新 sec request")
            die(f"查询失败：{msg}")
        last = out
        if out.get("state") in ("ran", "denied", "expired"):
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        time.sleep(1)
    print(json.dumps(last or {"id": rid, "state": "pending"}, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args):
    rid = args.id or last_id()
    if not rid:
        die("没有请求 id，也没有 --last 记录")
    port = ensure_server()
    try:
        out = api(port, "/api/status", {"id": rid})
    except Exception as exc:  # noqa: BLE001
        if str(exc) == "not found":
            die("请求不存在：服务重启会丢弃内存中的请求，请重新 sec request")
        die(f"查询失败：{exc}")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_guide(args):
    print(GUIDE_FULL, end="")
    return 0


def cmd_render(args):
    rid = args.id or last_id()
    if not rid:
        die("没有请求 id，也没有 --last 记录")
    port = ensure_server()
    out = api(port, "/api/status", {"id": rid})
    print(out.get("render") or "")
    return 0


def cmd_list(args):
    port = ensure_server()
    out = api(port, "/api/pending", {})
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_stop(args):
    if not PIDFILE.exists():
        print(json.dumps({"stopped": False, "reason": "没有 pid 文件"}, ensure_ascii=False))
        return 0
    try:
        pid = int(PIDFILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"stopped": False, "reason": str(exc)}, ensure_ascii=False))
        return 1
    port = read_port()
    for _ in range(30):
        if not port or not healthy(port, 0.2):
            break
        time.sleep(0.1)
    try:
        PORTFILE.unlink()
    except OSError:
        pass
    print(json.dumps({"stopped": True, "pid": pid}, ensure_ascii=False))
    return 0


def main():
    parser = argparse.ArgumentParser(prog="sec", description="密码链接输入 + 命令注入")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("request")
    p.add_argument("--env", action="append", default=[])
    p.add_argument("--stdin", action="store_true")
    p.add_argument("--ttl", type=int, default=DEFAULT_TTL)
    p.add_argument("--reason", default="")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_request)

    p = sub.add_parser("wait")
    p.add_argument("id", nargs="?")
    p.add_argument("--last", action="store_true", help="用最近一次 request 的 id")
    p.add_argument("--timeout", type=int, default=150, help="等待秒数，默认 150")
    p.set_defaults(func=cmd_wait)

    p = sub.add_parser("status")
    p.add_argument("id", nargs="?")
    p.add_argument("--last", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("render")
    p.add_argument("id", nargs="?")
    p.add_argument("--last", action="store_true")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("guide")
    p.set_defaults(func=cmd_guide)

    p = sub.add_parser("list")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("stop")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=None)
    def _serve(args):
        serve(args.port or configured_port() or pick_port())
        return 0
    p.set_defaults(func=_serve)

    args = parser.parse_args()
    # argparse.REMAINDER 会保留前导 "--"，去掉它
    if args.cmd == "request" and args.command and args.command[0] == "--":
        args.command = args.command[1:]
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
