"""命令执行与脱敏：env/stdin 注入、长短值都替换为 [secret:NAME]。"""
import unittest

from sec_gate.runner import redact, run_request


class RedactTest(unittest.TestCase):
    def test_long_and_short_values_are_masked(self):
        values = {"A": "abc", "B": "abcdef"}
        self.assertEqual(redact("abc and abcdef", values), "[secret:A] and [secret:B]")

    def test_empty_text_and_empty_value(self):
        self.assertEqual(redact("", {"A": "abc"}), "")
        self.assertEqual(redact("abc", {"A": ""}), "abc")


class RunRequestTest(unittest.TestCase):
    def test_env_injection_is_redacted(self):
        item = {"argv": ["sh", "-c", 'printf %s "$DEMO"'], "stdin": False}
        result = run_request(item, {"DEMO": "hunter2-env"})
        self.assertEqual(result["outcome"], "exited")
        self.assertEqual(result["exit"], 0)
        self.assertEqual(result["stdout"], "[secret:DEMO]")
        self.assertEqual(result["redacted"], ["DEMO"])

    def test_stdin_injection_is_redacted(self):
        item = {"argv": ["cat"], "stdin": True}
        result = run_request(item, {"VALUE": "hunter2-stdin"})
        self.assertEqual(result["outcome"], "exited")
        self.assertEqual(result["exit"], 0)
        self.assertEqual(result["stdout"], "[secret:VALUE]")
        self.assertEqual(result["redacted"], ["VALUE"])

    def test_missing_binary_reports_not_started(self):
        item = {"argv": ["sec-gate-no-such-binary-xyz"], "stdin": False}
        result = run_request(item, {"DEMO": "value"})
        self.assertEqual(result["outcome"], "not-started")
        self.assertIsNone(result["exit"])


if __name__ == "__main__":
    unittest.main()