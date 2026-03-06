"""Sea Turtle daemon — main process that manages agents, channels, and routing."""

import asyncio
import json
import logging
import math
import os
import signal
import sys
from pathlib import Path
from typing import Any

from sea_turtle.config.loader import load_config, get_agent_config, save_config
from sea_turtle.core.agent import AgentManager
from sea_turtle.core.heartbeat import Heartbeat
from sea_turtle.core.token_counter import TokenCounter
from sea_turtle.core.context import ContextManager
from sea_turtle.llm.registry import list_models, format_model_list, get_model_info, resolve_provider
from sea_turtle.utils.logger import get_daemon_logger

logger: logging.Logger | None = None


def _format_ms(elapsed_ms: float | int | None) -> str:
    """Format elapsed milliseconds for human-readable status output."""
    if elapsed_ms is None:
        return "N/A"
    try:
        value = max(0, int(elapsed_ms))
    except (TypeError, ValueError):
        return "N/A"
    if value < 1000:
        return f"{value} ms"
    seconds = value / 1000
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes = math.floor(seconds / 60)
    remaining = seconds - (minutes * 60)
    return f"{minutes}m {remaining:.1f}s"


class Daemon:
    """Main daemon process.

    Responsibilities:
    - Manage agent child processes (start/stop/restart/crash recovery)
    - Own channel listeners (Telegram/Discord)
    - Route system commands (/ prefixed) vs user messages
    - Run heartbeat checks
    - Serve CLI commands via Unix socket
    """

    def __init__(self, config: dict, config_path: str | None = None):
        self.config = config
        self.config_path = config_path
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
                workspace = str(Path(agent_cfg.get("workspace", f"~/.sea_turtle/agents/{agent_id}")).expanduser().resolve())
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
                "/effort list — List Codex reasoning efforts\n"
                "/effort [minimal|low|medium|high|xhigh] — Show or set Codex reasoning effort\n"
                "/help — Show this help"
            )

        elif cmd == "/reset":
            handle = self.agent_manager.get_handle(agent_id)
            if handle and handle.is_alive:
                self.agent_manager.send_message(agent_id, {
                    "type": "reset_context",
                    "source": source,
                    "chat_id": chat_id,
                    "user_id": user_id,
                })
                return f"✅ Context reset for {source}."
            return "⚠️ Agent is not running."

        elif cmd == "/context":
            handle = self.agent_manager.get_handle(agent_id)
            if handle and handle.is_alive:
                import uuid
                req_id = str(uuid.uuid4())
                future = asyncio.get_event_loop().create_future()
                self._pending_requests[req_id] = future
                self.agent_manager.send_message(agent_id, {
                    "type": "get_stats",
                    "request_id": req_id,
                    "source": source,
                    "chat_id": chat_id,
                    "user_id": user_id,
                })
                try:
                    resp = await asyncio.wait_for(future, timeout=10.0)
                    data = resp["data"]
                    ctx = data.get("context", {})
                    agent_cfg = get_agent_config(self.config, agent_id) or {}
                    provider = resolve_provider(
                        data.get("model", agent_cfg.get("model", self.config.get("llm", {}).get("default_model", ""))),
                        self.config.get("llm", {}).get("default_provider", "google"),
                    )
                    effort_line = ""
                    if provider == "codex":
                        effort = (
                            agent_cfg.get("codex", {}).get("reasoning_effort")
                            or self.config.get("llm", {}).get("providers", {}).get("codex", {}).get("reasoning_effort", "medium")
                        )
                        effort_line = f"\n  Reasoning Effort: {effort}"
                    return (
                        f"📊 Context Stats:\n"
                        f"  Model: {data.get('model', '?')}\n"
                        f"  Provider: {provider}{effort_line}\n"
                        f"  Messages: {ctx.get('message_count', 0)}\n"
                        f"  Requests: {ctx.get('request_count', 0)}\n"
                        f"  Last Reply: {_format_ms(ctx.get('last_response_time_ms', 0))}\n"
                        f"  Avg Reply: {_format_ms(ctx.get('avg_response_time_ms', 0))}\n"
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
                agent_cfg = get_agent_config(self.config, agent_id) or {}
                provider = resolve_provider(
                    agent_cfg.get("model", self.config.get("llm", {}).get("default_model", "")),
                    self.config.get("llm", {}).get("default_provider", "google"),
                )
                codex_cfg = agent_cfg.get("codex", {})
                codex_lines = ""
                if provider == "codex":
                    codex_lines = (
                        f"\n  Codex Sandbox: {codex_cfg.get('sandbox') or self.config.get('llm', {}).get('providers', {}).get('codex', {}).get('sandbox', 'workspace-write')}"
                        f"\n  Reasoning Effort: {codex_cfg.get('reasoning_effort') or self.config.get('llm', {}).get('providers', {}).get('codex', {}).get('reasoning_effort', 'medium')}"
                        f"\n  Timeout: {codex_cfg.get('timeout_seconds') or self.config.get('llm', {}).get('providers', {}).get('codex', {}).get('timeout_seconds', 300)}s"
                    )
                runtime_lines = ""
                if handle.is_alive:
                    req_id = None
                    try:
                        import uuid
                        req_id = str(uuid.uuid4())
                        future = asyncio.get_event_loop().create_future()
                        self._pending_requests[req_id] = future
                        self.agent_manager.send_message(agent_id, {
                            "type": "get_runtime_status",
                            "request_id": req_id,
                        })
                        resp = await asyncio.wait_for(future, timeout=5.0)
                        runtime = resp.get("data", {})
                        runtime_lines = (
                            f"\n  Requests: {runtime.get('request_count', 0)}"
                            f"\n  Errors: {runtime.get('error_count', 0)}"
                            f"\n  Last Reply: {_format_ms(runtime.get('last_processing_time_ms', 0))}"
                            f"\n  Avg Reply: {_format_ms(runtime.get('avg_processing_time_ms', 0))}"
                        )
                    except asyncio.TimeoutError:
                        runtime_lines = "\n  Timing: unavailable (status request timed out)"
                    finally:
                        if req_id:
                            self._pending_requests.pop(req_id, None)
                return (
                    f"🐢 Agent: {agent_id}\n"
                    f"  Status: {status}\n"
                    f"  Name: {agent_cfg.get('name', 'Turtle')}\n"
                    f"  Model: {agent_cfg.get('model', '?')} ({provider})\n"
                    f"  Sandbox: {agent_cfg.get('sandbox', 'confined')}\n"
                    f"  Workspace: {agent_cfg.get('workspace', '~/.sea_turtle/agents/default')}"
                    f"{codex_lines}{runtime_lines}\n"
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
                    # Persist to config file
                    if self.config_path and agent_id in self.config.get("agents", {}):
                        self.config["agents"][agent_id]["model"] = new_model
                        try:
                            save_config(self.config, self.config_path)
                            logger.info(f"Model for '{agent_id}' saved to config: {new_model}")
                        except Exception as e:
                            logger.error(f"Failed to save config: {e}")
                    return f"✅ Model switched to: {new_model}"
                return "⚠️ Agent is not running."
            else:
                return "Usage: /model list [provider] or /model <model_name>"

        elif cmd == "/effort":
            agent_cfg = get_agent_config(self.config, agent_id) or {}
            codex_cfg = agent_cfg.setdefault("codex", {})
            current_effort = codex_cfg.get("reasoning_effort") or self.config.get("llm", {}).get("providers", {}).get("codex", {}).get("reasoning_effort", "medium")
            if len(parts) >= 2 and parts[1].lower() == "list":
                return (
                    "🧠 Available Codex reasoning efforts:\n"
                    "- minimal\n- low\n- medium\n- high\n- xhigh\n"
                    f"\nCurrent: {current_effort}"
                )
            if len(parts) == 1:
                provider = resolve_provider(agent_cfg.get("model", self.config.get("llm", {}).get("default_model", "")), self.config.get("llm", {}).get("default_provider", "google"))
                note = "" if provider == "codex" else "\n⚠️ 当前模型不是 Codex，修改后要切回 Codex 模型才会生效。"
                return f"🧠 Current Codex reasoning effort: {current_effort}{note}"

            new_effort = parts[1].lower()
            if new_effort not in {"minimal", "low", "medium", "high", "xhigh"}:
                return "Usage: /effort [minimal|low|medium|high|xhigh]"

            codex_cfg["reasoning_effort"] = new_effort
            if self.config_path and agent_id in self.config.get("agents", {}):
                self.config["agents"][agent_id].setdefault("codex", {})["reasoning_effort"] = new_effort
                try:
                    save_config(self.config, self.config_path)
                except Exception as e:
                    logger.error(f"Failed to save config: {e}")

            handle = self.agent_manager.get_handle(agent_id)
            if handle and handle.is_alive:
                self.agent_manager.send_message(agent_id, {"type": "set_effort", "effort": new_effort})

            provider = resolve_provider(agent_cfg.get("model", self.config.get("llm", {}).get("default_model", "")), self.config.get("llm", {}).get("default_provider", "google"))
            note = "" if provider == "codex" else "\n⚠️ 当前模型不是 Codex，已保存，但只有切回 Codex 模型后才会生效。"
            return f"✅ Codex reasoning effort set to: {new_effort}{note}"

        return f"Unknown command: {cmd}. Type /help for available commands."

    def route_message(
        self,
        text: str,
        agent_id: str,
        source: str,
        chat_id: Any = None,
        user_id: Any = None,
        attachments: list[str] | None = None,
    ) -> bool:
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
            "attachments": attachments or [],
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
        payload = self._parse_reply_payload(content)

        if source == "telegram":
            await self._send_telegram_reply(chat_id, payload["text"], agent_id, payload["attachments"])
        elif source == "discord":
            await self._send_discord_reply(chat_id, payload["text"], agent_id)
        else:
            logger.debug(f"Reply to {source}: {payload['text'][:100]}")

    @staticmethod
    def _parse_reply_payload(content: str) -> dict[str, Any]:
        """Parse simple attachment directives from assistant text."""
        attachments = []
        text_lines = []
        for line in (content or "").splitlines():
            if line.startswith("ATTACH:"):
                path = line.split(":", 1)[1].strip()
                if path:
                    attachments.append(path)
            else:
                text_lines.append(line)
        return {"text": "\n".join(text_lines).strip(), "attachments": attachments}

    async def _send_telegram_reply(self, chat_id, content, agent_id: str, attachments: list[str] | None = None):
        """Send reply via Telegram."""
        if self._telegram_channel:
            await self._telegram_channel.stop_typing(chat_id, agent_id)
            if content:
                await self._telegram_channel.send_message(chat_id, content, agent_id)
            if attachments:
                await self._telegram_channel.send_attachments(chat_id, attachments, agent_id)
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
    from sea_turtle.config.loader import find_config_file
    resolved_path = find_config_file(config_path)
    config = load_config(config_path)
    daemon = Daemon(config, config_path=resolved_path)
    asyncio.run(daemon.start())
