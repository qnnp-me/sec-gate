"""sec-gate — 单文件密码链接工具：你在网页输入密码，命令拿到它，agent 只看到脱敏输出。

值只进环境变量或 stdin，命令在服务进程里执行，输出中的值替换为 [secret:NAME]。

包级共享的版本号、配置常量与状态路径集中在这里，供各模块导入。
"""
import os
from pathlib import Path

__version__ = "0.1.0"
VERSION = __version__

STATE = Path(os.environ.get("SEC_HOME", Path.home() / ".local/state/sec"))
PIDFILE = STATE / "sec.pid"
LOCKFILE = STATE / "sec.lock"
TOKENFILE = STATE / "api.token"
LOGFILE = STATE / "sec.log"
LASTFILE = STATE / "last"
PORTFILE = STATE / "port"
PORT_MIN = int(os.environ.get("SEC_PORT_MIN", "30000"))
PORT_MAX = int(os.environ.get("SEC_PORT_MAX", "30999"))
DEFAULT_TTL = int(os.environ.get("SEC_TTL", "300"))
MAX_TTL = 1800
MAX_PENDING = 8
MAX_VALUE = 16384
CMD_TIMEOUT = int(os.environ.get("SEC_CMD_TIMEOUT", "120"))
OUTPUT_LIMIT = 32000


def configured_port():
    """SEC_PORT 固定端口；未设置则每次拉起时随机选。"""
    raw = os.environ.get("SEC_PORT")
    return int(raw) if raw else None


def public_base(port):
    override = os.environ.get("SEC_PUBLIC_BASE")
    if override:
        return override.rstrip("/")
    return f"http://127.0.0.1:{port}"