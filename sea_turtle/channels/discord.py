"""Discord Bot channel implementation."""

import asyncio
import datetime
import logging
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

# Sensitive commands that require owner permission
SENSITIVE_COMMANDS = {"/restart", "/reset", "/model", "/agent", "/prompt"}


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
        @bot.tree.command(name="start", description="Start the bot and show welcome message")
        async def cmd_start(interaction: discord.Interaction):
            reply = await channel.daemon.handle_system_command(
                command="/start", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
                guild_id=interaction.guild_id,
            )
            await interaction.response.send_message(reply, ephemeral=True)

        @bot.tree.command(name="help", description="Show available commands")
        async def cmd_help(interaction: discord.Interaction):
            reply = await channel.daemon.handle_system_command(
                command="/help", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
                guild_id=interaction.guild_id,
            )
            await interaction.response.send_message(reply, ephemeral=True)

        @bot.tree.command(name="context", description="Show context statistics")
        async def cmd_context(interaction: discord.Interaction):
            reply = await channel.daemon.handle_system_command(
                command="/context", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
                guild_id=interaction.guild_id,
            )
            await interaction.response.send_message(reply, ephemeral=True)

        @bot.tree.command(name="prompt", description="Show current final system prompt (owner only)")
        async def cmd_prompt(interaction: discord.Interaction):
            if not channel._is_owner(interaction.user.id, agent_id, "discord"):
                await interaction.response.send_message("⛔ Owner permission required.", ephemeral=True)
                return
            reply = await channel.daemon.handle_system_command(
                command="/prompt", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
                guild_id=interaction.guild_id,
            )
            await interaction.response.send_message(reply, ephemeral=True)

        @bot.tree.command(name="usage", description="Show token usage and costs")
        async def cmd_usage(interaction: discord.Interaction):
            reply = await channel.daemon.handle_system_command(
                command="/usage", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
                guild_id=interaction.guild_id,
            )
            await interaction.response.send_message(reply, ephemeral=True)

        @bot.tree.command(name="status", description="Show agent status")
        async def cmd_status(interaction: discord.Interaction):
            reply = await channel.daemon.handle_system_command(
                command="/status", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
                guild_id=interaction.guild_id,
            )
            await interaction.response.send_message(reply, ephemeral=True)

        @bot.tree.command(name="reset", description="Reset conversation context (owner only)")
        async def cmd_reset(interaction: discord.Interaction):
            if not channel._is_owner(interaction.user.id, agent_id, "discord"):
                await interaction.response.send_message("⛔ Owner permission required.", ephemeral=True)
                return
            reply = await channel.daemon.handle_system_command(
                command="/reset", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
                guild_id=interaction.guild_id,
            )
            await interaction.response.send_message(reply, ephemeral=True)

        @bot.tree.command(name="restart", description="Restart agent process (owner only)")
        async def cmd_restart(interaction: discord.Interaction):
            if not channel._is_owner(interaction.user.id, agent_id, "discord"):
                await interaction.response.send_message("⛔ Owner permission required.", ephemeral=True)
                return
            reply = await channel.daemon.handle_system_command(
                command="/restart", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
                guild_id=interaction.guild_id,
            )
            await interaction.response.send_message(reply, ephemeral=True)

        @bot.tree.command(name="model", description="List or switch models (owner only)")
        @app_commands.describe(action="'list' to show models, or model name to switch")
        async def cmd_model(interaction: discord.Interaction, action: str = "list"):
            if action != "list" and not channel._is_owner(interaction.user.id, agent_id, "discord"):
                await interaction.response.send_message("⛔ Owner permission required to switch models.", ephemeral=True)
                return
            reply = await channel.daemon.handle_system_command(
                command=f"/model {action}", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
                guild_id=interaction.guild_id,
            )
            await interaction.response.send_message(reply, ephemeral=True)

        if agent_id == "kakuzu":
            @bot.tree.command(name="apex_status", description="查看 DarwinApex 与 MemeHarpoon 状态")
            async def cmd_apex_status(interaction: discord.Interaction):
                await channel._handle_kakuzu_command(interaction, command_name="apex_status")

            @bot.tree.command(name="roadmap", description="查看 DarwinApex 长期路线图")
            async def cmd_roadmap(interaction: discord.Interaction):
                await channel._handle_kakuzu_command(interaction, command_name="roadmap")

            @bot.tree.command(name="goals", description="查看 DarwinApex 长期目标")
            async def cmd_goals(interaction: discord.Interaction):
                await channel._handle_kakuzu_command(interaction, command_name="goals")

            @bot.tree.command(name="last_iter", description="查看最近一轮 DarwinApex 迭代")
            async def cmd_last_iter(interaction: discord.Interaction):
                await channel._handle_kakuzu_command(interaction, command_name="last_iter")

    async def _handle_kakuzu_command(self, interaction: discord.Interaction, *, command_name: str) -> None:
        agent_id = "kakuzu"
        if not self._is_user_allowed(interaction.user.id, agent_id, "discord"):
            await interaction.response.send_message("⛔ Unauthorized.", ephemeral=True)
            return
        try:
            if command_name == "apex_status":
                bundle = await darwin_apex.fetch_status_bundle()
                await interaction.response.send_message(
                    embed=discord.Embed.from_dict(darwin_apex.status_embed(bundle)),
                    ephemeral=True,
                )
                return
            if command_name == "roadmap":
                bundle = darwin_apex.load_roadmap_bundle()
                await interaction.response.send_message(
                    embed=discord.Embed.from_dict(darwin_apex.roadmap_embed(bundle)),
                    ephemeral=True,
                )
                return
            if command_name == "goals":
                bundle = darwin_apex.load_goals_bundle()
                embeds = [discord.Embed.from_dict(item) for item in darwin_apex.goals_embeds(bundle)]
                await interaction.response.send_message(embeds=embeds[:10], ephemeral=True)
                return
            if command_name == "last_iter":
                bundle = darwin_apex.load_last_iter_bundle()
                embeds = [discord.Embed.from_dict(item) for item in darwin_apex.last_iter_embeds(bundle)]
                await interaction.response.send_message(embeds=embeds[:10], ephemeral=True)
                return
            await interaction.response.send_message("Unsupported command.", ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to handle Kakuzu Discord command {command_name}: {e}", exc_info=True)
            if interaction.response.is_done():
                await interaction.followup.send(f"⚠️ {command_name} failed: {e}", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {command_name} failed: {e}", ephemeral=True)

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
    ) -> None:
        """Send a message to a Discord channel, optionally with embeds/files."""
        try:
            view = None
            message_text = text
            embed_objs = [discord.Embed.from_dict(item) for item in embeds or [] if isinstance(item, dict)]
            if not embed_objs and embed:
                embed_objs = [discord.Embed.from_dict(embed)]
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
                    await channel.send(chunk, **kwargs)
                    await asyncio.sleep(0.3)
            if react_to_message_id and reactions:
                target = channel.get_partial_message(int(react_to_message_id))
                for emoji in reactions:
                    try:
                        await target.add_reaction(emoji)
                    except Exception as e:
                        logger.warning(f"Failed to add Discord reaction {emoji!r}: {e}")
        except Exception as e:
            logger.error(f"Failed to send Discord message: {e}")

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
    ) -> None:
        """Send a message to a Discord channel by ID."""
        if not agent_id or agent_id not in self.bots:
            logger.warning(f"Discord bot not available for agent '{agent_id}', cannot send reply to {chat_id}")
            return
        bot = self.bots[agent_id]

        try:
            channel = bot.get_channel(int(chat_id))
            if channel:
                await self._send_discord_message(
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
                )
        except Exception as e:
            logger.error(f"Failed to send Discord message to {chat_id}: {e}")

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
