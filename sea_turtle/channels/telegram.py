"""Telegram Bot channel implementation."""

import asyncio
import logging
import os
from typing import Any, TYPE_CHECKING

from telegram import BotCommand, Update
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

BOT_COMMANDS = [
    BotCommand("start", "🐢 启动 / 欢迎信息"),
    BotCommand("help", "📖 显示帮助"),
    BotCommand("reset", "🔄 重置对话上下文"),
    BotCommand("context", "📊 查看上下文用量"),
    BotCommand("usage", "💰 查看 Token 用量与费用"),
    BotCommand("status", "📋 查看 Agent 状态"),
    BotCommand("model", "🤖 查看/切换模型 (如 /model gpt-4o)"),
    BotCommand("agent", "🔀 切换 Agent (如 /agent dev)"),
    BotCommand("restart", "♻️ 重启当前 Agent"),
]


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

            from sea_turtle.config.loader import resolve_secret
            token = resolve_secret(tg_cfg, "bot_token", "bot_token_env")
            if not token:
                logger.debug(f"No Telegram token for agent '{agent_id}', skipping.")
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

            # Register command menu
            try:
                await app.bot.set_my_commands(BOT_COMMANDS)
                logger.info(f"Telegram command menu registered for agent '{agent_id}'")
            except Exception as e:
                logger.warning(f"Failed to set command menu for '{agent_id}': {e}")

            logger.info(f"Telegram bot started for agent '{agent_id}'")

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

