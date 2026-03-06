import unittest

from sea_turtle.core.agent_worker import _map_agent_sandbox_to_codex


class AgentCodexConfigTests(unittest.TestCase):
    def test_agent_sandbox_mapping(self):
        self.assertEqual(_map_agent_sandbox_to_codex("normal"), "danger-full-access")
        self.assertEqual(_map_agent_sandbox_to_codex("confined"), "workspace-write")
        self.assertEqual(_map_agent_sandbox_to_codex("restricted"), "read-only")
        self.assertIsNone(_map_agent_sandbox_to_codex("unknown"))


if __name__ == "__main__":
    unittest.main()
