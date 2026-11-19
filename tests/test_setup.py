"""`sec setup` 的行为测试：dry-run 归档、状态化 stub 的幂等与三种降级分支。

stub 是 PATH 上的假 `multica`，只记录 argv、按子命令回预设 JSON。用例显式去掉
`MULTICA_CLI_PATH`（双保险：实现本身也只认 PATH），避免打到真实 workspace 库。
"""
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
SKILL = SRC / "sec_gate" / "skills" / "sec" / "SKILL.md"

STUB = """#!/bin/sh
set -u
STATE=__STATE__
printf '%s\\n' "$*" >> "$STATE/argv.log"
case "$1 $2" in
  "workspace mcp")
    case "${3:-}" in
      list)
        if [ "$MODE" = "list_fail" ]; then
          echo '{"error":"workspace lookup failed"}' >&2
          exit 1
        fi
        if [ -f "$STATE/mcp_added" ]; then
          printf '[{"id":"srv-1","name":"sec","transport":"stdio"}]\\n'
        else
          printf '[]\\n'
        fi
        ;;
      add)
        if [ "$MODE" = "add_403" ]; then
          echo '403: {"error":"agents cannot modify the workspace MCP servers"}' >&2
          exit 3
        fi
        if [ "$MODE" = "add_fail" ]; then
          echo 'error: admin/owner only' >&2
          exit 1
        fi
        : > "$STATE/mcp_added"
        printf '{"id":"srv-1","name":"sec","transport":"stdio"}\\n'
        ;;
    esac
    ;;
  "skill list")
    printf '[]\\n'
    ;;
  "skill import")
    if [ "$MODE" = "import_fail" ]; then
      echo 'error: import rejected' >&2
      exit 1
    fi
    printf '{"status":"created","id":"skl-1","name":"sec"}\\n'
    ;;
  "skill create")
    printf '{"status":"created","id":"skl-1","name":"sec"}\\n'
    ;;
esac
exit 0
"""


class SetupTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.bin = self.root / "bin"
        self.state = self.root / "state"
        self.bin.mkdir()
        self.state.mkdir()
        self.log = self.state / "argv.log"

    def tearDown(self):
        self._tmp.cleanup()

    def write_stub(self):
        script = STUB.replace("__STATE__", shlex.quote(str(self.state)))
        path = self.bin / "multica"
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
        return path

    def add_sec_mcp(self):
        path = self.bin / "sec-mcp"
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def run_cli(self, *args, path, mode="ok"):
        env = {k: v for k, v in os.environ.items() if k != "MULTICA_CLI_PATH"}
        env["PYTHONPATH"] = str(SRC)
        env["PATH"] = path
        env["MODE"] = mode
        return subprocess.run(
            [sys.executable, "-m", "sec_gate.cli", "setup", *args],
            env=env, check=False, capture_output=True, text=True, timeout=120,
        )

    def calls(self):
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    @staticmethod
    def commands(stdout):
        return [line for line in stdout.splitlines() if line.startswith("multica ")]

    def test_skill_frontmatter_and_install_block(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nname: sec\n"), text[:40])
        block = "python3 -m venv .venv\n.venv/bin/pip install .\n"
        self.assertIn(block, text)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(block, readme)

    def test_dry_run_prints_two_multica_lines_and_archive(self):
        self.write_stub()
        sec_mcp = self.add_sec_mcp()
        proc = self.run_cli("--dry-run", path=str(self.bin))
        self.assertEqual(proc.returncode, 0, proc.stderr)

        commands = self.commands(proc.stdout)
        self.assertEqual(len(commands), 2, proc.stdout)

        mcp = shlex.split(commands[0])
        self.assertEqual(mcp[:4], ["multica", "workspace", "mcp", "add"])
        config = json.loads(mcp[mcp.index("--server-config") + 1])
        self.assertEqual(config["command"], str(sec_mcp))

        skill = shlex.split(commands[1])
        archive = Path(skill[skill.index("--file") + 1])
        self.assertTrue(archive.is_file(), archive)
        self.assertEqual(skill[skill.index("--on-conflict") + 1], "overwrite")

        with zipfile.ZipFile(archive) as zf:
            self.assertEqual(zf.namelist(), ["sec/SKILL.md"])
            body = zf.read("sec/SKILL.md")
        self.assertEqual(
            hashlib.sha256(body).hexdigest(),
            hashlib.sha256(SKILL.read_bytes()).hexdigest(),
        )

        # dry-run 不做任何 multica 调用
        self.assertEqual(self.calls(), [])

    def test_setup_is_idempotent_with_stateful_stub(self):
        self.write_stub()
        first = self.run_cli(path=str(self.bin))
        second = self.run_cli(path=str(self.bin))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)

        calls = self.calls()
        add_calls = [c for c in calls if c.startswith("workspace mcp add")]
        import_calls = [c for c in calls if c.startswith("skill import")]
        list_calls = [c for c in calls if c.startswith("workspace mcp list")]

        self.assertEqual(len(list_calls), 2, calls)
        self.assertEqual(len(add_calls), 1, calls)
        self.assertEqual(len(import_calls), 2, calls)
        self.assertIn("--on-conflict overwrite", import_calls[1])
        self.assertIn("MCP 条目 sec 已存在", second.stdout)
        self.assertIn("skill sec 已写入 workspace 库", first.stdout)

    def test_list_failure_prints_manual_commands_without_writes(self):
        self.write_stub()
        proc = self.run_cli(path=str(self.bin), mode="list_fail")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.commands(proc.stdout)), 2, proc.stdout)
        self.assertEqual(self.calls(), ["workspace mcp list --output json"])

    def test_add_403_prints_manual_command_and_still_imports_skill(self):
        self.write_stub()
        proc = self.run_cli(path=str(self.bin), mode="add_403")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("agents cannot modify the workspace MCP servers", proc.stdout)
        manual = [c for c in self.commands(proc.stdout)
                  if c.startswith("multica workspace mcp add sec ")]
        self.assertEqual(len(manual), 1, proc.stdout)
        self.assertIn("skill sec 已写入 workspace 库", proc.stdout)
        imports = [c for c in self.calls() if c.startswith("skill import")]
        self.assertEqual(len(imports), 1)

    def test_add_other_failure_also_degrades(self):
        self.write_stub()
        proc = self.run_cli(path=str(self.bin), mode="add_fail")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len([c for c in self.commands(proc.stdout)
                              if c.startswith("multica workspace mcp add sec ")]), 1)
        self.assertEqual(len([c for c in self.calls() if c.startswith("skill import")]), 1)

    def test_missing_multica_prints_manual_commands(self):
        empty = self.root / "empty"
        empty.mkdir()
        proc = self.run_cli(path=str(empty))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        commands = self.commands(proc.stdout)
        self.assertEqual(len(commands), 2, proc.stdout)
        skill = shlex.split(commands[1])
        archive = Path(skill[skill.index("--file") + 1])
        self.assertTrue(archive.is_file(), archive)

    def test_import_failure_falls_back_to_create(self):
        self.write_stub()
        proc = self.run_cli(path=str(self.bin), mode="import_fail")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        creates = [c for c in self.calls() if c.startswith("skill create")]
        self.assertEqual(len(creates), 1, self.calls())
        self.assertIn("--content-file", creates[0])
        self.assertIn("已通过 create/update 写入", proc.stdout)


if __name__ == "__main__":
    unittest.main()