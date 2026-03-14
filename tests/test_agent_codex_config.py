import unittest
from multiprocessing import Queue

from sea_turtle.core.agent_worker import AgentWorker, _map_agent_sandbox_to_codex


class AgentCodexConfigTests(unittest.TestCase):
    def test_agent_sandbox_mapping(self):
        self.assertEqual(_map_agent_sandbox_to_codex("normal"), "danger-full-access")
        self.assertEqual(_map_agent_sandbox_to_codex("confined"), "workspace-write")
        self.assertEqual(_map_agent_sandbox_to_codex("restricted"), "read-only")
        self.assertIsNone(_map_agent_sandbox_to_codex("unknown"))

    def test_discord_tools_are_channel_scoped(self):
        config = {
            "context": {},
            "conversation_persistence": {"enabled": False},
            "shell": {},
            "agents": {
                "default": {
                    "workspace": "/tmp/default",
                    "model": "codex-5.4",
                    "sandbox": "confined",
                    "tools": ["shell", "memory", "schedule"],
                    "discord": {"bot_token": "test-token"},
                }
            },
        }
        worker = AgentWorker("default", config, Queue(), Queue())
        telegram_tools = {tool.name for tool in worker._get_tools("telegram")}
        discord_tools = {tool.name for tool in worker._get_tools("discord")}

        self.assertNotIn("discord_channel_info", telegram_tools)
        self.assertNotIn("discord_read_messages", telegram_tools)
        self.assertNotIn("discord_search_messages", telegram_tools)
        self.assertIn("discord_channel_info", discord_tools)
        self.assertIn("discord_read_messages", discord_tools)
        self.assertIn("discord_search_messages", discord_tools)


if __name__ == "__main__":
    unittest.main()
