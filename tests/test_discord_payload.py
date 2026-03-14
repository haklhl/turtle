import unittest

from sea_turtle.daemon import Daemon


class DiscordPayloadTests(unittest.TestCase):
    def test_parse_single_line_discord_embed(self):
        payload = Daemon._parse_reply_payload(
            'hello\nDISCORD_EMBED: {"title":"Status","description":"ok"}'
        )
        self.assertEqual(payload["text"], "hello")
        self.assertEqual(payload["discord_embed"], {"title": "Status", "description": "ok"})

    def test_parse_block_discord_embed(self):
        payload = Daemon._parse_reply_payload(
            "summary\nDISCORD_EMBED_JSON:\n```json\n{\n  \"title\": \"Status\",\n  \"color\": 65280\n}\n```"
        )
        self.assertEqual(payload["text"], "summary")
        self.assertEqual(payload["discord_embed"], {"title": "Status", "color": 65280})

    def test_parse_multiple_discord_embeds(self):
        payload = Daemon._parse_reply_payload(
            'hello\nDISCORD_EMBED: {"embeds":[{"title":"Embed 1"},{"title":"Embed 2"}]}'
        )
        self.assertEqual(payload["text"], "hello")
        self.assertIsNone(payload["discord_embed"])
        self.assertEqual(payload["discord_embeds"], [{"title": "Embed 1"}, {"title": "Embed 2"}])


if __name__ == "__main__":
    unittest.main()
