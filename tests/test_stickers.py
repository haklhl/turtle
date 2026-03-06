import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sea_turtle.core.stickers import (
    infer_emotion_from_emoji,
    load_sticker_data,
    pick_sticker_for_emotion,
    register_sticker,
)


class StickerRegistryTests(unittest.TestCase):
    def test_register_sticker_infers_emotion_from_emoji(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sticker = register_sticker(
                tmpdir,
                file_id="file-1",
                file_unique_id="unique-1",
                emoji="😊",
                set_name="konan_pack",
            )
            self.assertEqual(sticker["emotion"], "warm")
            self.assertTrue((Path(tmpdir) / "stickers.json").exists())

    def test_pick_sticker_for_emotion_chooses_from_matching_pool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            register_sticker(tmpdir, file_id="file-1", file_unique_id="unique-1", emoji="😊")
            register_sticker(tmpdir, file_id="file-2", file_unique_id="unique-2", emoji="🙂")
            with patch("sea_turtle.core.stickers.random.choice", lambda items: items[-1]):
                sticker = pick_sticker_for_emotion(tmpdir, "warm")
            self.assertEqual(sticker["file_id"], "file-2")

    def test_unknown_emoji_stays_unclassified(self):
        self.assertIsNone(infer_emotion_from_emoji("🧪"))

    def test_new_extended_emotion_mappings(self):
        self.assertEqual(infer_emotion_from_emoji("🤪"), "playful")
        self.assertEqual(infer_emotion_from_emoji("😨"), "surprised")
        self.assertEqual(infer_emotion_from_emoji("😪"), "tired")
        self.assertEqual(infer_emotion_from_emoji("💪"), "supportive")
        self.assertEqual(infer_emotion_from_emoji("🙅‍♂️"), "refuse")

    def test_load_backfills_emotion_for_existing_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "stickers.json"
            path.write_text(
                '{"version":1,"stickers":[{"id":"sticker-1","file_id":"f1","file_unique_id":"u1","emoji":"💔","emotion":"","created_at":"2026-03-06T00:00:00+00:00","updated_at":"2026-03-06T00:00:00+00:00"}]}\n',
                encoding="utf-8",
            )
            data = load_sticker_data(tmpdir)
            self.assertEqual(data["stickers"][0]["emotion"], "sad")


if __name__ == "__main__":
    unittest.main()
