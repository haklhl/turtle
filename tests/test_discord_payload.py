import unittest

from sea_turtle.daemon import Daemon
from sea_turtle.channels.discord_components import build_layout_view, DiscordInteractionRuntime


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

    def test_parse_discord_components_block(self):
        payload = Daemon._parse_reply_payload(
            "hello\nDISCORD_COMPONENTS_JSON:\n```json\n{\n"
            "  \"components\": [\n"
            "    {\"type\": \"text_display\", \"content\": \"Choose an action\"},\n"
            "    {\"type\": \"button\", \"label\": \"Open form\", \"style\": \"primary\","
            "     \"action\": {\"type\": \"open_modal\", \"modal\": {\"title\": \"Feedback\","
            "       \"components\": [{\"type\": \"text_input\", \"custom_id\": \"summary\", \"label\": \"Summary\"}]}}}\n"
            "  ]\n"
            "}\n```"
        )
        self.assertEqual(payload["text"], "hello")
        self.assertIsInstance(payload["discord_components"], dict)
        self.assertEqual(len(payload["discord_components"]["components"]), 2)

    def test_build_layout_view_for_components_v2(self):
        runtime = DiscordInteractionRuntime(channel_manager=_DummyChannelManager(), agent_id="default", channel_id=123)
        view = build_layout_view(
            {
                "components": [
                    {"type": "text_display", "content": "hello"},
                    {
                        "type": "button",
                        "label": "Open form",
                        "style": "primary",
                        "action": {
                            "type": "open_modal",
                            "modal": {
                                "title": "Feedback",
                                "components": [
                                    {"type": "text_input", "custom_id": "summary", "label": "Summary"}
                                ],
                            },
                        },
                    },
                ]
            },
            runtime,
        )
        self.assertEqual(len(view.children), 2)

class _DummyChannelManager:
    def __init__(self):
        self.daemon = _DummyDaemon()


class _DummyDaemon:
    def route_message(self, **kwargs):
        return True


if __name__ == "__main__":
    unittest.main()
