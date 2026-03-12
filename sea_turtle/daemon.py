"""Sea Turtle daemon — main process that manages agents, channels, and routing."""

import asyncio
import json
import logging
import math
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sea_turtle.config.loader import load_config, get_agent_config, save_config
from sea_turtle.core.agent import AgentManager
from sea_turtle.core.heartbeat import Heartbeat
from sea_turtle.core.memory import MemoryManager
from sea_turtle.core.jobs import (
    ACTIVE_JOB_STATUSES,
    apply_job_step_result,
    create_job,
    expire_job_if_needed,
    get_active_job,
    get_job,
    is_job_due,
    list_job_runs,
    list_recent_jobs,
    mark_job_started,
    record_job_failure,
    request_job_cancel,
)
from sea_turtle.core.rules import load_rules, load_skills
from sea_turtle.core.stickers import pick_sticker_for_emotion
from sea_turtle.core.token_counter import TokenCounter
from sea_turtle.core.context import ContextManager
from sea_turtle.core.tasks import (
    append_heartbeat_run,
    append_schedule_run,
    is_heartbeat_due,
    is_schedule_due,
    list_schedules,
    list_recent_schedules,
    load_heartbeat_data,
    mark_heartbeat_started,
    update_schedule,
    mark_schedule_failed,
    mark_schedules_started,
)
from sea_turtle.llm.registry import list_models, format_model_list, get_model_info, resolve_provider
from sea_turtle.security.system_prompt import build_system_prompt
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

    def _is_owner_user(self, agent_id: str, source: str, user_id: Any) -> bool:
        if user_id is None:
            return False
        agent_cfg = get_agent_config(self.config, agent_id) or {}
        channel_cfg = agent_cfg.get(source, {}) if source in {"telegram", "discord"} else {}
        global_cfg = self.config.get(source, {}) if source in {"telegram", "discord"} else {}
        owners = channel_cfg.get("owner_user_ids") or global_cfg.get("default_owner_ids", [])
        return int(user_id) in [int(owner) for owner in owners]

    def _build_current_system_prompt(self, agent_id: str, source: str) -> str:
        agent_cfg = get_agent_config(self.config, agent_id) or {}
        workspace = agent_cfg.get("workspace", f"~/.sea_turtle/agents/{agent_id}")
        memory = MemoryManager(workspace)
        return build_system_prompt(
            agent_id=agent_id,
            agent_config=agent_cfg,
            shell_config=self.config.get("shell", {}),
            skills_content=load_skills(workspace),
            memory_content=memory.read(),
            rules_content=load_rules(workspace),
            channel_name=source,
        )

    @staticmethod
    def _is_explicit_job_request(text: str) -> bool:
        lowered = (text or "").strip().lower()
        signals = (
            "给你个任务",
            "交给你个任务",
            "给你一个任务",
            "交给你一个任务",
            "后台处理",
            "帮我整理一份资料",
        )
        return any(signal in lowered for signal in signals)

    @staticmethod
    def _looks_heavy_request(text: str) -> bool:
        lowered = (text or "").strip().lower()
        keywords = [
            "整理",
            "调研",
            "研究",
            "分析",
            "报告",
            "文档",
            "资料",
            "链上",
            "发我",
            "文件",
            "markdown",
            "md文档",
        ]
        matched = sum(1 for keyword in keywords if keyword in lowered)
        lines = [line for line in (text or "").splitlines() if line.strip()]
        return matched >= 3 or len(lines) >= 4 or len(text or "") >= 180

    def _should_start_background_job(self, text: str, attachments: list[str] | None = None) -> bool:
        if attachments:
            return False
        return self._is_explicit_job_request(text) or self._looks_heavy_request(text)

    @staticmethod
    def _derive_job_title(text: str) -> str:
        stripped = (text or "").strip()
        if not stripped:
            return "后台任务"
        first_line = stripped.splitlines()[0].strip()
        title = first_line[:60]
        for prefix in ("给你个任务", "给你一个任务", "交给你个任务", "交给你一个任务"):
            if title.startswith(prefix):
                title = title[len(prefix):].strip(" ：:，,")
        return title or "后台任务"

    @staticmethod
    def _format_job_status(job: dict[str, Any]) -> str:
        if not job:
            return "🧩 当前没有后台任务。"
        next_run_at = (job.get("next_run_at") or "").replace("T", " ")
        lines = [
            "🧩 Current Job:",
            f"- ID: {job.get('id', '?')}",
            f"- Status: {job.get('status', '?')}",
            f"- Title: {job.get('title', '') or '(untitled)'}",
            f"- Phase: {job.get('current_phase', '') or 'queued'}",
            f"- Progress: {job.get('progress_text', '') or '暂无进度'}",
            f"- Steps: {job.get('step_count', 0)}/{job.get('max_steps', 0)}",
            f"- Cooldown: {job.get('cooldown_seconds', 0)}s",
            f"- Consecutive Failures: {job.get('consecutive_failures', 0)}",
            f"- Consecutive Timeouts: {job.get('consecutive_timeouts', 0)}",
            f"- Retries: {job.get('retry_count', 0)}",
        ]
        if next_run_at:
            lines.append(f"- Next Run: {next_run_at}")
        if job.get("result_summary"):
            lines.append(f"- Summary: {str(job.get('result_summary'))[:300]}")
        if job.get("last_error"):
            lines.append(f"- Last Error: {str(job.get('last_error'))[:300]}")
        return "\n".join(lines)

    def _write_prompt_export(self, agent_id: str, source: str, prompt: str) -> str:
        agent_cfg = get_agent_config(self.config, agent_id) or {}
        workspace = Path(agent_cfg.get("workspace", f"~/.sea_turtle/agents/{agent_id}")).expanduser()
        export_dir = workspace / ".exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        timestamp = Path(str(int(asyncio.get_event_loop().time() * 1000)))
        export_path = export_dir / f"system-prompt-{source}-{timestamp.name}.txt"
        export_path.write_text(prompt, encoding="utf-8")
        return str(export_path)

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
            scheduler_tick_seconds = min(int(heartbeat_cfg.get("interval_seconds", 300)), 30)
            for agent_id, agent_cfg in self.config.get("agents", {}).items():
                workspace = str(Path(agent_cfg.get("workspace", f"~/.sea_turtle/agents/{agent_id}")).expanduser().resolve())
                hb = Heartbeat(
                    agent_id=agent_id,
                    workspace=workspace,
                    interval=scheduler_tick_seconds,
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
                "/prompt — Show current final system prompt (owner only)\n"
                "/heartbeat — Show heartbeat status and latest result\n"
                "/job — Show current background job status\n"
                "/job_cancel — Cancel the current background job\n"
                "/schedules — Show recent schedules\n"
                "/tasks — Alias of /schedules\n"
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

        elif cmd == "/prompt":
            if not self._is_owner_user(agent_id, source, user_id):
                return "⛔ Owner permission required."
            prompt = self._build_current_system_prompt(agent_id, source)
            export_path = self._write_prompt_export(agent_id, source, prompt)
            return (
                f"📜 Final system prompt export for `{source}`.\n"
                f"ATTACH: {export_path}"
            )

        elif cmd == "/heartbeat":
            workspace = (get_agent_config(self.config, agent_id) or {}).get("workspace", f"~/.sea_turtle/agents/{agent_id}")
            heartbeat = load_heartbeat_data(workspace)
            last_run_at = (heartbeat.get("last_run_at") or "").replace("T", " ")
            last_result = str(heartbeat.get("last_result") or "").strip()
            lines = [
                "🫀 Heartbeat:",
                f"- Status: {'enabled' if heartbeat.get('enabled') else 'disabled'}",
                f"- Interval: {heartbeat.get('interval_minutes', 60)} min",
                f"- Running: {'yes' if heartbeat.get('is_running') else 'no'}",
                f"- Run Count: {heartbeat.get('run_count', 0)}",
            ]
            if last_run_at:
                lines.append(f"- Last Run: {last_run_at}")
            if heartbeat.get("last_outcome"):
                lines.append(f"- Last Outcome: {heartbeat.get('last_outcome')}")
            if last_result:
                lines.append(f"- Last Result: {last_result[:500]}")
            return "\n".join(lines)

        elif cmd == "/job":
            workspace = (get_agent_config(self.config, agent_id) or {}).get("workspace", f"~/.sea_turtle/agents/{agent_id}")
            job = get_active_job(workspace)
            if not job:
                recent = list_recent_jobs(workspace, limit=1)
                job = recent[0] if recent else None
            return self._format_job_status(job or {})

        elif cmd == "/job_cancel":
            workspace = (get_agent_config(self.config, agent_id) or {}).get("workspace", f"~/.sea_turtle/agents/{agent_id}")
            job = get_active_job(workspace)
            if not job:
                return "🧩 当前没有可取消的后台任务。"
            updated = request_job_cancel(workspace, str(job.get("id") or ""))
            if not updated:
                return "⚠️ 取消后台任务失败。"
            if updated.get("status") == "cancelled":
                return f"✅ 已取消后台任务 {updated.get('id')}。"
            return f"⏸️ 已请求取消后台任务 {updated.get('id')}，会在当前步骤结束后停止。"

        elif cmd in {"/tasks", "/schedules"}:
            workspace = (get_agent_config(self.config, agent_id) or {}).get("workspace", f"~/.sea_turtle/agents/{agent_id}")
            schedules = list_recent_schedules(workspace, limit=20)
            if not schedules:
                return "⏰ 最近没有定时作业。"
            lines = ["⏰ Recent Schedules:"]
            for schedule in schedules:
                updated_at = (schedule.get("updated_at") or schedule.get("created_at") or "").replace("T", " ")
                title = schedule.get("description", "").strip() or "(untitled)"
                status = schedule.get("status", "enabled")
                trigger = schedule.get("trigger", {})
                trigger_text = (
                    f"every {trigger.get('seconds')}s"
                    if trigger.get("type") == "interval"
                    else f"daily {trigger.get('time')} {trigger.get('timezone', 'UTC')}"
                )
                result = schedule.get("last_result", "").strip()
                line = f"- [{status}] {schedule.get('id', '?')} {title} | {schedule.get('execution_type')} | {trigger_text}"
                if updated_at:
                    line += f" ({updated_at})"
                lines.append(line)
                if result:
                    lines.append(f"  last: {result[:160]}")
            return "\n".join(lines)

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
                workspace = agent_cfg.get("workspace", f"~/.sea_turtle/agents/{agent_id}")
                heartbeat = load_heartbeat_data(workspace)
                enabled_schedule_count = len(list_schedules(workspace, include_disabled=False))
                active_job = get_active_job(workspace)
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
                    f"  Workspace: {workspace}"
                    f"\n  Heartbeat: {'enabled' if heartbeat.get('enabled') else 'disabled'}"
                    f"\n  Heartbeat Interval: {heartbeat.get('interval_minutes', 60)} min"
                    f"\n  Enabled Schedules: {enabled_schedule_count}"
                    f"\n  Active Job: {active_job.get('id') if active_job else 'none'}"
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

        workspace = (get_agent_config(self.config, agent_id) or {}).get("workspace", f"~/.sea_turtle/agents/{agent_id}")
        if self._should_start_background_job(text, attachments):
            active_job = get_active_job(workspace)
            if active_job:
                asyncio.create_task(self._send_reply({
                    "type": "reply",
                    "agent_id": agent_id,
                    "source": source,
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "content": (
                        "🧩 当前已有后台任务在执行。\n"
                        f"{self._format_job_status(active_job)}\n\n"
                        "你可以等待它完成，或者使用 /job_cancel 先中断当前任务。"
                    ),
                }))
                return True
            job = create_job(
                workspace,
                source=source,
                chat_id=chat_id,
                user_id=user_id,
                title=self._derive_job_title(text),
                user_request=text,
            )
            asyncio.create_task(self._send_reply({
                "type": "reply",
                "agent_id": agent_id,
                "source": source,
                "chat_id": chat_id,
                "user_id": user_id,
                "content": (
                    "收到，已按后台任务接单处理。\n"
                    f"- Job: {job.get('id')}\n"
                    f"- Title: {job.get('title')}\n"
                    "- 我会分步推进，期间你可以用 /job 查看进度，或用 /job_cancel 中断当前任务。"
                ),
            }))
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
                    if msg.get("type") == "job_result":
                        await self._handle_job_result(msg)
                        continue
                    if msg.get("type") == "heartbeat_result":
                        await self._handle_heartbeat_result(msg)
                        continue
                    if msg.get("type") == "schedule_result":
                        await self._handle_schedule_result(msg)
                        continue
                    # Regular replies go to channels
                    logger.debug(f"Dispatching reply from '{agent_id}' to {msg.get('source')}:{msg.get('chat_id')}")
                    await self._send_reply(msg)
                except Exception as e:
                    logger.error(f"Error dispatching reply: {e}", exc_info=True)
            await asyncio.sleep(0.1)

    async def _handle_schedule_result(self, msg: dict) -> None:
        """Persist one scheduled run result and notify owners."""
        agent_id = msg.get("agent_id", "default")
        schedule_id = str(msg.get("schedule_id") or "").strip()
        if not schedule_id:
            logger.warning("Dropped schedule_result without schedule_id")
            return
        workspace = (get_agent_config(self.config, agent_id) or {}).get("workspace", f"~/.sea_turtle/agents/{agent_id}")
        append_schedule_run(
            workspace,
            schedule_id,
            outcome=str(msg.get("outcome") or "error"),
            summary=str(msg.get("summary") or msg.get("content") or "").strip(),
            output=str(msg.get("output") or "").strip(),
            error=str(msg.get("error") or "").strip(),
            started_at=str(msg.get("started_at") or "") or None,
        )
        await self._send_reply(msg)

    async def _handle_job_result(self, msg: dict) -> None:
        """Persist one background job step result and notify the original chat on completion."""
        agent_id = msg.get("agent_id", "default")
        workspace = (get_agent_config(self.config, agent_id) or {}).get("workspace", f"~/.sea_turtle/agents/{agent_id}")
        job_id = str(msg.get("job_id") or "").strip()
        if not job_id:
            logger.warning("Dropped job_result without job_id")
            return

        outcome = str(msg.get("outcome") or "runtime_error")
        started_at = str(msg.get("started_at") or "") or None
        if outcome == "success":
            report = msg.get("report") or {}
            job = apply_job_step_result(
                workspace,
                job_id,
                summary=str(msg.get("summary") or "").strip(),
                output=str(msg.get("output") or "").strip(),
                started_at=started_at,
                phase_after=str(report.get("current_phase") or "").strip(),
                progress_text=str(report.get("progress_text") or "").strip(),
                working_notes=report.get("working_notes") if isinstance(report.get("working_notes"), list) else [],
                artifacts_added=report.get("artifacts_added") if isinstance(report.get("artifacts_added"), list) else [],
                status=str(report.get("status") or "waiting").strip().lower(),
                cooldown_seconds=report.get("cooldown_seconds"),
                result_summary=str(report.get("result_summary") or "").strip(),
                result_file=str(report.get("result_file") or "").strip(),
            )
        else:
            error_type = "timeout" if outcome == "timeout" else (
                "parse_error" if outcome == "parse_error" else "runtime_error"
            )
            if "429" in str(msg.get("error") or "") or "rate" in str(msg.get("error") or "").lower():
                error_type = "provider_error"
            job = record_job_failure(
                workspace,
                job_id,
                error_type=error_type,
                error_text=str(msg.get("error") or msg.get("summary") or "Job step failed."),
                started_at=started_at,
            )
        if not job:
            return
        if job.get("status") in {"completed", "failed", "cancelled"}:
            content = self._format_job_status(job)
            if job.get("status") == "completed":
                content = f"✅ 后台任务已完成。\n{content}"
                result_file = str(job.get("result_file") or "").strip()
                if result_file:
                    content = f"{content}\nATTACH: {result_file}"
            elif job.get("status") == "cancelled":
                content = f"⏹️ 后台任务已取消。\n{content}"
            else:
                content = f"❌ 后台任务已停止。\n{content}"
            await self._send_reply({
                "type": "reply",
                "agent_id": agent_id,
                "source": job.get("source") or "telegram",
                "chat_id": job.get("chat_id"),
                "user_id": job.get("user_id"),
                "content": content,
            })

    async def _handle_heartbeat_result(self, msg: dict) -> None:
        """Persist one heartbeat run result and notify owners."""
        agent_id = msg.get("agent_id", "default")
        workspace = (get_agent_config(self.config, agent_id) or {}).get("workspace", f"~/.sea_turtle/agents/{agent_id}")
        append_heartbeat_run(
            workspace,
            outcome=str(msg.get("outcome") or "error"),
            summary=str(msg.get("summary") or msg.get("content") or "").strip(),
            output=str(msg.get("output") or "").strip(),
            error=str(msg.get("error") or "").strip(),
            started_at=str(msg.get("started_at") or "") or None,
        )
        await self._send_reply(msg)

    async def _send_reply(self, msg: dict) -> None:
        """Send a reply message to the appropriate channel."""
        source = msg.get("source", "")
        content = msg.get("content", "")
        chat_id = msg.get("chat_id")
        agent_id = msg.get("agent_id", "default")
        payload = self._parse_reply_payload(content)

        if source == "telegram":
            await self._send_telegram_reply(
                chat_id,
                payload["text"],
                agent_id,
                payload["attachments"],
                payload.get("sticker_emotion", ""),
            )
        elif source == "discord":
            await self._send_discord_reply(chat_id, payload["text"], agent_id)
        elif source == "heartbeat":
            logger.debug(f"Heartbeat result for '{agent_id}': {payload['text'][:200]}")
        elif source == "scheduler":
            logger.debug(f"Schedule result for '{agent_id}': {payload['text'][:200]}")
        else:
            logger.debug(f"Reply to {source}: {payload['text'][:100]}")

    @staticmethod
    def _parse_reply_payload(content: str) -> dict[str, Any]:
        """Parse simple attachment directives from assistant text."""
        attachments = []
        sticker_emotion = ""
        text_lines = []
        for line in (content or "").splitlines():
            if line.startswith("ATTACH:"):
                path = line.split(":", 1)[1].strip()
                if path:
                    attachments.append(path)
            elif line.startswith("STICKER_EMOTION:"):
                sticker_emotion = line.split(":", 1)[1].strip()
            else:
                text_lines.append(line)
        return {
            "text": "\n".join(text_lines).strip(),
            "attachments": attachments,
            "sticker_emotion": sticker_emotion,
        }

    async def _send_telegram_reply(
        self,
        chat_id,
        content,
        agent_id: str,
        attachments: list[str] | None = None,
        sticker_emotion: str = "",
    ):
        """Send reply via Telegram."""
        if self._telegram_channel:
            await self._telegram_channel.stop_typing(chat_id, agent_id)
            if content:
                await self._telegram_channel.send_message(chat_id, content, agent_id)
            if attachments:
                await self._telegram_channel.send_attachments(chat_id, attachments, agent_id)
            if sticker_emotion:
                await self._send_telegram_sticker(chat_id, agent_id, sticker_emotion)
        else:
            logger.warning(f"Telegram channel not available, cannot send reply to {chat_id}")

    async def _send_telegram_sticker(self, chat_id: Any, agent_id: str, emotion: str) -> None:
        """Send one Telegram sticker for the requested emotion, or notify if missing."""
        agent_cfg = get_agent_config(self.config, agent_id) or {}
        tg_cfg = agent_cfg.get("telegram", {})
        if not tg_cfg.get("stickers_enabled", False) or not self._telegram_channel:
            return
        workspace = agent_cfg.get("workspace", f"~/.sea_turtle/agents/{agent_id}")
        sticker = pick_sticker_for_emotion(workspace, emotion)
        if sticker:
            await self._telegram_channel.send_sticker(chat_id, sticker["file_id"], agent_id)
            return
        await self._telegram_channel.send_message(
            chat_id,
            f"⚠️ 当前缺少 `{emotion}` 情绪的 sticker，请补一张贴纸给我记住。",
            agent_id,
        )

    async def _send_discord_reply(self, chat_id, content, agent_id: str):
        """Send reply via Discord."""
        if self._discord_channel:
            await self._discord_channel.send_message(chat_id, content, agent_id)
        else:
            logger.warning(f"Discord channel not available, cannot send reply to {chat_id}")

    def _telegram_owner_ids(self, agent_id: str) -> list[int]:
        agent_cfg = get_agent_config(self.config, agent_id) or {}
        agent_tg_cfg = agent_cfg.get("telegram", {})
        global_tg_cfg = self.config.get("telegram", {})
        owners = agent_tg_cfg.get("owner_user_ids") or global_tg_cfg.get("default_owner_ids", [])
        return [int(owner) for owner in owners]

    async def _send_heartbeat_summary(self, agent_id: str, content: str) -> None:
        """Push heartbeat task summary to Telegram owners."""
        if not content or not self._telegram_channel:
            return
        owners = self._telegram_owner_ids(agent_id)
        if not owners:
            logger.info(f"No Telegram owners configured for heartbeat summary of '{agent_id}'")
            return
        summary = f"🫀 Heartbeat / {agent_id}\n{content.strip()}"
        for owner_id in owners:
            await self._telegram_channel.send_message(owner_id, summary, agent_id)

    async def _send_scheduler_summary(self, agent_id: str, content: str) -> None:
        """Push schedule execution summary to Telegram owners."""
        if not content or not self._telegram_channel:
            return
        owners = self._telegram_owner_ids(agent_id)
        if not owners:
            logger.info(f"No Telegram owners configured for scheduler summary of '{agent_id}'")
            return
        summary = f"⏰ Schedule / {agent_id}\n{content.strip()}"
        for owner_id in owners:
            await self._telegram_channel.send_message(owner_id, summary, agent_id)

    async def _health_monitor(self) -> None:
        """Periodically check agent health and recover crashed agents."""
        while self._running:
            await asyncio.sleep(30)
            restarted = self.agent_manager.recover_crashed()
            if restarted:
                logger.warning(f"Recovered crashed agents: {restarted}")

    async def _on_tasks_found(self, agent_id: str) -> None:
        """Scheduler tick: dispatch due schedules and heartbeat if needed."""
        workspace = (get_agent_config(self.config, agent_id) or {}).get("workspace", f"~/.sea_turtle/agents/{agent_id}")
        active_job = get_active_job(workspace)
        if active_job:
            active_job = expire_job_if_needed(workspace, str(active_job.get("id") or "")) or active_job
        if active_job and active_job.get("status") in ACTIVE_JOB_STATUSES:
            if is_job_due(active_job):
                started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                started_job = mark_job_started(workspace, str(active_job.get("id") or ""), started_at=started_at)
                if started_job:
                    sent = self.agent_manager.send_message(agent_id, {
                        "type": "job_run",
                        "source": "job",
                        "job": started_job,
                        "started_at": started_at,
                    })
                    if not sent:
                        record_job_failure(
                            workspace,
                            str(active_job.get("id") or ""),
                            error_type="runtime_error",
                            error_text="Agent is not running; job step could not be dispatched.",
                            started_at=started_at,
                        )
            return

        schedules = list_schedules(workspace, include_disabled=False)
        incompatible = [item for item in schedules if item.get("execution_type") != "script"]
        for schedule in incompatible:
            update_schedule(
                workspace,
                str(schedule.get("id") or ""),
                status="disabled",
            )
            logger.warning(
                "Disabled incompatible non-script schedule '%s' for agent '%s'",
                schedule.get("id"),
                agent_id,
            )
        due_schedules = [
            item for item in schedules
            if item.get("status") == "enabled"
            and not item.get("is_running")
            and item.get("execution_type") == "script"
        ]
        due_schedules = [item for item in due_schedules if is_schedule_due(item)]
        started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        started = mark_schedules_started(workspace, [task["id"] for task in due_schedules], started_at=started_at)
        for schedule in started:
            sent = self.agent_manager.send_message(agent_id, {
                "type": "schedule_run",
                "source": "scheduler",
                "schedule": schedule,
                "started_at": started_at,
            })
            if not sent:
                mark_schedule_failed(workspace, schedule["id"], error="Agent is not running; scheduled job could not be dispatched.", started_at=started_at)
        if is_heartbeat_due(workspace):
            heartbeat = mark_heartbeat_started(workspace, started_at=started_at)
            sent = self.agent_manager.send_message(agent_id, {
                "type": "heartbeat_run",
                "source": "heartbeat",
                "heartbeat": heartbeat,
                "started_at": started_at,
            })
            if not sent:
                append_heartbeat_run(
                    workspace,
                    outcome="error",
                    summary="Agent is not running; heartbeat could not be dispatched.",
                    error="Agent is not running; heartbeat could not be dispatched.",
                    started_at=started_at,
                )

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
