import tempfile
import unittest
from multiprocessing import Queue
from pathlib import Path

from sea_turtle.core.agent_worker import AgentWorker


class AgentResetTests(unittest.TestCase):
    def test_reset_loads_persisted_context_before_clearing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "context": {},
                "conversation_persistence": {"enabled": True},
                "shell": {},
                "agents": {
                    "pain": {
                        "workspace": tmpdir,
                        "model": "codex-spark",
                        "sandbox": "confined",
                    }
                },
            }

            worker_a = AgentWorker("pain", config, Queue(), Queue())
            _, ctx = worker_a._get_context("telegram", 1, 2)
            ctx.add_message("user", "hello")

            persisted = Path(tmpdir) / ".contexts" / "telegram__chat_1__user_2.json"
            self.assertTrue(persisted.exists())

            worker_b = AgentWorker("pain", config, Queue(), Queue())
            conversation_id = worker_b._reset_context("telegram", 1, 2)
            self.assertEqual(conversation_id, "telegram|chat:1|user:2")

            worker_c = AgentWorker("pain", config, Queue(), Queue())
            _, reloaded = worker_c._get_context("telegram", 1, 2)
            self.assertEqual(reloaded.messages, [])


if __name__ == "__main__":
    unittest.main()
