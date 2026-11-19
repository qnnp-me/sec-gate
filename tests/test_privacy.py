"""隐私守卫自测：脚本必须对命中样本退出 1，对本仓库退出 0。

样本在运行时拼接，仓库里不出现模式连续字面量（参见 scripts/privacy_scan.sh）。
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "privacy_scan.sh"


def run_scan(target):
    return subprocess.run(
        ["bash", str(SCRIPT), str(target)], check=False, capture_output=True, text=True
    )


class PrivacyScanTest(unittest.TestCase):
    def test_repo_is_clean(self):
        proc = run_scan(ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("SCAN=PASS", proc.stdout)

    def test_detects_leaked_host_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = "mu" + "137" + ".example.invalid"
            Path(tmp, "leak.txt").write_text("host=" + marker + "\n", encoding="utf-8")
            proc = run_scan(tmp)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("SCAN=FAIL", proc.stdout)


if __name__ == "__main__":
    unittest.main()