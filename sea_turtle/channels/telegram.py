"""Telegram Bot channel implementation."""

import asyncio
import logging
import os
from typing import Any, TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from sea_turtle.channels.base import BaseChannel

if TYPE_CHECKING:
    from sea_turtle.daemon import Daemon

logger = logging.getLogger("sea_turtle.channels.telegram")


class TelegramChannel(BaseChannel):
    """Telegram Bot channel using python-telegram-bot (async).

    Each agent can have its own Telegram bot, or share one bot.
    System commands (/ prefix) are routed to the daemon.
    Regular messages are forwarded to the agent.
    """

    def __init__(self, config: dict, daemon: "Daemon"):
        super().__init__(config, daemon)
        self.applications: dict[str, Application] = {}
        self._agent_bot_map: dict[str, str] = {}  # bot_token -> agent_id

    async def start(self) -> None:
        """Start Telegram bot(s) for all configured agents."""
        seen_tokens: dict[str, str] = {}

        for agent_id, agent_cfg in self.config.get("agents", {}).items():
            tg_cfg = agent_cfg.get("telegram", {})
            token_env = tg_cfg.get("bot_token_env", "")
            if not token_env:
                continue

            token = os.environ.get(token_env, "")
            if not token:
                logger.warning(f"Telegram token env '{token_env}' not set for agent '{agent_id}', skipping.")
                continue

            # Avoid starting duplicate bots for the same token
            if token in seen_tokens:
                logger.info(f"Agent '{agent_id}' shares Telegram bot with '{seen_tokens[token]}'")
                self._agent_bot_map[token] = seen_tokens[token]  # Map to first agent
                continue

            seen_tokens[token] = agent_id
            self._agent_bot_map[token] = agent_id

            app = Application.builder().token(token).build()

            # Register handlers
            app.add_handler(CommandHandler("start", self._make_command_handler(agent_id)))
            app.add_handler(CommandHandler("help", self._make_command_handler(agent_id)))
            app.add_handler(CommandHandler("reset", self._make_command_handler(agent_id)))
            app.add_handler(CommandHandler("context", self._make_command_handler(agent_id)))
            app.add_handler(CommandHandler("restart", self._make_command_handler(agent_id)))
            app.add_handler(CommandHandler("usage", self._make_command_handler(agent_id)))
            app.add_handler(CommandHandler("status", self._make_command_handler(agent_id)))
            app.add_handler(CommandHandler("model", self._make_command_handler(agent_id)))
            app.add_handler(CommandHandler("agent", self._make_command_handler(agent_id)))

            # Regular messages
            app.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._make_message_handler(agent_id),
            ))

            self.applications[agent_id] = app

            # Start polling in background
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)

            logger.info(f"Telegram bot started for agent '{agent_id}'")

        # Start reply dispatcher for Telegram
        asyncio.create_task(self._reply_dispatcher())

    async def stop(self) -> None:
        """Stop all Telegram bots."""
        for agent_id, app in self.applications.items():
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
                logger.info(f"Telegram bot stopped for agent '{agent_id}'")
            except Exception as e:
                logger.error(f"Error stopping Telegram bot for '{agent_id}': {e}")

    async def send_message(self, chat_id: Any, text: str, agent_id: str | None = None) -> None:
        """Send a message to a Telegram chat.

        Args:
            chat_id: Telegram chat ID.
            text: Message text.
            agent_id: Agent whose bot to use. Uses first available if None.
        """
        app = None
        if agent_id and agent_id in self.applications:
            app = self.applications[agent_id]
        elif self.applications:
            app = next(iter(self.applications.values()))

        if app and app.bot:
            try:
                # Telegram has a 4096 char limit per message
                if len(text) <= 4096:
                    await app.bot.send_message(chat_id=chat_id, text=text)
                else:
                    # Split into chunks
                    for i in range(0, len(text), 4096):
                        chunk = text[i:i + 4096]
                        await app.bot.send_message(chat_id=chat_id, text=chunk)
                        await asyncio.sleep(0.3)
            except Exception as e:
                logger.error(f"Failed to send Telegram message: {e}")

    def _make_command_handler(self, default_agent_id: str):
        """Create a command handler closure for a specific agent."""
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not update.message or not update.message.text:
                return

            user_id = update.effective_user.id if update.effective_user else 0
            chat_id = update.effective_chat.id if update.effective_chat else 0

            if not self._is_user_allowed(user_id, default_agent_id, "telegram"):
                await update.message.reply_text("⛔ You are not authorized to use this bot.")
                return

            command_text = update.message.text
            reply = await self.daemon.handle_system_command(
                command=command_text,
                agent_id=default_agent_id,
                source="telegram",
                chat_id=chat_id,
                user_id=user_id,
            )
            if reply:
                await self.send_message(chat_id, reply, default_agent_id)

        return handler

    def _make_message_handler(self, default_agent_id: str):
        """Create a message handler closure for a specific agent."""
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not update.message or not update.message.text:
                return

            user_id = update.effective_user.id if update.effective_user else 0
            chat_id = update.effective_chat.id if update.effective_chat else 0

            if not self._is_user_allowed(user_id, default_agent_id, "telegram"):
                await update.message.reply_text("⛔ You are not authorized to use this bot.")
                return

            text = update.message.text
            success = self.daemon.route_message(
                text=text,
                agent_id=default_agent_id,
                source="telegram",
                chat_id=chat_id,
                user_id=user_id,
            )
            if not success:
                await update.message.reply_text("⚠️ Agent is not available. Try /restart.")

        return handler

    async def _reply_dispatcher(self) -> None:
        """Poll agent outboxes and send replies via Telegram."""
        while True:
            for agent_id in list(self.applications.keys()):
                handle = self.daemon.agent_manager.get_handle(agent_id)
                if not handle:
                    continue
                try:
                    msg = handle.outbox.get_nowait()
                    if msg and msg.get("source") == "telegram":
                        chat_id = msg.get("chat_id")
                        content = msg.get("content", "")
                        if chat_id and content:
                            await self.send_message(chat_id, content, agent_id)
                except Exception:
                    pass
            await asyncio.sleep(0.2)
