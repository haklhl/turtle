import unittest
from types import SimpleNamespace

from sea_turtle.daemon import Daemon
from sea_turtle.channels.discord_components import (
    build_layout_view,
    DiscordInteractionRuntime,
    normalize_components_payload,
)


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

    def test_normalize_components_lifts_plain_text(self):
        payload = normalize_components_payload(
            {"components": [{"type": "button", "label": "Go"}]},
            text="summary first",
        )
        self.assertEqual(payload["components"][0], {"type": "text_display", "content": "summary first"})
        self.assertEqual(payload["components"][1]["type"], "action_row")
        self.assertEqual(payload["components"][1]["children"][0]["type"], "button")

    def test_normalize_components_wraps_top_level_interactives(self):
        payload = normalize_components_payload(
            {
                "components": [
                    {"type": "text_display", "content": "Actions"},
                    {"type": "button", "label": "One"},
                    {"type": "button", "label": "Two"},
                    {"type": "select", "options": [{"label": "A", "value": "a"}]},
                    {"type": "separator"},
                ]
            }
        )
        self.assertEqual(payload["components"][0]["type"], "text_display")
        self.assertEqual(payload["components"][1]["type"], "action_row")
        self.assertEqual(
            [item["type"] for item in payload["components"][1]["children"]],
            ["button", "button", "select"],
        )
        self.assertEqual(payload["components"][2]["type"], "separator")

    def test_build_layout_view_supports_action_row(self):
        runtime = DiscordInteractionRuntime(channel_manager=_DummyChannelManager(), agent_id="default", channel_id=123)
        view = build_layout_view(
            {
                "components": [
                    {
                        "type": "action_row",
                        "children": [
                            {"type": "button", "label": "One"},
                            {"type": "button", "label": "Two"},
                        ],
                    }
                ]
            },
            runtime,
        )
        self.assertEqual(len(view.children), 1)
        self.assertEqual(view.children[0].__class__.__name__, "ActionRow")


class DiscordInteractionPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_interaction_requires_owner(self):
        runtime = DiscordInteractionRuntime(
            channel_manager=_DummyChannelManager(owner=False),
            agent_id="default",
            channel_id=123,
        )
        sent = {}

        async def send_message(content, ephemeral=True):
            sent["content"] = content
            sent["ephemeral"] = ephemeral

        interaction = SimpleNamespace(
            user=SimpleNamespace(id=1, display_name="guest"),
            channel_id=123,
            guild_id=456,
            response=SimpleNamespace(is_done=lambda: False, send_message=send_message),
            followup=SimpleNamespace(send=send_message),
        )

        await runtime.handle_action(interaction, {"type": "respond", "content": "ok"})
        self.assertEqual(sent["content"], "⛔ Owner permission required.")
        self.assertTrue(sent["ephemeral"])


class _DummyChannelManager:
    def __init__(self, *, owner: bool = True):
        self.daemon = _DummyDaemon()
        self._owner = owner

    def _is_owner(self, user_id, agent_id, channel_type):
        return self._owner


class _DummyDaemon:
    def route_message(self, **kwargs):
        return True


if __name__ == "__main__":
    unittest.main()
