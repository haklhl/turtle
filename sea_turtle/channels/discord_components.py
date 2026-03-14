"""Discord component/modal helpers built on top of discord.py."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

import discord

logger = logging.getLogger("sea_turtle.channels.discord_components")


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _build_allowed_mentions(spec: dict[str, Any] | None) -> discord.AllowedMentions | None:
    if not isinstance(spec, dict):
        return None
    everyone = _as_bool(spec.get("everyone"), False)
    users = spec.get("users", True)
    roles = spec.get("roles", True)
    replied_user = _as_bool(spec.get("replied_user"), False)
    if users is True:
        parsed_users: bool | list[int] = True
    elif isinstance(users, list):
        parsed_users = [int(item) for item in users]
    else:
        parsed_users = False
    if roles is True:
        parsed_roles: bool | list[int] = True
    elif isinstance(roles, list):
        parsed_roles = [int(item) for item in roles]
    else:
        parsed_roles = False
    return discord.AllowedMentions(
        everyone=everyone,
        users=parsed_users,
        roles=parsed_roles,
        replied_user=replied_user,
    )


class DiscordInteractionRuntime:
    """Runtime bridge for component/modal callbacks."""

    def __init__(self, channel_manager: Any, agent_id: str, channel_id: int):
        self.channel_manager = channel_manager
        self.daemon = channel_manager.daemon
        self.agent_id = agent_id
        self.channel_id = int(channel_id)

    async def handle_action(
        self,
        interaction: discord.Interaction,
        action: dict[str, Any] | None,
        event_data: dict[str, Any] | None = None,
    ) -> None:
        event_data = event_data or {}
        action = action or {"type": "route_message"}
        action_type = str(action.get("type") or "route_message").strip().lower()
        if action_type == "open_modal":
            modal_spec = action.get("modal")
            if not isinstance(modal_spec, dict):
                await self._send_interaction_message(interaction, "⚠️ Modal 配置无效。", ephemeral=True)
                return
            modal = build_modal(modal_spec, self, interaction, event_data)
            await interaction.response.send_modal(modal)
            return
        if action_type == "respond":
            content = str(action.get("content") or "").strip() or "已收到。"
            ephemeral = _as_bool(action.get("ephemeral"), True)
            await self._send_interaction_message(interaction, content, ephemeral=ephemeral)
            return
        if action_type != "route_message":
            await self._send_interaction_message(interaction, f"⚠️ 不支持的 action: {action_type}", ephemeral=True)
            return
        text = self._render_action_template(interaction, action, event_data)
        ack = str(action.get("ack") or "已收到，正在处理。").strip()
        ephemeral = _as_bool(action.get("ephemeral"), True)
        await self._send_interaction_message(interaction, ack, ephemeral=ephemeral)
        ok = self.daemon.route_message(
            text=text,
            agent_id=self.agent_id,
            source="discord",
            chat_id=interaction.channel_id,
            user_id=interaction.user.id,
            guild_id=interaction.guild_id,
        )
        if not ok:
            logger.warning(
                "Failed to route Discord interaction for agent '%s' in channel %s",
                self.agent_id,
                interaction.channel_id,
            )

    async def _send_interaction_message(
        self,
        interaction: discord.Interaction,
        content: str,
        *,
        ephemeral: bool = True,
    ) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)

    def _render_action_template(
        self,
        interaction: discord.Interaction,
        action: dict[str, Any],
        event_data: dict[str, Any],
    ) -> str:
        template = str(action.get("template") or "").strip()
        context = {
            "agent_id": self.agent_id,
            "channel_id": interaction.channel_id,
            "guild_id": interaction.guild_id or "",
            "user_id": interaction.user.id,
            "user_name": interaction.user.display_name,
            "values": ", ".join(str(item) for item in event_data.get("values", [])),
            "component_label": event_data.get("component_label", ""),
            "component_custom_id": event_data.get("component_custom_id", ""),
        }
        for key, value in (event_data.get("fields") or {}).items():
            context[f"fields.{key}"] = value
        if template:
            return _render_template(template, context)

        lines = [
            "Discord interactive event",
            f"- Agent: {self.agent_id}",
            f"- User: {interaction.user.display_name} ({interaction.user.id})",
            f"- Channel: {interaction.channel_id}",
        ]
        if interaction.guild_id:
            lines.append(f"- Guild: {interaction.guild_id}")
        if event_data.get("component_label"):
            lines.append(f"- Component: {event_data['component_label']}")
        if event_data.get("values"):
            lines.append(f"- Values: {', '.join(str(item) for item in event_data['values'])}")
        fields = event_data.get("fields") or {}
        if fields:
            lines.append("- Fields:")
            for key, value in fields.items():
                lines.append(f"  - {key}: {value}")
        return "\n".join(lines)


def _render_template(template: str, context: dict[str, Any]) -> str:
    pattern = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")
    return pattern.sub(lambda match: str(context.get(match.group(1), "")), template)


class RoutedButton(discord.ui.Button):
    def __init__(self, runtime: DiscordInteractionRuntime, spec: dict[str, Any]):
        style_value = str(spec.get("style") or "secondary").strip().lower()
        style = getattr(discord.ButtonStyle, style_value, discord.ButtonStyle.secondary)
        custom_id = None if spec.get("url") else str(spec.get("custom_id") or _generated_custom_id())
        super().__init__(
            style=style,
            label=spec.get("label"),
            disabled=_as_bool(spec.get("disabled"), False),
            custom_id=custom_id,
            url=spec.get("url"),
            emoji=spec.get("emoji"),
            row=spec.get("row"),
        )
        self.runtime = runtime
        self.action = spec.get("action")
        self.spec = spec

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.runtime.handle_action(
            interaction,
            self.action,
            {
                "component_label": self.label or "",
                "component_custom_id": self.custom_id or "",
            },
        )


class RoutedSelect(discord.ui.Select):
    def __init__(self, runtime: DiscordInteractionRuntime, spec: dict[str, Any]):
        options = []
        for item in spec.get("options", []) or []:
            if not isinstance(item, dict):
                continue
            options.append(
                discord.SelectOption(
                    label=str(item.get("label") or ""),
                    value=str(item.get("value") or item.get("label") or ""),
                    description=item.get("description"),
                    emoji=item.get("emoji"),
                    default=_as_bool(item.get("default"), False),
                )
            )
        super().__init__(
            custom_id=str(spec.get("custom_id") or _generated_custom_id()),
            placeholder=spec.get("placeholder"),
            min_values=int(spec.get("min_values", 1)),
            max_values=int(spec.get("max_values", 1)),
            options=options,
            disabled=_as_bool(spec.get("disabled"), False),
            row=spec.get("row"),
        )
        self.runtime = runtime
        self.action = spec.get("action")
        self.spec = spec

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.runtime.handle_action(
            interaction,
            self.action,
            {
                "component_label": self.placeholder or "select",
                "component_custom_id": self.custom_id,
                "values": list(self.values),
            },
        )


class RoutedModal(discord.ui.Modal):
    def __init__(
        self,
        runtime: DiscordInteractionRuntime,
        spec: dict[str, Any],
        origin_interaction: discord.Interaction,
        origin_event_data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            title=str(spec.get("title") or "Form"),
            timeout=spec.get("timeout"),
            custom_id=str(spec.get("custom_id") or _generated_custom_id(prefix="modal")),
        )
        self.runtime = runtime
        self.spec = spec
        self.origin_interaction = origin_interaction
        self.origin_event_data = origin_event_data or {}
        for item in spec.get("components", []) or []:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "text_input").strip().lower()
            if item_type != "text_input":
                raise ValueError(f"Unsupported modal component type: {item_type}")
            self.add_item(
                discord.ui.TextInput(
                    label=item.get("label"),
                    style=_resolve_text_style(item.get("style")),
                    custom_id=str(item.get("custom_id") or _generated_custom_id(prefix="field")),
                    placeholder=item.get("placeholder"),
                    default=item.get("default"),
                    required=_as_bool(item.get("required"), True),
                    min_length=item.get("min_length"),
                    max_length=item.get("max_length"),
                    row=item.get("row"),
                )
            )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        fields = {
            item.custom_id: str(item.value).strip()
            for item in self.children
            if isinstance(item, discord.ui.TextInput)
        }
        event_data = dict(self.origin_event_data)
        event_data["fields"] = fields
        submit_action = self.spec.get("submit") or {"type": "route_message"}
        await self.runtime.handle_action(interaction, submit_action, event_data)


def _generated_custom_id(prefix: str = "st") -> str:
    return f"sea_turtle:{prefix}:{uuid.uuid4().hex[:16]}"


def _resolve_text_style(value: Any) -> discord.TextStyle:
    name = str(value or "short").strip().lower()
    return getattr(discord.TextStyle, name, discord.TextStyle.short)


def _build_component_item(spec: dict[str, Any], runtime: DiscordInteractionRuntime) -> discord.ui.Item[Any]:
    item_type = str(spec.get("type") or "").strip().lower()
    if item_type in {"text", "text_display"}:
        return discord.ui.TextDisplay(str(spec.get("content") or ""))
    if item_type == "separator":
        spacing_name = str(spec.get("spacing") or "small").strip().lower()
        spacing = getattr(discord.SeparatorSpacing, spacing_name, discord.SeparatorSpacing.small)
        return discord.ui.Separator(
            visible=_as_bool(spec.get("visible"), True),
            spacing=spacing,
        )
    if item_type == "button":
        return RoutedButton(runtime, spec)
    if item_type == "select":
        return RoutedSelect(runtime, spec)
    if item_type == "thumbnail":
        media = spec.get("media")
        if not media:
            raise ValueError("thumbnail requires media")
        return discord.ui.Thumbnail(
            str(media),
            description=spec.get("description"),
            spoiler=_as_bool(spec.get("spoiler"), False),
        )
    if item_type == "media_gallery":
        items = []
        for item in spec.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            media = item.get("media")
            if not media:
                continue
            items.append(
                discord.MediaGalleryItem(
                    str(media),
                    description=item.get("description"),
                    spoiler=_as_bool(item.get("spoiler"), False),
                )
            )
        return discord.ui.MediaGallery(*items)
    if item_type == "section":
        raw_children = spec.get("children")
        raw_content = raw_children if isinstance(raw_children, list) else [spec.get("content")]
        children: list[Any] = []
        for child in raw_content:
            if isinstance(child, str) and child.strip():
                children.append(child)
            elif isinstance(child, dict):
                children.append(_build_component_item(child, runtime))
        accessory_spec = spec.get("accessory")
        if not isinstance(accessory_spec, dict):
            raise ValueError("section requires accessory")
        return discord.ui.Section(
            *children,
            accessory=_build_component_item(accessory_spec, runtime),
        )
    if item_type == "container":
        children = [
            _build_component_item(child, runtime)
            for child in spec.get("children", []) or []
            if isinstance(child, dict)
        ]
        return discord.ui.Container(
            *children,
            accent_color=spec.get("accent_color"),
            spoiler=_as_bool(spec.get("spoiler"), False),
        )
    raise ValueError(f"Unsupported Discord component type: {item_type}")


def build_layout_view(spec: dict[str, Any] | list[dict[str, Any]], runtime: DiscordInteractionRuntime) -> discord.ui.LayoutView:
    if isinstance(spec, list):
        payload = {"components": spec}
    elif isinstance(spec, dict):
        payload = spec
    else:
        raise ValueError("components payload must be an object or list")
    timeout = payload.get("timeout")
    view = discord.ui.LayoutView(timeout=timeout if timeout is not None else 3600.0)
    for item in payload.get("components", []) or []:
        if not isinstance(item, dict):
            continue
        view.add_item(_build_component_item(item, runtime))
    return view


def normalize_components_payload(
    spec: dict[str, Any] | list[dict[str, Any]],
    text: str = "",
) -> dict[str, Any]:
    if isinstance(spec, list):
        payload: dict[str, Any] = {"components": list(spec)}
    elif isinstance(spec, dict):
        payload = dict(spec)
        payload["components"] = list(payload.get("components", []) or [])
    else:
        raise ValueError("components payload must be an object or list")
    if text.strip():
        payload["components"] = [{"type": "text_display", "content": text.strip()}, *payload["components"]]
    payload["components"] = _wrap_top_level_interactives(payload["components"])
    return payload


def _wrap_top_level_interactives(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wrapped: list[dict[str, Any]] = []
    buffer: list[dict[str, Any]] = []

    def flush_buffer() -> None:
        nonlocal buffer
        if not buffer:
            return
        wrapped.append({"type": "container", "children": buffer})
        buffer = []

    for item in components:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type in {"button", "select"}:
            buffer.append(item)
            continue
        flush_buffer()
        wrapped.append(item)
    flush_buffer()
    return wrapped


def build_modal(
    spec: dict[str, Any],
    runtime: DiscordInteractionRuntime,
    interaction: discord.Interaction,
    event_data: dict[str, Any] | None = None,
) -> RoutedModal:
    return RoutedModal(runtime, spec, interaction, origin_event_data=event_data)


def build_allowed_mentions(spec: dict[str, Any] | None) -> discord.AllowedMentions | None:
    return _build_allowed_mentions(spec)
