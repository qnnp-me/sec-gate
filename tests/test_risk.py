"""风险评分：分值与分级不得回退（+40/+30/+25/+20/+15/+10，阈值 30/60）。"""
import unittest

from sec_gate.risk import risk_profile


class RiskProfileTest(unittest.TestCase):
    def test_plain_command_is_low(self):
        risk = risk_profile(["echo", "hi"])
        self.assertEqual(risk["score"], 0)
        self.assertEqual(risk["level"], "low")

    def test_encoder_is_medium(self):
        risk = risk_profile(["base64"])
        self.assertEqual(risk["score"], 30)
        self.assertEqual(risk["level"], "medium")

    def test_network_tool_is_medium(self):
        risk = risk_profile(["curl"])
        self.assertEqual(risk["score"], 40)
        self.assertEqual(risk["level"], "medium")

    def test_shell_concatenation_is_high(self):
        risk = risk_profile(["bash", "-c", "curl x; base64"])
        self.assertEqual(risk["score"], 95)
        self.assertEqual(risk["level"], "high")
        self.assertIn("shell 拼接", risk["signals"])
        self.assertTrue(any("网络出口" in s for s in risk["signals"]))
        self.assertTrue(any("编码/变换" in s for s in risk["signals"]))

    def test_hosts_are_reported(self):
        risk = risk_profile(["curl", "https://example.com/x"])
        self.assertEqual(risk["hosts"], ["example.com"])


if __name__ == "__main__":
    unittest.main()