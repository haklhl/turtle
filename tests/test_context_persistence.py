import tempfile
import unittest
from pathlib import Path

from sea_turtle.core.context import ContextManager


class ContextPersistenceTests(unittest.TestCase):
    def test_messages_persist_to_disk_and_reload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".contexts" / "telegram__chat_1__user_2.json"
            config = {
                "context": {},
                "conversation_persistence": {"enabled": True},
            }

            ctx = ContextManager(config, persistence_path=str(path))
            ctx.add_message("user", "hello")
            ctx.add_message("assistant", "world")

            reloaded = ContextManager(config, persistence_path=str(path))
            self.assertEqual(len(reloaded.messages), 2)
            self.assertEqual(reloaded.messages[0]["content"], "hello")
            self.assertEqual(reloaded.messages[1]["content"], "world")

    def test_timing_stats_persist_to_disk_and_reload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".contexts" / "telegram__chat_1__user_2.json"
            config = {
                "context": {},
                "conversation_persistence": {"enabled": True},
            }

            ctx = ContextManager(config, persistence_path=str(path))
            ctx.record_response_time(1200)
            ctx.record_response_time(800)

            reloaded = ContextManager(config, persistence_path=str(path))
            stats = reloaded.get_stats()
            self.assertEqual(stats["request_count"], 2)
            self.assertEqual(stats["last_response_time_ms"], 800)
            self.assertEqual(stats["avg_response_time_ms"], 1000)


if __name__ == "__main__":
    unittest.main()
