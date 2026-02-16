"""Sea Turtle daemon — main process that manages agents, channels, and routing."""

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

from sea_turtle.config.loader import load_config, get_agent_config
from sea_turtle.core.agent import AgentManager
from sea_turtle.core.heartbeat import Heartbeat
from sea_turtle.core.token_counter import TokenCounter
from sea_turtle.core.context import ContextManager
from sea_turtle.llm.registry import list_models, format_model_list, get_model_info
from sea_turtle.utils.logger import get_daemon_logger

logger: logging.Logger | None = None


class Daemon:
    """Main daemon process.

    Responsibilities:
    - Manage agent child processes (start/stop/restart/crash recovery)
    - Own channel listeners (Telegram/Discord)
    - Route system commands (/ prefixed) vs user messages
    - Run heartbeat checks
    - Serve CLI commands via Unix socket
    """

    def __init__(self, config: dict):
        self.config = config
        self.agent_manager = AgentManager(config)
        self.heartbeats: dict[str, Heartbeat] = {}
        self._running = False
        self._reply_task: asyncio.Task | None = None
        self._health_task: asyncio.Task | None = None
        self._channel_tasks: list[asyncio.Task] = []
        self._telegram_channel = None
        self._discord_channel = None
        self._pending_requests: dict[str, asyncio.Future] = {}

        global logger
        logger = get_daemon_logger(config)

    async def start(self) -> None:
        """Start the daemon: agents, channels, heartbeats."""
        self._running = True
        logger.info("Sea Turtle daemon starting...")

        # Write PID file
        self._write_pid()

        # Start all configured agents
        self.agent_manager.start_all()

        # Start heartbeats for each agent
        heartbeat_cfg = self.config.get("heartbeat", {})
        if heartbeat_cfg.get("enabled", True):
            for agent_id, agent_cfg in self.config.get("agents", {}).items():
                workspace = str(Path(agent_cfg.get("workspace", f"./agents/{agent_id}")).resolve())
                hb = Heartbeat(
                    agent_id=agent_id,
                    workspace=workspace,
                    interval=heartbeat_cfg.get("interval_seconds", 300),
                    on_tasks_found=self._on_tasks_found,
                )
                self.heartbeats[agent_id] = hb
                await hb.start()

        # Start reply dispatcher
        self._reply_task = asyncio.create_task(self._dispatch_replies())

        # Start health monitor
        self._health_task = asyncio.create_task(self._health_monitor())

        # Start channels
        await self._start_channels()

        logger.info("Sea Turtle daemon started successfully")

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        # Keep running
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the daemon gracefully."""
        if not self._running:
            return
        self._running = False
        logger.info("Sea Turtle daemon stopping...")

        # Stop channels
        if self._telegram_channel:
            try:
                await self._telegram_channel.stop()
            except Exception as e:
                logger.error(f"Error stopping Telegram channel: {e}")
        if self._discord_channel:
            try:
                await self._discord_channel.stop()
            except Exception as e:
                logger.error(f"Error stopping Discord channel: {e}")
        for task in self._channel_tasks:
            task.cancel()

        # Stop heartbeats
        for hb in self.heartbeats.values():
            await hb.stop()

        # Stop reply dispatcher
        if self._reply_task:
            self._reply_task.cancel()
        if self._health_task:
            self._health_task.cancel()

        # Stop all agents
        self.agent_manager.stop_all()

        # Remove PID file
        self._remove_pid()

        logger.info("Sea Turtle daemon stopped")

    async def handle_system_command(
        self, command: str, agent_id: str, source: str = "telegram",
        chat_id: Any = None, user_id: Any = None,
    ) -> str:
        """Handle a /command from a channel.

        System commands are processed by the daemon, not forwarded to agents.

        Args:
            command: Full command string (e.g., '/reset', '/model list google').
            agent_id: Target agent ID.
            source: Channel source identifier.
            chat_id: Chat/channel ID for reply routing.
            user_id: User ID for auth.

        Returns:
            Response string.
        """
        parts = command.strip().split()
        cmd = parts[0].lower() if parts else ""

        if cmd == "/start":
            agent_cfg = get_agent_config(self.config, agent_id)
            name = agent_cfg.get("name", "Turtle") if agent_cfg else "Turtle"
            return f"🐢 Welcome! I'm {name}, your personal AI assistant.\nType /help for available commands."

        elif cmd == "/help":
            return (
                "🐢 Sea Turtle Commands:\n"
                "/reset — Reset conversation context\n"
                "/context — Show context stats\n"
                "/restart — Restart agent process\n"
                "/usage — Show token usage & costs\n"
                "/status — Show agent status\n"
                "/model list [provider] — List available models\n"
                "/model <name> — Switch model\n"
                "/help — Show this help"
            )

        elif cmd == "/reset":
            handle = self.agent_manager.get_handle(agent_id)
            if handle and handle.is_alive:
                self.agent_manager.send_message(agent_id, {"type": "reset_context"})
                return "✅ Context reset."
            return "⚠️ Agent is not running."

        elif cmd == "/context":
            handle = self.agent_manager.get_handle(agent_id)
            if handle and handle.is_alive:
                import uuid
                req_id = str(uuid.uuid4())
                future = asyncio.get_event_loop().create_future()
                self._pending_requests[req_id] = future
                self.agent_manager.send_message(agent_id, {"type": "get_stats", "request_id": req_id})
                try:
                    resp = await asyncio.wait_for(future, timeout=10.0)
                    data = resp["data"]
                    ctx = data.get("context", {})
                    return (
                        f"📊 Context Stats:\n"
                        f"  Model: {data.get('model', '?')}\n"
                        f"  Messages: {ctx.get('message_count', 0)}\n"
                        f"  System Prompt: ~{ctx.get('system_prompt_tokens', 0):,} tokens\n"
                        f"  Conversation: ~{ctx.get('message_tokens', 0):,} tokens\n"
                        f"  Total: ~{ctx.get('estimated_tokens', 0):,} / {ctx.get('max_tokens', 0):,} ({ctx.get('usage_ratio', 0):.1%})\n"
                        f"  Compressions: {ctx.get('compression_count', 0)}"
                    )
                except asyncio.TimeoutError:
                    return "⚠️ Timeout waiting for stats."
                finally:
                    self._pending_requests.pop(req_id, None)
            return "⚠️ Agent is not running."

        elif cmd == "/restart":
            try:
                self.agent_manager.restart_agent(agent_id)
                return f"✅ Agent '{agent_id}' restarted."
            except Exception as e:
                return f"❌ Failed to restart: {e}"

        elif cmd == "/usage":
            counter = TokenCounter(self.config, agent_id)
            total = counter.get_total_usage()
            return counter.format_usage(total)

        elif cmd == "/status":
            handle = self.agent_manager.get_handle(agent_id)
            if handle:
                status = "🟢 Running" if handle.is_alive else "🔴 Stopped"
                uptime_min = handle.uptime / 60
                return (
                    f"🐢 Agent: {agent_id}\n"
                    f"  Status: {status}\n"
                    f"  PID: {handle.pid or 'N/A'}\n"
                    f"  Uptime: {uptime_min:.1f} min\n"
                    f"  Restarts: {handle.restart_count}"
                )
            return f"⚠️ Agent '{agent_id}' not found."

        elif cmd == "/model":
            if len(parts) >= 2 and parts[1].lower() == "list":
                provider = parts[2].lower() if len(parts) >= 3 else None
                models = list_models(provider)
                if not models:
                    return f"No models found for provider '{provider}'." if provider else "No models found."
                return format_model_list(models)
            elif len(parts) >= 2:
                new_model = parts[1]
                handle = self.agent_manager.get_handle(agent_id)
                if handle and handle.is_alive:
                    self.agent_manager.send_message(agent_id, {"type": "set_model", "model": new_model})
                    return f"✅ Model switched to: {new_model}"
                return "⚠️ Agent is not running."
            else:
                return "Usage: /model list [provider] or /model <model_name>"

        return f"Unknown command: {cmd}. Type /help for available commands."

    def route_message(self, text: str, agent_id: str, source: str, chat_id: Any = None, user_id: Any = None) -> bool:
        """Route an incoming message to the appropriate handler.

        System commands (/ prefix) go to daemon, regular messages go to agent.

        Args:
            text: Message text.
            agent_id: Target agent.
            source: Channel source.
            chat_id: Chat ID for replies.
            user_id: User ID.

        Returns:
            True if message was routed successfully.
        """
        if text.startswith("/"):
            # System command — handled async, reply sent via outbox
            asyncio.create_task(self._handle_and_reply_command(text, agent_id, source, chat_id, user_id))
            return True

        # Regular message — forward to agent
        return self.agent_manager.send_message(agent_id, {
            "type": "message",
            "content": text,
            "source": source,
            "chat_id": chat_id,
            "user_id": user_id,
        })

    async def _handle_and_reply_command(self, command, agent_id, source, chat_id, user_id):
        """Handle system command and put reply in outbox."""
        reply = await self.handle_system_command(command, agent_id, source, chat_id, user_id)
        handle = self.agent_manager.get_handle(agent_id)
        if handle:
            handle.outbox.put({
                "type": "reply",
                "agent_id": agent_id,
                "content": reply,
                "source": source,
                "chat_id": chat_id,
                "user_id": user_id,
            })

    async def _dispatch_replies(self) -> None:
        """Dispatch replies from agent outboxes to the appropriate channels."""
        import queue as _queue
        while self._running:
            for agent_id, handle in self.agent_manager.agents.items():
                try:
                    msg = handle.outbox.get_nowait()
                except _queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"Error reading outbox for '{agent_id}': {e}")
                    continue
                if not msg:
                    continue
                try:
                    # Route stats responses to pending futures
                    req_id = msg.get("request_id")
                    if req_id and req_id in self._pending_requests:
                        future = self._pending_requests[req_id]
                        if not future.done():
                            future.set_result(msg)
                        continue
                    # Regular replies go to channels
                    logger.debug(f"Dispatching reply from '{agent_id}' to {msg.get('source')}:{msg.get('chat_id')}")
                    await self._send_reply(msg)
                except Exception as e:
                    logger.error(f"Error dispatching reply: {e}", exc_info=True)
            await asyncio.sleep(0.1)

    async def _send_reply(self, msg: dict) -> None:
        """Send a reply message to the appropriate channel."""
        source = msg.get("source", "")
        content = msg.get("content", "")
        chat_id = msg.get("chat_id")
        agent_id = msg.get("agent_id", "default")

        if source == "telegram":
            await self._send_telegram_reply(chat_id, content, agent_id)
        elif source == "discord":
            await self._send_discord_reply(chat_id, content, agent_id)
        else:
            logger.debug(f"Reply to {source}: {content[:100]}")

    async def _send_telegram_reply(self, chat_id, content, agent_id: str):
        """Send reply via Telegram."""
        if self._telegram_channel:
            await self._telegram_channel.send_message(chat_id, content, agent_id)
        else:
            logger.warning(f"Telegram channel not available, cannot send reply to {chat_id}")

    async def _send_discord_reply(self, chat_id, content, agent_id: str):
        """Send reply via Discord."""
        if self._discord_channel:
            await self._discord_channel.send_message(chat_id, content, agent_id)
        else:
            logger.warning(f"Discord channel not available, cannot send reply to {chat_id}")

    async def _health_monitor(self) -> None:
        """Periodically check agent health and recover crashed agents."""
        while self._running:
            await asyncio.sleep(30)
            restarted = self.agent_manager.recover_crashed()
            if restarted:
                logger.warning(f"Recovered crashed agents: {restarted}")

    async def _on_tasks_found(self, agent_id: str, tasks: list[str]) -> None:
        """Callback when heartbeat finds pending tasks."""
        task_list = "\n".join(f"- {t}" for t in tasks[:5])
        message = f"You have {len(tasks)} pending task(s):\n{task_list}\nPlease work on them."
        self.agent_manager.send_message(agent_id, {
            "type": "message",
            "content": message,
            "source": "heartbeat",
        })

    async def _start_channels(self) -> None:
        """Start configured communication channels."""
        # Telegram
        if self.config.get("telegram", {}).get("enabled", False):
            try:
                from sea_turtle.channels.telegram import TelegramChannel
                self._telegram_channel = TelegramChannel(self.config, self)
                await self._telegram_channel.start()
                logger.info("Telegram channel started")
            except Exception as e:
                logger.error(f"Failed to start Telegram channel: {e}", exc_info=True)
                self._telegram_channel = None

        # Discord
        if self.config.get("discord", {}).get("enabled", False):
            try:
                from sea_turtle.channels.discord import DiscordChannel
                self._discord_channel = DiscordChannel(self.config, self)
                await self._discord_channel.start()
                logger.info("Discord channel started")
            except Exception as e:
                logger.error(f"Failed to start Discord channel: {e}", exc_info=True)
                self._discord_channel = None

    def _write_pid(self) -> None:
        """Write daemon PID to file."""
        pid_file = self.config.get("global", {}).get("pid_file", "~/.sea_turtle/daemon.pid")
        pid_path = Path(pid_file).expanduser()
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(os.getpid()))

    def _remove_pid(self) -> None:
        """Remove daemon PID file."""
        pid_file = self.config.get("global", {}).get("pid_file", "~/.sea_turtle/daemon.pid")
        pid_path = Path(pid_file).expanduser()
        if pid_path.exists():
            pid_path.unlink()


def run_daemon(config_path: str | None = None) -> None:
    """Entry point to run the daemon."""
    config = load_config(config_path)
    daemon = Daemon(config)
    asyncio.run(daemon.start())
