"""Discord Bot channel implementation."""

import asyncio
import logging
import os
from typing import Any, TYPE_CHECKING

import discord
from discord.ext import commands

from sea_turtle.channels.base import BaseChannel

if TYPE_CHECKING:
    from sea_turtle.daemon import Daemon

logger = logging.getLogger("sea_turtle.channels.discord")


class DiscordChannel(BaseChannel):
    """Discord Bot channel using discord.py.

    System commands (/ prefix) are routed to the daemon.
    Regular messages in allowed channels are forwarded to the agent.
    """

    def __init__(self, config: dict, daemon: "Daemon"):
        super().__init__(config, daemon)
        self.bots: dict[str, commands.Bot] = {}

    async def start(self) -> None:
        """Start Discord bot(s) for all configured agents."""
        seen_tokens: dict[str, str] = {}

        for agent_id, agent_cfg in self.config.get("agents", {}).items():
            dc_cfg = agent_cfg.get("discord", {})

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
            bot = commands.Bot(command_prefix="/", intents=intents)

            self._register_handlers(bot, agent_id)
            self.bots[agent_id] = bot

            # Start bot in background
            asyncio.create_task(self._run_bot(bot, token, agent_id))

        # Start reply dispatcher
        if self.bots:
            asyncio.create_task(self._reply_dispatcher())

    async def _run_bot(self, bot: commands.Bot, token: str, agent_id: str) -> None:
        """Run a Discord bot."""
        try:
            await bot.start(token)
        except Exception as e:
            logger.error(f"Discord bot for agent '{agent_id}' failed: {e}")

    def _register_handlers(self, bot: commands.Bot, agent_id: str) -> None:
        """Register event handlers on a Discord bot."""

        @bot.event
        async def on_ready():
            logger.info(f"Discord bot for agent '{agent_id}' connected as {bot.user}")

        @bot.event
        async def on_message(message: discord.Message):
            if message.author == bot.user:
                return

            user_id = message.author.id
            chat_id = message.channel.id
            text = message.content

            if not self._is_user_allowed(user_id, agent_id, "discord"):
                return

            if not text:
                return

            if text.startswith("/"):
                # System command
                reply = await self.daemon.handle_system_command(
                    command=text,
                    agent_id=agent_id,
                    source="discord",
                    chat_id=chat_id,
                    user_id=user_id,
                )
                if reply:
                    await self._send_discord_message(message.channel, reply)
            else:
                # Regular message
                success = self.daemon.route_message(
                    text=text,
                    agent_id=agent_id,
                    source="discord",
                    chat_id=chat_id,
                    user_id=user_id,
                )
                if not success:
                    await message.channel.send("⚠️ Agent is not available. Try /restart.")

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

    async def _reply_dispatcher(self) -> None:
        """Poll agent outboxes and send replies via Discord."""
        while True:
            for agent_id in list(self.bots.keys()):
                handle = self.daemon.agent_manager.get_handle(agent_id)
                if not handle:
                    continue
                try:
                    msg = handle.outbox.get_nowait()
                    if msg and msg.get("source") == "discord":
                        chat_id = msg.get("chat_id")
                        content = msg.get("content", "")
                        if chat_id and content:
                            await self.send_message(chat_id, content, agent_id)
                except Exception:
                    pass
            await asyncio.sleep(0.2)
