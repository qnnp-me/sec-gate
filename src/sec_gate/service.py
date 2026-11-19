"""服务进程与客户端连接：拉起、健康检查、端口选择、API 调用。"""
import contextlib
import fcntl
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from . import (
    LOCKFILE,
    LOGFILE,
    PIDFILE,
    PORT_MAX,
    PORT_MIN,
    PORTFILE,
    STATE,
    TOKENFILE,
    VERSION,
    configured_port,
)
from .httpapi import Handler
from .store import STORE


def serve(port):
    STATE.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(STATE, 0o700)
    except OSError:
        pass
    if not TOKENFILE.exists():
        TOKENFILE.write_text(secrets.token_urlsafe(32))
        os.chmod(TOKENFILE, 0o600)
    PIDFILE.write_text(str(os.getpid()))
    PORTFILE.write_text(str(port))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    httpd.daemon_threads = True
    httpd.allow_reuse_address = True

    def sweeper():
        while True:
            time.sleep(5)
            with contextlib.suppress(Exception):
                STORE.sweep()

    threading.Thread(target=sweeper, daemon=True).start()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: threading.Thread(target=httpd.shutdown, daemon=True).start())
    sys.stderr.write(f"sec {VERSION} listening on 0.0.0.0:{port} pid={os.getpid()}\n")
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    finally:
        try:
            PIDFILE.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- 客户端

def api(port, path, payload):
    token = TOKENFILE.read_text().strip()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method="POST",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Sec-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(raw)
        except Exception:  # noqa: BLE001
            detail = {"error": raw or str(exc)}
        detail["http_status"] = exc.code
        raise RuntimeError(detail.get("error") or str(exc)) from None


def healthy(port, timeout=0.6):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=timeout) as resp:
            return json.loads(resp.read()).get("ok") is True
    except Exception:  # noqa: BLE001
        return False


def read_port():
    fixed = configured_port()
    if fixed:
        return fixed
    try:
        return int(PORTFILE.read_text().strip())
    except Exception:  # noqa: BLE001
        return None


def pick_port():
    fixed = configured_port()
    if fixed:
        return fixed
    for _ in range(60):
        port = PORT_MIN + secrets.randbelow(max(1, PORT_MAX - PORT_MIN + 1))
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("0.0.0.0", port))
            return port
        except OSError:
            continue
    raise SystemExit(f"{PORT_MIN}-{PORT_MAX} 里找不到空闲端口")


def ensure_server():
    port = read_port()
    if port and healthy(port):
        return port
    STATE.mkdir(parents=True, exist_ok=True)
    with open(LOCKFILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        port = read_port()
        if port and healthy(port):
            return port
        port = pick_port()
        log = open(LOGFILE, "ab", buffering=0)  # noqa: SIM115
        subprocess.Popen(
            [sys.executable, "-m", "sec_gate", "serve", "--port", str(port)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            start_new_session=True, close_fds=True,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            if healthy(port, 0.3):
                PORTFILE.write_text(str(port))
                return port
            time.sleep(0.15)
        raise SystemExit(f"服务拉不起来（端口 {port}），看日志：{LOGFILE}")
