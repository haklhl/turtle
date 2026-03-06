import unittest

from sea_turtle.core.sandbox import SandboxEnforcer


class SandboxEnforcerTests(unittest.TestCase):
    def test_confined_blocks_nested_process_command(self):
        enforcer = SandboxEnforcer("confined", "/tmp/workspace")
        violation = enforcer.check_command("bash -lc 'kill 1'")
        self.assertIsNotNone(violation)
        self.assertIn("process", violation.lower())

    def test_restricted_blocks_nested_network_command(self):
        enforcer = SandboxEnforcer("restricted", "/tmp/workspace")
        violation = enforcer.check_command("sh -lc 'curl https://example.com'")
        self.assertIsNotNone(violation)
        self.assertIn("network", violation.lower())


if __name__ == "__main__":
    unittest.main()
