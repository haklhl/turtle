"""Discord Bot channel implementation."""

import asyncio
import datetime
import logging
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from sea_turtle.channels.base import BaseChannel
from sea_turtle.channels.discord_components import (
    build_layout_view,
    DiscordInteractionRuntime,
    normalize_components_payload,
)
from sea_turtle.integrations import darwin_apex

if TYPE_CHECKING:
    from sea_turtle.daemon import Daemon

logger = logging.getLogger("sea_turtle.channels.discord")
AGENT_DISCORD_COMMAND_REGISTRARS = {
    "kakuzu": darwin_apex.register_discord_commands,
}

# Sensitive commands that require owner permission
SENSITIVE_COMMANDS = {"/restart", "/reset", "/model", "/agent", "/prompt"}
DISCORD_EMBED_LIMITS = {
    "title": 256,
    "description": 4096,
    "field_name": 256,
    "field_value": 1024,
    "fields": 25,
    "footer_text": 2048,
    "author_name": 256,
    "total": 6000,
}

SYS_COMMAND_TITLES = {
    "/start": "欢迎",
    "/help": "系统命令",
    "/context": "上下文统计",
    "/prompt": "最终系统提示词",
    "/heartbeat": "心跳状态",
    "/job": "后台任务",
    "/job_cancel": "后台任务取消",
    "/schedules": "定时作业",
    "/usage": "Token 用量",
    "/status": "Agent 状态",
    "/reset": "上下文重置",
    "/restart": "Agent 重启",
    "/model": "模型设置",
}


