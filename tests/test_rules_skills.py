from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sea_turtle.core.rules import load_global_skills, load_skills


class RulesSkillsTests(unittest.TestCase):
    def test_load_global_skills_uses_channel_specific_file(self):
        discord_skills = load_global_skills("discord")
        self.assertIn("Discord Skills", discord_skills)
        self.assertIn("Components V2", discord_skills)

        telegram_skills = load_global_skills("telegram")
        self.assertIn("Telegram Skills", telegram_skills)
        self.assertIn("STICKER_EMOTION", telegram_skills)
        self.assertNotIn("Discord Skills", telegram_skills)

    def test_load_skills_merges_global_agent_and_channel_files(self):
        with TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "skills.md").write_text("agent common", encoding="utf-8")
            (ws / "skills.discord.md").write_text("agent discord", encoding="utf-8")
            merged = load_skills(str(ws), "discord")
            self.assertIn("Discord Skills", merged)
            self.assertIn("agent common", merged)
            self.assertIn("agent discord", merged)


if __name__ == "__main__":
    unittest.main()
