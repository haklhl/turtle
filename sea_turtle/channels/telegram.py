"""Telegram Bot channel implementation."""

import asyncio
import html
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from telegram import BotCommand, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from sea_turtle.channels.base import BaseChannel
from sea_turtle.core.stickers import register_sticker

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
TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_RENDER_CHUNK_SIZE = 3500

BOT_COMMANDS = [
    BotCommand("start", "🐢 启动 / 欢迎信息"),
    BotCommand("help", "📖 显示帮助"),
    BotCommand("reset", "🔄 重置对话上下文"),
    BotCommand("context", "📊 查看上下文用量"),
    BotCommand("prompt", "📜 查看当前 System Prompt"),
    BotCommand("tasks", "🗂️ 查看最近任务"),
    BotCommand("usage", "💰 查看 Token 用量与费用"),
    BotCommand("status", "📋 查看 Agent 状态"),
    BotCommand("model", "🤖 查看/切换模型 (如 /model gpt-4o)"),
    BotCommand("effort", "🧠 查看/切换 Codex 思考深度"),
    BotCommand("restart", "♻️ 重启当前 Agent"),
]

FENCED_CODE_RE = re.compile(r"```(?:[^\n`]*)\n?(.*?)```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
UNDERLINE_RE = re.compile(r"__([^_\n]+?)__")
SPOILER_RE = re.compile(r"\|\|([^\n|]+?)\|\|")
STRIKE_RE = re.compile(r"~~([^~\n]+?)~~")
ITALIC_STAR_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
ITALIC_UNDERSCORE_RE = re.compile(r"(?<!_)_([^_\n]+?)_(?!_)")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def _split_telegram_chunks(text: str, limit: int = TELEGRAM_RENDER_CHUNK_SIZE) -> list[str]:
    """Split raw text into Telegram-sized chunks before HTML rendering."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    paragraphs = text.split("\n\n")

    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(paragraph) <= limit:
            current = paragraph
            continue
        lines = paragraph.splitlines()
        for line in lines:
            candidate = line if not current else f"{current}\n{line}"
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                chunks.append(current)
                current = ""
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line

    if current:
        chunks.append(current)

    return chunks or [text[:limit]]


def _convert_inline_markdown_to_html(text: str) -> str:
    text = MARKDOWN_LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
    text = UNDERLINE_RE.sub(lambda m: f"<u>{m.group(1)}</u>", text)
    text = SPOILER_RE.sub(lambda m: f"<tg-spoiler>{m.group(1)}</tg-spoiler>", text)
    text = STRIKE_RE.sub(lambda m: f"<s>{m.group(1)}</s>", text)
    text = INLINE_CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    text = BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = ITALIC_STAR_RE.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    text = ITALIC_UNDERSCORE_RE.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    return text


def markdown_to_telegram_html(text: str) -> str:
    """Convert a small Markdown subset into Telegram-safe HTML."""
    placeholders: dict[str, str] = {}

    def replace_fence(match: re.Match[str]) -> str:
        key = f"CODEBLOCKTOKEN{len(placeholders)}"
        code = html.escape(match.group(1).strip("\n"))
        placeholders[key] = f"<pre><code>{code}</code></pre>"
        return key

    text = FENCED_CODE_RE.sub(replace_fence, text)
    lines = text.splitlines()
    rendered: list[str] = []
    quote_buffer: list[str] = []

    def flush_quote() -> None:
        if not quote_buffer:
            return
        quote_text = "\n".join(_convert_inline_markdown_to_html(html.escape(line)) for line in quote_buffer)
        rendered.append(f"<blockquote>{quote_text}</blockquote>")
        quote_buffer.clear()

    for raw_line in lines:
        stripped = raw_line.lstrip()
        if stripped.startswith(">"):
            content = stripped[1:].lstrip()
            quote_buffer.append(content)
            continue
        flush_quote()
        rendered.append(_convert_inline_markdown_to_html(html.escape(raw_line)))

    flush_quote()
    output = "\n".join(rendered)
    for key, value in placeholders.items():
        output = output.replace(key, value)
    return output


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
            app.add_handler(CommandHandler("prompt", self._make_command_handler(agent_id)))
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
                filters.PHOTO | filters.Sticker.ALL | (filters.Document.ALL & ~filters.COMMAND),
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
                for raw_chunk in _split_telegram_chunks(text):
                    chunk = markdown_to_telegram_html(raw_chunk)
                    if len(chunk) > TELEGRAM_TEXT_LIMIT:
                        # Fallback to escaped plain text if HTML expansion overflows.
                        chunk = html.escape(raw_chunk[:TELEGRAM_TEXT_LIMIT])
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        parse_mode=ParseMode.HTML,
                    )
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

    async def send_sticker(self, chat_id: Any, file_id: str, agent_id: str | None = None) -> None:
        """Send a Telegram sticker by file_id."""
        app = None
        if agent_id and agent_id in self.applications:
            app = self.applications[agent_id]
        elif self.applications:
            app = next(iter(self.applications.values()))

        if app and app.bot:
            try:
                await app.bot.send_sticker(chat_id=chat_id, sticker=file_id)
            except Exception as e:
                logger.error(f"Failed to send Telegram sticker: {e}")

    def _stickers_enabled(self, agent_id: str) -> bool:
        agent_cfg = self.config.get("agents", {}).get(agent_id, {})
        tg_cfg = agent_cfg.get("telegram", {})
        return bool(tg_cfg.get("stickers_enabled", False))

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
                payload = self.daemon._parse_reply_payload(reply)
                if payload["text"]:
                    await self.send_message(chat_id, payload["text"], default_agent_id)
                if payload["attachments"]:
                    await self.send_attachments(chat_id, payload["attachments"], default_agent_id)
                if payload.get("sticker_emotion"):
                    await self.daemon._send_telegram_sticker(chat_id, default_agent_id, payload["sticker_emotion"])

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

            if update.message.sticker and self._stickers_enabled(default_agent_id):
                agent_cfg = self.config.get("agents", {}).get(default_agent_id, {})
                workspace = agent_cfg.get("workspace", "~/.sea_turtle/agents/default")
                sticker = update.message.sticker
                saved = register_sticker(
                    workspace,
                    file_id=sticker.file_id,
                    file_unique_id=sticker.file_unique_id,
                    emoji=sticker.emoji,
                    set_name=sticker.set_name,
                )
                emotion = saved.get("emotion") or "未分类"
                await update.message.reply_text(f"🗂️ 已记住这个 sticker。emotion: {emotion}")
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
