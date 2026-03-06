"""Telegram Bot channel implementation."""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from telegram import BotCommand, Update
from telegram.constants import ChatAction
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
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
ALLOWED_DOCUMENT_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".txt", ".md", ".pdf", ".json", ".csv", ".log",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".yaml", ".yml",
}
DEFAULT_ATTACHMENT_RETENTION_HOURS = 24 * 7
MAX_INBOUND_ATTACHMENT_BYTES = 50 * 1024 * 1024

BOT_COMMANDS = [
    BotCommand("start", "🐢 启动 / 欢迎信息"),
    BotCommand("help", "📖 显示帮助"),
    BotCommand("reset", "🔄 重置对话上下文"),
    BotCommand("context", "📊 查看上下文用量"),
    BotCommand("tasks", "🗂️ 查看最近任务"),
    BotCommand("usage", "💰 查看 Token 用量与费用"),
    BotCommand("status", "📋 查看 Agent 状态"),
    BotCommand("model", "🤖 查看/切换模型 (如 /model gpt-4o)"),
    BotCommand("effort", "🧠 查看/切换 Codex 思考深度"),
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
        self._typing_tasks: dict[tuple[str, Any], asyncio.Task] = {}

    async def start(self) -> None:
        """Start Telegram bot(s) for all configured agents."""
        seen_tokens: dict[str, str] = {}

        for agent_id, agent_cfg in self.config.get("agents", {}).items():
            self._cleanup_old_attachments(agent_id)
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
            app.add_handler(CommandHandler("tasks", self._make_command_handler(agent_id)))
            app.add_handler(CommandHandler("usage", self._make_command_handler(agent_id)))
            app.add_handler(CommandHandler("status", self._make_command_handler(agent_id)))
            app.add_handler(CommandHandler("model", self._make_command_handler(agent_id)))
            app.add_handler(CommandHandler("effort", self._make_command_handler(agent_id)))

            # Regular messages
            app.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._make_message_handler(agent_id),
            ))
            app.add_handler(MessageHandler(
                filters.PHOTO | (filters.Document.ALL & ~filters.COMMAND),
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
                await self.stop_typing(None, agent_id)
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

    async def send_attachments(self, chat_id: Any, attachments: list[str], agent_id: str | None = None) -> None:
        """Send local image/file attachments to Telegram."""
        app = None
        if agent_id and agent_id in self.applications:
            app = self.applications[agent_id]
        elif self.applications:
            app = next(iter(self.applications.values()))

        if not app or not app.bot:
            return

        for attachment in attachments:
            path = Path(attachment).expanduser()
            if not path.exists():
                logger.warning(f"Attachment not found, skipping: {path}")
                continue
            try:
                with path.open("rb") as f:
                    if path.suffix.lower() in IMAGE_SUFFIXES:
                        await app.bot.send_photo(chat_id=chat_id, photo=f)
                    else:
                        await app.bot.send_document(chat_id=chat_id, document=f)
            except Exception as e:
                logger.error(f"Failed to send Telegram attachment '{path}': {e}")

    async def start_typing(self, chat_id: Any, agent_id: str | None = None) -> None:
        """Start a periodic Telegram typing indicator for a chat."""
        key = (agent_id or "", chat_id)
        if key in self._typing_tasks and not self._typing_tasks[key].done():
            return

        async def runner():
            while True:
                app = None
                if agent_id and agent_id in self.applications:
                    app = self.applications[agent_id]
                elif self.applications:
                    app = next(iter(self.applications.values()))
                if not app or not app.bot:
                    return
                try:
                    await app.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                except Exception as e:
                    logger.debug(f"Failed to send typing action for {agent_id}:{chat_id}: {e}")
                    return
                await asyncio.sleep(4)

        self._typing_tasks[key] = asyncio.create_task(runner())

    async def stop_typing(self, chat_id: Any | None, agent_id: str | None = None) -> None:
        """Stop a periodic Telegram typing indicator."""
        keys = []
        if chat_id is None:
            keys = [key for key in self._typing_tasks if not agent_id or key[0] == (agent_id or "")]
        else:
            keys = [(agent_id or "", chat_id)]

        for key in keys:
            task = self._typing_tasks.pop(key, None)
            if task:
                task.cancel()

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
            if not update.message:
                return

            user_id = update.effective_user.id if update.effective_user else 0
            chat_id = update.effective_chat.id if update.effective_chat else 0

            if not self._is_user_allowed(user_id, default_agent_id, "telegram"):
                await update.message.reply_text("⛔ You are not authorized to use this bot.")
                return

            text = update.message.text or update.message.caption or ""
            try:
                attachments = await self._download_attachments(update, default_agent_id)
            except ValueError as e:
                await update.message.reply_text(str(e))
                return
            if not text and attachments:
                text = "Please inspect the attached file(s)."
            success = self.daemon.route_message(
                text=text,
                agent_id=default_agent_id,
                source="telegram",
                chat_id=chat_id,
                user_id=user_id,
                attachments=attachments,
            )
            if not success:
                await update.message.reply_text("⚠️ Agent is not available. Try /restart.")
                return
            await self.start_typing(chat_id, default_agent_id)

        return handler

    async def _download_attachments(self, update: Update, agent_id: str) -> list[str]:
        """Download incoming attachments into the agent workspace."""
        if not update.message:
            return []

        self._cleanup_old_attachments(agent_id)
        media_dir = self._incoming_dir(agent_id)
        media_dir.mkdir(parents=True, exist_ok=True)

        attachments: list[str] = []
        if update.message.photo:
            photo = update.message.photo[-1]
            if photo.file_size and photo.file_size > MAX_INBOUND_ATTACHMENT_BYTES:
                raise ValueError("⚠️ 附件超过 50MB，已拒绝下载。")
            file = await photo.get_file()
            path = media_dir / f"photo_{update.update_id}_{photo.file_unique_id}.jpg"
            await file.download_to_drive(custom_path=str(path))
            attachments.append(str(path))

        document = update.message.document
        if document:
            if document.file_size and document.file_size > MAX_INBOUND_ATTACHMENT_BYTES:
                raise ValueError("⚠️ 附件超过 50MB，已拒绝下载。")
            original_name = Path(document.file_name or "attachment.bin")
            suffix = original_name.suffix or ".bin"
            if suffix.lower() not in ALLOWED_DOCUMENT_SUFFIXES:
                raise ValueError("⚠️ 当前仅允许上传常见图片、文本、代码、JSON、CSV、PDF、日志文件。")
            file = await document.get_file()
            safe_stem = original_name.stem.replace("/", "_").replace("\\", "_") or "attachment"
            path = media_dir / f"document_{update.update_id}_{document.file_unique_id}_{safe_stem}{suffix}"
            await file.download_to_drive(custom_path=str(path))
            attachments.append(str(path))

        return attachments

    def _incoming_dir(self, agent_id: str) -> Path:
        workspace = self.config.get("agents", {}).get(agent_id, {}).get("workspace", "~/.sea_turtle/agents/default")
        return Path(workspace).expanduser() / ".incoming" / "telegram"

    def _cleanup_old_attachments(self, agent_id: str) -> None:
        """Delete stale downloaded Telegram attachments from the agent workspace."""
        media_dir = self._incoming_dir(agent_id)
        if not media_dir.exists():
            return

        tg_cfg = self.config.get("telegram", {})
        retention_hours = tg_cfg.get("attachment_retention_hours", DEFAULT_ATTACHMENT_RETENTION_HOURS)
        try:
            retention_seconds = max(int(retention_hours), 1) * 3600
        except (TypeError, ValueError):
            retention_seconds = DEFAULT_ATTACHMENT_RETENTION_HOURS * 3600

        cutoff = time.time() - retention_seconds
        for path in media_dir.rglob("*"):
            try:
                if not path.is_file():
                    continue
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError as e:
                logger.warning(f"Failed to clean attachment '{path}': {e}")
