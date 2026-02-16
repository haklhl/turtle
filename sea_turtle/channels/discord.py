"""Discord Bot channel implementation."""

import asyncio
import logging
from typing import Any, TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from sea_turtle.channels.base import BaseChannel

if TYPE_CHECKING:
    from sea_turtle.daemon import Daemon

logger = logging.getLogger("sea_turtle.channels.discord")

# Sensitive commands that require owner permission
SENSITIVE_COMMANDS = {"/restart", "/reset", "/model", "/agent"}


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
            logger.debug(f"user_id={user_id}, chat_id={chat_id}, guild_id={guild_id}")

            # Check guild/channel allowlist
            if guild_id and not channel._is_guild_allowed(guild_id, dc_cfg):
                logger.debug(f"Guild {guild_id} not allowed")
                return
            if not channel._is_channel_allowed(chat_id, dc_cfg):
                logger.debug(f"Channel {chat_id} not allowed")
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
                )
                if reply:
                    await channel._send_discord_message(message.channel, reply)
            else:
                # Regular message - forward to agent
                success = channel.daemon.route_message(
                    text=text,
                    agent_id=agent_id,
                    source="discord",
                    chat_id=chat_id,
                    user_id=user_id,
                )
                if not success:
                    await message.channel.send("⚠️ Agent is not available.")

        # Register slash commands
        @bot.tree.command(name="start", description="Start the bot and show welcome message")
        async def cmd_start(interaction: discord.Interaction):
            reply = await channel.daemon.handle_system_command(
                command="/start", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
            )
            await interaction.response.send_message(reply)

        @bot.tree.command(name="help", description="Show available commands")
        async def cmd_help(interaction: discord.Interaction):
            reply = await channel.daemon.handle_system_command(
                command="/help", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
            )
            await interaction.response.send_message(reply)

        @bot.tree.command(name="context", description="Show context statistics")
        async def cmd_context(interaction: discord.Interaction):
            reply = await channel.daemon.handle_system_command(
                command="/context", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
            )
            await interaction.response.send_message(reply)

        @bot.tree.command(name="usage", description="Show token usage and costs")
        async def cmd_usage(interaction: discord.Interaction):
            reply = await channel.daemon.handle_system_command(
                command="/usage", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
            )
            await interaction.response.send_message(reply)

        @bot.tree.command(name="status", description="Show agent status")
        async def cmd_status(interaction: discord.Interaction):
            reply = await channel.daemon.handle_system_command(
                command="/status", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
            )
            await interaction.response.send_message(reply)

        @bot.tree.command(name="reset", description="Reset conversation context (owner only)")
        async def cmd_reset(interaction: discord.Interaction):
            if not channel._is_owner(interaction.user.id, agent_id, "discord"):
                await interaction.response.send_message("⛔ Owner permission required.", ephemeral=True)
                return
            reply = await channel.daemon.handle_system_command(
                command="/reset", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
            )
            await interaction.response.send_message(reply)

        @bot.tree.command(name="restart", description="Restart agent process (owner only)")
        async def cmd_restart(interaction: discord.Interaction):
            if not channel._is_owner(interaction.user.id, agent_id, "discord"):
                await interaction.response.send_message("⛔ Owner permission required.", ephemeral=True)
                return
            reply = await channel.daemon.handle_system_command(
                command="/restart", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
            )
            await interaction.response.send_message(reply)

        @bot.tree.command(name="model", description="List or switch models (owner only)")
        @app_commands.describe(action="'list' to show models, or model name to switch")
        async def cmd_model(interaction: discord.Interaction, action: str = "list"):
            if action != "list" and not channel._is_owner(interaction.user.id, agent_id, "discord"):
                await interaction.response.send_message("⛔ Owner permission required to switch models.", ephemeral=True)
                return
            reply = await channel.daemon.handle_system_command(
                command=f"/model {action}", agent_id=agent_id, source="discord",
                chat_id=interaction.channel_id, user_id=interaction.user.id,
            )
            await interaction.response.send_message(reply)

    async def _send_discord_message(self, channel, text: str) -> None:
        """Send a message to a Discord channel, splitting if needed."""
        try:
            if len(text) <= 2000:
                await channel.send(text)
            else:
                for i in range(0, len(text), 2000):
                    chunk = text[i:i + 2000]
                    await channel.send(chunk)
                    await asyncio.sleep(0.3)
        except Exception as e:
            logger.error(f"Failed to send Discord message: {e}")

    async def send_message(self, chat_id: Any, text: str, agent_id: str | None = None) -> None:
        """Send a message to a Discord channel by ID."""
        bot = None
        if agent_id and agent_id in self.bots:
            bot = self.bots[agent_id]
        elif self.bots:
            bot = next(iter(self.bots.values()))

        if bot:
            try:
                channel = bot.get_channel(int(chat_id))
                if channel:
                    await self._send_discord_message(channel, text)
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

