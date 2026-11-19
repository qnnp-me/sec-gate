"""命令执行与输出脱敏：值只进 env 或 stdin，不进 argv。"""
import os
import subprocess

from . import CMD_TIMEOUT, OUTPUT_LIMIT


def redact(text, values):
    """把每个值替换成 [secret:NAME]；短值也要替换（宁可误伤可读性）。"""
    if not text:
        return ""
    for name, value in sorted(values.items(), key=lambda kv: -len(kv[1])):
        if not value:
            continue
        text = text.replace(value, f"[secret:{name}]")
    return text


def tail(text, limit=OUTPUT_LIMIT):
    return text if len(text) <= limit else text[-limit:]


def run_request(item, values):
    """在子进程里执行命令：值只进 env 或 stdin，不进 argv。"""
    env = os.environ.copy()
    stdin_data = None
    if item["stdin"]:
        stdin_data = values["VALUE"]
    else:
        env.update(values)
    try:
        proc = subprocess.run(
            item["argv"], check=False, env=env,
            input=stdin_data.encode() if stdin_data is not None else None,
            capture_output=True, timeout=CMD_TIMEOUT, cwd=os.getcwd(),
        )
        code, out, err = proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")
        outcome = "exited"
    except subprocess.TimeoutExpired:
        code, out, err, outcome = None, "", f"命令超过 {CMD_TIMEOUT}s 被终止", "timeout"
    except FileNotFoundError as exc:
        code, out, err, outcome = None, "", f"无法执行：{exc}", "not-started"
    except Exception as exc:  # noqa: BLE001
        code, out, err, outcome = None, "", f"执行失败：{exc}", "error"
    return {
        "state": "ran", "outcome": outcome, "exit": code,
        "stdout": tail(redact(out, values)),
        "stderr": tail(redact(err, values)),
        "redacted": sorted(values),
        "duration_ms": None,
    }