class DiscordChannel(BaseChannel):
    """Discord Bot channel using discord.py.

    Features:
    - Only responds to @mentions (configurable)
    - Guild/channel allowlist filtering
    - Owner permission for sensitive commands
    - Adds 👀 reaction when message is received
    - Slash commands for system operations
    """

    def __init__(self, config: dict, daemon: "Daemon"):
        super().__init__(config, daemon)
        self.bots: dict[str, commands.Bot] = {}
        self._agent_configs: dict[str, dict] = {}
        self._bot_tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Start Discord bot(s) for all configured agents."""
        seen_tokens: dict[str, str] = {}

        for agent_id, agent_cfg in self.config.get("agents", {}).items():
            dc_cfg = agent_cfg.get("discord", {})
            self._agent_configs[agent_id] = dc_cfg

            from sea_turtle.config.loader import resolve_secret
            token = resolve_secret(dc_cfg, "bot_token", "bot_token_env")
            if not token:
                logger.debug(f"No Discord token for agent '{agent_id}', skipping.")
                continue

            if token in seen_tokens:
                logger.info(f"Agent '{agent_id}' shares Discord bot with '{seen_tokens[token]}'")
                continue

            seen_tokens[token] = agent_id

            intents = discord.Intents.default()
            intents.message_content = True
            intents.guilds = True
            bot = commands.Bot(command_prefix="!", intents=intents)

            self._register_handlers(bot, agent_id, dc_cfg)
            self.bots[agent_id] = bot

            # Start bot in background
            task = asyncio.create_task(self._run_bot(bot, token, agent_id))
            self._bot_tasks.append(task)

        # Give bots a moment to start connecting
        if self._bot_tasks:
            await asyncio.sleep(0.5)

    async def _run_bot(self, bot: commands.Bot, token: str, agent_id: str) -> None:
        """Run a Discord bot."""
        logger.info(f"Starting Discord bot for agent '{agent_id}'...")
        try:
            await bot.start(token)
        except Exception as e:
            logger.error(f"Discord bot for agent '{agent_id}' failed: {e}", exc_info=True)

    def _is_guild_allowed(self, guild_id: int, dc_cfg: dict) -> bool:
        """Check if guild is in allowlist (empty = allow all)."""
        allowed = dc_cfg.get("allowed_guild_ids", [])
        if not allowed:
            return True
        return guild_id in allowed

    def _is_channel_allowed(self, channel_id: int, dc_cfg: dict) -> bool:
        """Check if channel is in allowlist (empty = allow all)."""
        allowed = dc_cfg.get("allowed_channel_ids", [])
        if not allowed:
            return True
        return channel_id in allowed

    def _should_respond(self, message: discord.Message, bot: commands.Bot, dc_cfg: dict) -> bool:
        """Check if bot should respond to this message."""
        # Always respond to DMs
        if isinstance(message.channel, discord.DMChannel):
            return True

        # Check if respond_to_mentions_only is enabled
        if dc_cfg.get("respond_to_mentions_only", True):
            # Check if bot is mentioned (user mention or role mention)
            if bot.user in message.mentions:
                return True
            # Also check if bot user ID appears in content (for role mentions)
            if bot.user and f"<@{bot.user.id}>" in message.content:
                return True
            if bot.user and f"<@!{bot.user.id}>" in message.content:
                return True
            # Check role mentions - if bot has any of the mentioned roles
            if message.role_mentions and bot.user:
                member = message.guild.get_member(bot.user.id) if message.guild else None
                if member:
                    for role in message.role_mentions:
                        if role in member.roles:
                            return True
            return False

        return True

    def _register_handlers(self, bot: commands.Bot, agent_id: str, dc_cfg: dict) -> None:
        """Register event handlers and slash commands on a Discord bot."""
        channel = self  # Capture reference for closures

        @bot.event
        async def on_ready():
            logger.info(f"Discord bot for agent '{agent_id}' connected as {bot.user} (ID: {bot.user.id})")
            try:
                await bot.change_presence(status=discord.Status.online)
            except Exception as e:
                logger.warning(f"Failed to set Discord presence for '{agent_id}': {e}")
            # Sync slash commands
            try:
                synced = await bot.tree.sync()
                logger.info(f"Synced {len(synced)} slash commands for '{agent_id}'")
            except Exception as e:
                logger.error(f"Failed to sync slash commands: {e}")

        @bot.event
        async def on_message(message: discord.Message):
            logger.debug(f"on_message from {message.author}: {message.content[:50]}")
            if message.author == bot.user:
                return
            if message.author.bot:
                return

            user_id = message.author.id
            chat_id = message.channel.id
            guild_id = message.guild.id if message.guild else 0
            guild_name = message.guild.name if message.guild else None
            channel_name = getattr(message.channel, "name", None)
            is_thread = isinstance(message.channel, discord.Thread)
            thread_name = message.channel.name if is_thread else None
            thread_parent = message.channel.parent if is_thread else None
            thread_parent_id = thread_parent.id if thread_parent else None
            thread_parent_name = getattr(thread_parent, "name", None) if thread_parent else None
            thread_parent_type = str(thread_parent.type) if thread_parent else None
            channel_topic = getattr(message.channel, "topic", None)
            if is_thread and thread_parent is not None:
                channel_topic = getattr(thread_parent, "topic", None)
            logger.debug(f"user_id={user_id}, chat_id={chat_id}, guild_id={guild_id}")

            if isinstance(message.channel, discord.DMChannel):
                logger.debug("Ignoring Discord DM message; Discord is channel-scoped for this deployment.")
                return

            # Check guild/channel allowlist
            if guild_id and not channel._is_guild_allowed(guild_id, dc_cfg):
                logger.debug(f"Guild {guild_id} not allowed")
                return
            if not channel._is_channel_allowed(chat_id, dc_cfg):
                logger.debug(f"Channel {chat_id} not allowed")
                return
            if not channel._is_user_allowed(user_id, agent_id, "discord"):
                logger.debug(f"User {user_id} not allowed for agent '{agent_id}'")
                return

            # Check if should respond (mentions only mode)
            if not channel._should_respond(message, bot, dc_cfg):
                logger.debug(f"Not responding (mentions_only mode, bot not mentioned)")
                return
            
            logger.debug(f"Processing message from {message.author}...")

            # Add 👀 reaction to show message was seen
            try:
                await message.add_reaction("👀")
            except Exception as e:
                logger.debug(f"Failed to add reaction: {e}")

            # Extract text, removing bot mention if present
            text = message.content
            if bot.user:
                text = text.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()

            if not text:
                return

            is_owner = channel._is_owner(user_id, agent_id, "discord")

            # Check for / commands in message text (legacy style)
            if text.startswith("/"):
                cmd = text.split()[0].lower()
                # Non-owner cannot execute sensitive commands
                if cmd in SENSITIVE_COMMANDS and not is_owner:
                    await message.channel.send("⛔ You don't have permission to execute this command.")
                    return

                reply = await channel.daemon.handle_system_command(
                    command=text,
                    agent_id=agent_id,
                    source="discord",
                    chat_id=chat_id,
                    user_id=user_id,
                    guild_id=guild_id,
                )
                if reply:
                    await channel._send_discord_message(message.channel, reply, agent_id=agent_id)
            else:
                # Regular message - forward to agent
                success = channel.daemon.route_message(
                    text=text,
                    agent_id=agent_id,
                    source="discord",
                    chat_id=chat_id,
                    user_id=user_id,
                    guild_id=guild_id,
                    message_id=message.id,
                    metadata={
                        "guild_name": guild_name,
                        "channel_name": channel_name,
                        "channel_topic": channel_topic,
                        "is_thread": is_thread,
                        "thread_name": thread_name,
                        "thread_parent_id": thread_parent_id,
                        "thread_parent_name": thread_parent_name,
                        "thread_parent_type": thread_parent_type,
                    },
                )
                if not success:
                    await message.channel.send("⚠️ Agent is not available.")

        # Register slash commands
        @bot.tree.command(name="sys_start", description="Start the bot and show welcome message")
        async def cmd_start(interaction: discord.Interaction):
            await channel._respond_system_slash(
                interaction,
                agent_id=agent_id,
                command="/start",
            )

        @bot.tree.command(name="sys_help", description="Show available commands")
        async def cmd_help(interaction: discord.Interaction):
            await channel._respond_system_slash(
                interaction,
                agent_id=agent_id,
                command="/help",
            )

        @bot.tree.command(name="sys_context", description="Show context statistics")
        async def cmd_context(interaction: discord.Interaction):
            await channel._respond_system_slash(
                interaction,
                agent_id=agent_id,
                command="/context",
            )

        @bot.tree.command(name="sys_prompt", description="Show current final system prompt (owner only)")
        async def cmd_prompt(interaction: discord.Interaction):
            if not channel._is_owner(interaction.user.id, agent_id, "discord"):
                await interaction.response.send_message("⛔ Owner permission required.", ephemeral=True)
                return
            await channel._respond_system_slash(
                interaction,
                agent_id=agent_id,
                command="/prompt",
            )

        @bot.tree.command(name="sys_heartbeat", description="Show heartbeat status")
        async def cmd_heartbeat(interaction: discord.Interaction):
            await channel._respond_system_slash(
                interaction,
                agent_id=agent_id,
                command="/heartbeat",
            )

        @bot.tree.command(name="sys_job", description="Show current background job status")
        async def cmd_job(interaction: discord.Interaction):
            await channel._respond_system_slash(
                interaction,
                agent_id=agent_id,
                command="/job",
            )

        @bot.tree.command(name="sys_job_cancel", description="Cancel the current background job")
        async def cmd_job_cancel(interaction: discord.Interaction):
            await channel._respond_system_slash(
                interaction,
                agent_id=agent_id,
                command="/job_cancel",
            )

        @bot.tree.command(name="sys_schedules", description="Show recent schedules")
        async def cmd_schedules(interaction: discord.Interaction):
            await channel._respond_system_slash(
                interaction,
                agent_id=agent_id,
                command="/schedules",
            )

        @bot.tree.command(name="sys_usage", description="Show token usage and costs")
        async def cmd_usage(interaction: discord.Interaction):
            await channel._respond_system_slash(
                interaction,
                agent_id=agent_id,
                command="/usage",
            )

        @bot.tree.command(name="sys_status", description="Show agent status")
        async def cmd_status(interaction: discord.Interaction):
            await channel._respond_system_slash(
                interaction,
                agent_id=agent_id,
                command="/status",
            )

        @bot.tree.command(name="sys_reset", description="Reset conversation context (owner only)")
        async def cmd_reset(interaction: discord.Interaction):
            if not channel._is_owner(interaction.user.id, agent_id, "discord"):
                await interaction.response.send_message("⛔ Owner permission required.", ephemeral=True)
                return
            await channel._respond_system_slash(
                interaction,
                agent_id=agent_id,
                command="/reset",
            )

        @bot.tree.command(name="sys_restart", description="Restart agent process (owner only)")
        async def cmd_restart(interaction: discord.Interaction):
            if not channel._is_owner(interaction.user.id, agent_id, "discord"):
                await interaction.response.send_message("⛔ Owner permission required.", ephemeral=True)
                return
            await channel._respond_system_slash(
                interaction,
                agent_id=agent_id,
                command="/restart",
            )

        @bot.tree.command(name="sys_model", description="List or switch models (owner only)")
        @app_commands.describe(action="'list' to show models, or model name to switch")
        async def cmd_model(interaction: discord.Interaction, action: str = "list"):
            if action != "list" and not channel._is_owner(interaction.user.id, agent_id, "discord"):
                await interaction.response.send_message("⛔ Owner permission required to switch models.", ephemeral=True)
                return
            await channel._respond_system_slash(
                interaction,
                agent_id=agent_id,
                command=f"/model {action}",
            )

        registrar = AGENT_DISCORD_COMMAND_REGISTRARS.get(agent_id)
        if registrar:
            try:
                registrar(bot, channel, agent_id)
            except Exception as e:
                logger.error(f"Failed to register agent-specific Discord commands for '{agent_id}': {e}", exc_info=True)

    async def _send_discord_message(
        self,
        channel,
        text: str,
        agent_id: str | None = None,
        embed: dict | None = None,
        embeds: list[dict] | None = None,
        components: dict | list[dict] | None = None,
        poll: dict | None = None,
        attachments: list[str] | None = None,
        react_to_message_id: Any = None,
        reactions: list[str] | None = None,
        reference_message_id: Any = None,
    ) -> bool:
        """Send a message to a Discord channel, optionally with embeds/files."""
        try:
            view = None
            message_text = text
            sanitized_embeds = _sanitize_embed_payloads(embeds or ([] if embed is None else [embed]))
            embed_objs = [discord.Embed.from_dict(item) for item in sanitized_embeds]
            if not embed_objs and embed:
                embed_objs = [discord.Embed.from_dict(item) for item in sanitized_embeds]
            poll_obj = None
            if isinstance(poll, dict):
                poll_obj = _build_discord_poll(poll)
            if components and agent_id:
                normalized_components = normalize_components_payload(components, text)
                runtime = DiscordInteractionRuntime(self, agent_id, channel.id)
                view = build_layout_view(normalized_components, runtime)
                message_text = ""
                if embed_objs:
                    logger.warning("Ignoring Discord embeds because Components V2 payload is present.")
                    embed_objs = []
                if poll_obj:
                    logger.warning("Ignoring Discord poll because Components V2 payload is present.")
                    poll_obj = None
            file_paths = [Path(item).expanduser() for item in attachments or [] if str(item).strip()]
            file_paths = [path for path in file_paths if path.exists() and path.is_file()]
            if len(message_text) <= 2000:
                kwargs = {}
                if embed_objs:
                    if len(embed_objs) == 1:
                        kwargs["embed"] = embed_objs[0]
                    else:
                        kwargs["embeds"] = embed_objs[:10]
                if view:
                    kwargs["view"] = view
                if poll_obj:
                    kwargs["poll"] = poll_obj
                if file_paths:
                    kwargs["files"] = [discord.File(str(path), filename=path.name) for path in file_paths[:10]]
                if reference_message_id:
                    kwargs["reference"] = channel.get_partial_message(int(reference_message_id)).to_reference(
                        fail_if_not_exists=False
                    )
                    kwargs["mention_author"] = False
                if message_text:
                    await channel.send(message_text, **kwargs)
                elif embed_objs or file_paths or view or poll_obj:
                    await channel.send(**kwargs)
            else:
                for i in range(0, len(message_text), 2000):
                    chunk = message_text[i:i + 2000]
                    kwargs = {}
                    if embed_objs and i == 0:
                        if len(embed_objs) == 1:
                            kwargs["embed"] = embed_objs[0]
                        else:
                            kwargs["embeds"] = embed_objs[:10]
                    if view and i == 0:
                        kwargs["view"] = view
                    if poll_obj and i == 0:
                        kwargs["poll"] = poll_obj
                    if file_paths and i == 0:
                        kwargs["files"] = [discord.File(str(path), filename=path.name) for path in file_paths[:10]]
                    if reference_message_id and i == 0:
                        kwargs["reference"] = channel.get_partial_message(int(reference_message_id)).to_reference(
                            fail_if_not_exists=False
                        )
                        kwargs["mention_author"] = False
                    await channel.send(chunk, **kwargs)
                    await asyncio.sleep(0.3)
            if react_to_message_id and reactions:
                target = channel.get_partial_message(int(react_to_message_id))
                for emoji in reactions:
                    try:
                        await target.add_reaction(emoji)
                    except Exception as e:
                        logger.warning(f"Failed to add Discord reaction {emoji!r}: {e}")
            return True
        except Exception as e:
            logger.error(f"Failed to send Discord message: {e}")
            return False

    async def _respond_system_slash(
        self,
        interaction: discord.Interaction,
        *,
        agent_id: str,
        command: str,
    ) -> bool:
        reply = await self.daemon.handle_system_command(
            command=command,
            agent_id=agent_id,
            source="discord",
            chat_id=interaction.channel_id,
            user_id=interaction.user.id,
            guild_id=interaction.guild_id,
        )
        text, embed, files = _build_system_command_response(command, reply)
        kwargs: dict[str, Any] = {"ephemeral": True}
        if embed is not None:
            kwargs["embed"] = embed
        if files:
            kwargs["files"] = files
        await interaction.response.send_message(text or None, **kwargs)

    async def send_message(
        self,
        chat_id: Any,
        text: str,
        agent_id: str | None = None,
        embed: dict | None = None,
        embeds: list[dict] | None = None,
        components: dict | list[dict] | None = None,
        poll: dict | None = None,
        attachments: list[str] | None = None,
        react_to_message_id: Any = None,
        reactions: list[str] | None = None,
        reference_message_id: Any = None,
    ) -> None:
        """Send a message to a Discord channel by ID."""
        if not agent_id or agent_id not in self.bots:
            logger.warning(f"Discord bot not available for agent '{agent_id}', cannot send reply to {chat_id}")
            return
        bot = self.bots[agent_id]

        try:
            channel = bot.get_channel(int(chat_id))
            if channel:
                return await self._send_discord_message(
                    channel,
                    text,
                    agent_id=agent_id,
                    embed=embed,
                    embeds=embeds,
                    components=components,
                    poll=poll,
                    attachments=attachments,
                    react_to_message_id=react_to_message_id,
                    reactions=reactions,
                    reference_message_id=reference_message_id,
                )
        except Exception as e:
            logger.error(f"Failed to send Discord message to {chat_id}: {e}")
        return False

    async def _get_channel(self, bot: commands.Bot, channel_id: int | str):
        try:
            channel_int = int(channel_id)
        except Exception as exc:
            raise ValueError(f"Invalid Discord channel id: {channel_id}") from exc
        channel = bot.get_channel(channel_int)
        if channel is None:
            channel = await bot.fetch_channel(channel_int)
        return channel

    @staticmethod
    def _channel_metadata(channel_obj: Any) -> dict[str, Any]:
        guild = getattr(channel_obj, "guild", None)
        is_thread = isinstance(channel_obj, discord.Thread)
        parent = getattr(channel_obj, "parent", None) if is_thread else None
        channel_topic = getattr(channel_obj, "topic", None)
        if is_thread and parent is not None:
            channel_topic = getattr(parent, "topic", None)
        return {
            "guild_name": getattr(guild, "name", None),
            "channel_name": getattr(channel_obj, "name", None),
            "channel_topic": channel_topic,
            "is_thread": is_thread,
            "thread_name": getattr(channel_obj, "name", None) if is_thread else None,
            "thread_parent_id": getattr(parent, "id", None) if parent else None,
            "thread_parent_name": getattr(parent, "name", None) if parent else None,
            "thread_parent_type": str(parent.type) if parent else None,
        }

    async def ensure_job_thread(
        self,
        *,
        agent_id: str,
        channel_id: Any,
        message_id: Any,
        title: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if agent_id not in self.bots:
            raise RuntimeError(f"Discord bot not available for agent '{agent_id}'")
        bot = self.bots[agent_id]
        current_channel = await self._get_channel(bot, channel_id)
        metadata = metadata or {}
        me = None
        if getattr(current_channel, "guild", None) and bot.user:
            me = current_channel.guild.get_member(bot.user.id)
        if bool(metadata.get("is_thread")) and isinstance(current_channel, discord.Thread):
            if me is not None:
                perms = current_channel.permissions_for(me)
                if not perms.send_messages_in_threads:
                    raise RuntimeError("缺少在当前 thread 发言的权限（需要 Send Messages in Threads）。")
            return {
                "thread_id": current_channel.id,
                "parent_channel_id": getattr(current_channel.parent, "id", None),
                "summary_channel_id": current_channel.id,
                "summary_message_id": message_id,
                "thread_metadata": self._channel_metadata(current_channel),
            }

        if not isinstance(current_channel, (discord.TextChannel, discord.ForumChannel)):
            raise RuntimeError("Discord job thread can only be created from a guild text/forum channel message.")
        if me is not None:
            perms = current_channel.permissions_for(me)
            if not perms.create_public_threads:
                raise RuntimeError("缺少创建公开 thread 的权限（需要 Create Public Threads）。")
            if not perms.send_messages_in_threads:
                raise RuntimeError("缺少在 thread 发言的权限（需要 Send Messages in Threads）。")
        message = await current_channel.fetch_message(int(message_id))
        thread_name = title.strip()[:90] or f"job-{message.id}"
        thread = await message.create_thread(name=thread_name, auto_archive_duration=1440)
        return {
            "thread_id": thread.id,
            "parent_channel_id": current_channel.id,
            "summary_channel_id": current_channel.id,
            "summary_message_id": message.id,
            "thread_metadata": self._channel_metadata(thread),
        }

    async def stop(self) -> None:
        """Stop all Discord bots."""
        for agent_id, bot in self.bots.items():
            try:
                await bot.close()
                logger.info(f"Discord bot stopped for agent '{agent_id}'")
            except Exception as e:
                logger.error(f"Error stopping Discord bot for '{agent_id}': {e}")


def _build_discord_poll(spec: dict[str, Any]) -> discord.Poll:
    question = str(spec.get("question") or "").strip()
    answers = spec.get("answers")
    if not question or not isinstance(answers, list) or len(answers) < 2:
        raise ValueError("Discord poll requires question and at least two answers")
    duration_hours_raw = spec.get("duration_hours", 24)
    try:
        duration_hours = max(1, min(168, int(duration_hours_raw)))
    except Exception:
        duration_hours = 24
    poll = discord.Poll(
        question=question,
        duration=datetime.timedelta(hours=duration_hours),
        multiple=bool(spec.get("multiple", False)),
    )
    for item in answers[:10]:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            emoji = item.get("emoji")
        else:
            text = str(item).strip()
            emoji = None
        if text:
            poll.add_answer(text=text, emoji=emoji)
    if len(poll.answers) < 2:
        raise ValueError("Discord poll requires at least two valid answers")
    return poll


def _take_embed_budget(text: str, limit: int, remaining: int) -> tuple[str, int]:
    if remaining <= 0:
        return "", 0
    cleaned = str(text or "")
    allowed = min(limit, remaining)
    if len(cleaned) > allowed:
        cleaned = cleaned[: max(0, allowed - 1)] + "…" if allowed > 0 else ""
    return cleaned, remaining - len(cleaned)


def _sanitize_embed_payloads(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    remaining = DISCORD_EMBED_LIMITS["total"]
    sanitized: list[dict[str, Any]] = []
    for raw in payloads[:10]:
        if not isinstance(raw, dict):
            continue
        item = deepcopy(raw)
        embed: dict[str, Any] = {}

        title, remaining = _take_embed_budget(item.get("title", ""), DISCORD_EMBED_LIMITS["title"], remaining)
        if title:
            embed["title"] = title

        description, remaining = _take_embed_budget(item.get("description", ""), DISCORD_EMBED_LIMITS["description"], remaining)
        if description:
            embed["description"] = description

        if isinstance(item.get("author"), dict):
            author = dict(item["author"])
            author_name, remaining = _take_embed_budget(author.get("name", ""), DISCORD_EMBED_LIMITS["author_name"], remaining)
            if author_name:
                author["name"] = author_name
            else:
                author.pop("name", None)
            if author:
                embed["author"] = author

        if isinstance(item.get("footer"), dict):
            footer = dict(item["footer"])
            footer_text, remaining = _take_embed_budget(footer.get("text", ""), DISCORD_EMBED_LIMITS["footer_text"], remaining)
            if footer_text:
                footer["text"] = footer_text
            else:
                footer.pop("text", None)
            if footer:
                embed["footer"] = footer

        fields: list[dict[str, Any]] = []
        for field in item.get("fields", [])[: DISCORD_EMBED_LIMITS["fields"]]:
            if not isinstance(field, dict):
                continue
            name, remaining = _take_embed_budget(field.get("name", ""), DISCORD_EMBED_LIMITS["field_name"], remaining)
            value, remaining = _take_embed_budget(field.get("value", ""), DISCORD_EMBED_LIMITS["field_value"], remaining)
            if not name and not value:
                continue
            fields.append({
                "name": name or "Value",
                "value": value or "n/a",
                "inline": bool(field.get("inline", False)),
            })
            if remaining <= 0:
                break
        if fields:
            embed["fields"] = fields

        for passthrough_key in ("color", "url", "timestamp", "thumbnail", "image"):
            if passthrough_key in item:
                embed[passthrough_key] = item[passthrough_key]

        if embed:
            sanitized.append(embed)
        if remaining <= 0:
            break
    return sanitized


def _build_system_command_response(command: str, reply: str) -> tuple[str, discord.Embed | None, list[discord.File]]:
    cleaned, attachment_paths = _extract_attachment_paths(reply)
    files = [discord.File(str(path), filename=path.name) for path in attachment_paths[:10]]
    embed = _build_system_command_embed(command, cleaned)
    if embed is not None:
        return "", embed, files
    return cleaned, None, files


def _extract_attachment_paths(reply: str) -> tuple[str, list[Path]]:
    body_lines: list[str] = []
    paths: list[Path] = []
    for raw_line in reply.splitlines():
        line = raw_line.rstrip()
        if line.startswith("ATTACH:"):
            candidate = line.split(":", 1)[1].strip()
            if candidate:
                path = Path(candidate).expanduser()
                if path.exists() and path.is_file():
                    paths.append(path)
            continue
        body_lines.append(raw_line)
    return "\n".join(body_lines).strip(), paths


def _build_system_command_embed(command: str, reply: str) -> discord.Embed | None:
    command_name = command.split()[0].lower()
    title = SYS_COMMAND_TITLES.get(command_name)
    if not title or not reply.strip():
        return None

    if command_name == "/help":
        return _build_help_embed(title, reply)
    if command_name in {"/status", "/context"}:
        return _build_key_value_embed(title, reply)
    if command_name == "/heartbeat":
        return _build_bullet_status_embed(title, reply, 0xE74C3C, 0x2ECC71)
    if command_name == "/job":
        return _build_bullet_status_embed(title, reply, 0xE67E22, 0x3498DB)
    if command_name == "/job_cancel":
        return _build_simple_embed(title, reply, 0xE67E22)
    if command_name == "/schedules":
        return _build_schedule_embed(title, reply)
    if command_name == "/usage":
        return _build_usage_embed(title, reply)
    if command_name == "/model":
        return _build_model_embed(title, reply)
    if command_name in {"/start", "/prompt", "/reset", "/restart"}:
        return _build_simple_embed(title, reply)
    return _build_simple_embed(title, reply)


def _build_simple_embed(title: str, reply: str, color: int = 0x3498DB) -> discord.Embed:
    embed = discord.Embed(title=title, description=_clamp_text(reply, 4096), color=color)
    return embed


def _build_help_embed(title: str, reply: str) -> discord.Embed:
    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    embed = discord.Embed(title=title, color=0x3498DB)
    description_lines: list[str] = []
    for line in lines:
        if line.startswith("/"):
            command, _, detail = line.partition("—")
            embed.add_field(
                name=command.strip(),
                value=_clamp_text((detail or "").strip() or "No description.", 1024),
                inline=False,
            )
        else:
            description_lines.append(line)
    if description_lines:
        embed.description = _clamp_text("\n".join(description_lines), 4096)
    return embed


def _build_key_value_embed(title: str, reply: str) -> discord.Embed:
    lines = [line.rstrip() for line in reply.splitlines() if line.strip()]
    heading = lines[0] if lines else title
    color = 0x2ECC71 if "🟢" in reply else 0x3498DB
    embed = discord.Embed(title=title, description=_strip_leading_emoji(heading), color=color)
    for line in lines[1:]:
        stripped = line.strip()
        if ":" not in stripped:
            embed.add_field(name="Note", value=_clamp_text(stripped, 1024), inline=False)
            continue
        key, value = stripped.split(":", 1)
        embed.add_field(
            name=_strip_bullet(key).strip()[:256] or "Value",
            value=_clamp_text(value.strip() or "n/a", 1024),
            inline=False,
        )
    return embed


def _build_usage_embed(title: str, reply: str) -> discord.Embed:
    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    embed = discord.Embed(title=title, color=0x9B59B6)
    description_lines: list[str] = []
    for line in lines:
        if ":" not in line:
            description_lines.append(line)
            continue
        key, value = line.split(":", 1)
        embed.add_field(
            name=_strip_bullet(key).strip()[:256] or "Metric",
            value=_clamp_text(value.strip() or "n/a", 1024),
            inline=False,
        )
    if description_lines:
        embed.description = _clamp_text("\n".join(description_lines), 4096)
    return embed


def _build_bullet_status_embed(title: str, reply: str, warning_color: int, ok_color: int) -> discord.Embed:
    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    heading = lines[0] if lines else title
    lowered = reply.lower()
    color = ok_color
    if "disabled" in lowered or "没有" in lowered or "取消" in lowered:
        color = warning_color
    if "失败" in lowered or "stopped" in lowered or "error" in lowered:
        color = 0xE74C3C
    embed = discord.Embed(title=title, description=_strip_leading_emoji(heading), color=color)
    for line in lines[1:]:
        if line.startswith("- ") and ":" in line:
            key, value = line[2:].split(":", 1)
            embed.add_field(name=key.strip()[:256] or "Value", value=_clamp_text(value.strip() or "n/a", 1024), inline=False)
        else:
            embed.add_field(name="Note", value=_clamp_text(line, 1024), inline=False)
    return embed


def _build_schedule_embed(title: str, reply: str) -> discord.Embed:
    lines = [line.rstrip() for line in reply.splitlines() if line.strip()]
    embed = discord.Embed(title=title, color=0x3498DB)
    if not lines:
        embed.description = "当前没有定时作业。"
        return embed
    description = lines[0]
    embed.description = _strip_leading_emoji(description)
    current_name = ""
    current_value = ""
    for line in lines[1:]:
        stripped = line.strip()
        if stripped.startswith("- "):
            if current_name:
                embed.add_field(name=current_name[:256], value=_clamp_text(current_value.strip() or "n/a", 1024), inline=False)
            current_name = stripped[2:][:256]
            current_value = ""
        else:
            current_value = f"{current_value}\n{stripped}".strip()
    if current_name:
        embed.add_field(name=current_name[:256], value=_clamp_text(current_value.strip() or "n/a", 1024), inline=False)
    return embed


def _build_model_embed(title: str, reply: str) -> discord.Embed:
    color = 0x34495E
    lowered = reply.lower()
    if lowered.startswith("✅"):
        color = 0x2ECC71
    elif lowered.startswith("⚠️") or lowered.startswith("usage:"):
        color = 0xE67E22
    embed = discord.Embed(title=title, color=color)
    if "📦 " in reply:
        chunks = []
        current_chunk = ""
        for line in reply.splitlines():
            candidate = f"{current_chunk}\n{line}".strip() if current_chunk else line
            if len(candidate) > 1000:
                chunks.append(current_chunk)
                current_chunk = line
            else:
                current_chunk = candidate
        if current_chunk:
            chunks.append(current_chunk)
        for index, chunk in enumerate(chunks[:6], start=1):
            embed.add_field(name=f"Available Models {index}", value=f"```text\n{chunk[:1000]}\n```", inline=False)
    else:
        embed.description = _clamp_text(reply, 4096)
    return embed


def _strip_leading_emoji(text: str) -> str:
    return re.sub(r"^[^\w/]+", "", text).strip()


def _strip_bullet(text: str) -> str:
    return text.lstrip("- ").strip()


def _clamp_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"
