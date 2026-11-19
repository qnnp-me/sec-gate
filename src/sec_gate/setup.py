"""`sec setup`：把 sec 的 MCP 入口与 skill 注册进当前 Multica workspace。

设计要点：

- `multica` 只从 PATH 解析，不读 `MULTICA_CLI_PATH`（运行时若设了该变量，路径上的
  测试 stub 会被顶掉，甚至可能把写请求发到真实 workspace）；
- 默认执行注册，`--dry-run` 只打印两条命令与归档路径，不做任何写入；
- `multica workspace mcp list` 本身失败时不写任何状态，只打印手工命令；
- `list` 正常但 `workspace mcp add` 被拒（agent 环境是 403，退出码 3）时打印手工命令，
  仍然继续写 skill，整体退出码保持 0；
- skill 走 `skill import --file <自建 .skill> --on-conflict overwrite`，服务端拒绝该
  归档形态时才回退 `skill create/update --content-file`。
"""
import json
import shlex
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

SKILL_NAME = "sec"
MCP_NAME = "sec"
SKILL_DESC = "通过一次性链接让用户在网页里输入凭据，命令在服务端执行，agent 只拿到脱敏后的输出。"
SKILL_PATH = Path(__file__).resolve().parent / "skills" / SKILL_NAME / "SKILL.md"


def multica_bin():
    """只认 PATH；故意不读 MULTICA_CLI_PATH。"""
    return shutil.which("multica")


def mcp_config():
    """MCP 条目配置：command 指向 PATH 上的 sec-mcp，找不到时回落裸名。"""
    command = shutil.which("sec-mcp") or "sec-mcp"
    return json.dumps({"command": command, "args": []}, ensure_ascii=False)


def mcp_add_argv():
    return ["workspace", "mcp", "add", MCP_NAME, "--server-config", mcp_config()]


def skill_import_argv(archive):
    return ["skill", "import", "--file", str(archive), "--on-conflict", "overwrite",
            "--output", "json"]


def build_archive():
    """把唯一来源 SKILL.md 打进 `<tmp>/sec/SKILL.md` 的 .skill（zip）归档。

    归档留在磁盘上：dry-run 与手工回退命令都要引用这个路径。
    """
    root = Path(tempfile.mkdtemp(prefix="sec-setup-"))
    archive = root / f"{SKILL_NAME}.skill"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(SKILL_PATH, f"{SKILL_NAME}/SKILL.md")
    return archive


def print_command(argv):
    print("multica " + shlex.join(argv))


def print_manual(archive):
    print("在你自己终端执行：")
    print_command(mcp_add_argv())
    print_command(skill_import_argv(archive))


def _run(argv):
    print("-> " + shlex.join(argv))
    return subprocess.run(argv, check=False, capture_output=True, text=True)


def _summary(proc, limit=300):
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    out = " ".join(out.split())
    return out if len(out) <= limit else out[:limit] + "…"


def _mcp_present(stdout):
    try:
        items = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        return False
    return any(isinstance(item, dict) and item.get("name") == MCP_NAME for item in items)


def _skill_fallback(bin_path):
    """skill import 被拒时的回退：已有同名 skill 就 update，否则 create。"""
    listing = _run([bin_path, "skill", "list", "--output", "json"])
    existing = None
    if listing.returncode == 0:
        try:
            items = json.loads(listing.stdout or "[]")
        except json.JSONDecodeError:
            items = []
        existing = next(
            (item for item in items
             if isinstance(item, dict) and item.get("name") == SKILL_NAME),
            None,
        )
    if existing and existing.get("id"):
        argv = [bin_path, "skill", "update", str(existing["id"]),
                "--content-file", str(SKILL_PATH), "--output", "json"]
    else:
        argv = [bin_path, "skill", "create", "--name", SKILL_NAME,
                "--description", SKILL_DESC, "--content-file", str(SKILL_PATH),
                "--output", "json"]
    return _run(argv).returncode


def run_setup(dry_run=False):
    archive = build_archive()
    print(f"归档：{archive}（内层 {SKILL_NAME}/SKILL.md）")

    if dry_run:
        print("dry-run：只打印，不执行")
        print_command(mcp_add_argv())
        print_command(skill_import_argv(archive))
        return 0

    bin_path = multica_bin()
    if not bin_path:
        print("PATH 上没有 multica：跳过自动注册。")
        print_manual(archive)
        return 0

    listing = _run([bin_path, "workspace", "mcp", "list", "--output", "json"])
    if listing.returncode != 0:
        print(f"workspace mcp list 失败（rc={listing.returncode}）：{_summary(listing)}")
        print("没有写入任何状态。")
        print_manual(archive)
        return 0

    if _mcp_present(listing.stdout):
        print(f"MCP 条目 {MCP_NAME} 已存在，跳过 add。")
    else:
        added = _run([bin_path, *mcp_add_argv()])
        if added.returncode == 0:
            print(f"MCP 条目 {MCP_NAME} 已写入 workspace 库。")
        else:
            print(f"workspace mcp add 被拒（rc={added.returncode}）：{_summary(added)}")
            print("agent 无权写 workspace MCP 库，请在你自己终端执行：")
            print_command(mcp_add_argv())

    imported = _run([bin_path, *skill_import_argv(archive)])
    if imported.returncode == 0:
        print(f"skill {SKILL_NAME} 已写入 workspace 库。")
        return 0

    print(f"skill import 失败（rc={imported.returncode}）：{_summary(imported)}")
    if _skill_fallback(bin_path) == 0:
        print(f"skill {SKILL_NAME} 已通过 create/update 写入 workspace 库。")
    else:
        print("skill 写入失败，请手工执行：")
        print_command(skill_import_argv(archive))
    return 0