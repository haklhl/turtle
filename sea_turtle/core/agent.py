"""Agent process manager — manages child processes for each agent."""

import logging
import multiprocessing
import os
import time
from dataclasses import dataclass, field
from multiprocessing import Process, Queue
from pathlib import Path
from typing import Any

from sea_turtle.core.agent_worker import run_agent_worker
from sea_turtle.core.rules import init_agent_workspace
from sea_turtle.config.loader import get_agent_config

logger = logging.getLogger("sea_turtle.agent")


@dataclass
class AgentHandle:
    """Handle to a running agent child process."""
    agent_id: str
    process: Process | None = None
    inbox: Queue = field(default_factory=Queue)
    outbox: Queue = field(default_factory=Queue)
    started_at: float = 0.0
    restart_count: int = 0

    @property
    def is_alive(self) -> bool:
        return self.process is not None and self.process.is_alive()

    @property
    def pid(self) -> int | None:
        if self.process and self.process.pid:
            return self.process.pid
        return None

    @property
    def uptime(self) -> float:
        if self.started_at > 0:
            return time.time() - self.started_at
        return 0.0


class AgentManager:
    """Manage multiple agent child processes.

    Each agent runs in its own process with isolated queues for communication.
    The daemon (main process) owns the AgentManager.
    """

    def __init__(self, config: dict):
        self.config = config
        self.agents: dict[str, AgentHandle] = {}

    def start_agent(self, agent_id: str) -> AgentHandle:
        """Start an agent child process.

        Args:
            agent_id: Agent identifier (must exist in config).

        Returns:
            AgentHandle for the started agent.

        Raises:
            ValueError: If agent not found in config.
        """
        agent_cfg = get_agent_config(self.config, agent_id)
        if not agent_cfg:
            raise ValueError(f"Agent '{agent_id}' not found in configuration.")

        # Ensure workspace exists
        workspace = agent_cfg.get("workspace", f"./agents/{agent_id}")
        init_agent_workspace(
            workspace,
            agent_name=agent_cfg.get("name", "Turtle"),
            human_name=agent_cfg.get("human_name", "Human"),
        )

        # Stop existing process if any
        if agent_id in self.agents and self.agents[agent_id].is_alive:
            self.stop_agent(agent_id)

        handle = AgentHandle(agent_id=agent_id)

        process = Process(
            target=run_agent_worker,
            args=(agent_id, self.config, handle.inbox, handle.outbox),
            name=f"agent-{agent_id}",
            daemon=True,
        )
        process.start()

        handle.process = process
        handle.started_at = time.time()
        self.agents[agent_id] = handle

        logger.info(f"Agent '{agent_id}' started (pid: {process.pid})")
        return handle

    def stop_agent(self, agent_id: str) -> bool:
        """Stop an agent child process gracefully.

        Args:
            agent_id: Agent identifier.

        Returns:
            True if agent was stopped.
        """
        handle = self.agents.get(agent_id)
        if not handle:
            return False

        if handle.is_alive:
            # Send poison pill
            try:
                handle.inbox.put(None, timeout=2)
            except Exception:
                pass

            # Wait for graceful shutdown
            handle.process.join(timeout=5)

            # Force kill if still alive
            if handle.process.is_alive():
                handle.process.terminate()
                handle.process.join(timeout=3)
                if handle.process.is_alive():
                    handle.process.kill()

            logger.info(f"Agent '{agent_id}' stopped")

        return True

    def restart_agent(self, agent_id: str) -> AgentHandle:
        """Restart an agent child process.

        Args:
            agent_id: Agent identifier.

        Returns:
            New AgentHandle.
        """
        old_handle = self.agents.get(agent_id)
        restart_count = old_handle.restart_count if old_handle else 0

        self.stop_agent(agent_id)
        handle = self.start_agent(agent_id)
        handle.restart_count = restart_count + 1

        logger.info(f"Agent '{agent_id}' restarted (count: {handle.restart_count})")
        return handle

    def get_handle(self, agent_id: str) -> AgentHandle | None:
        """Get the handle for an agent."""
        return self.agents.get(agent_id)

    def send_message(self, agent_id: str, message: dict) -> bool:
        """Send a message to an agent's inbox.

        Args:
            agent_id: Target agent.
            message: Message dict to send.

        Returns:
            True if message was queued.
        """
        handle = self.agents.get(agent_id)
        if not handle or not handle.is_alive:
            return False
        try:
            handle.inbox.put(message, timeout=5)
            return True
        except Exception as e:
            logger.error(f"Failed to send message to agent '{agent_id}': {e}")
            return False

    def check_health(self) -> dict[str, dict[str, Any]]:
        """Check health of all agents.

        Returns:
            Dict mapping agent_id to status info.
        """
        status = {}
        for agent_id, handle in self.agents.items():
            status[agent_id] = {
                "alive": handle.is_alive,
                "pid": handle.pid,
                "uptime": handle.uptime,
                "restart_count": handle.restart_count,
            }
        return status

    def start_all(self) -> None:
        """Start all configured agents."""
        for agent_id in self.config.get("agents", {}):
            try:
                self.start_agent(agent_id)
            except Exception as e:
                logger.error(f"Failed to start agent '{agent_id}': {e}")

    def stop_all(self) -> None:
        """Stop all running agents."""
        for agent_id in list(self.agents.keys()):
            try:
                self.stop_agent(agent_id)
            except Exception as e:
                logger.error(f"Failed to stop agent '{agent_id}': {e}")

    def recover_crashed(self) -> list[str]:
        """Check for crashed agents and restart them.

        Returns:
            List of agent IDs that were restarted.
        """
        restarted = []
        for agent_id, handle in list(self.agents.items()):
            if not handle.is_alive and handle.started_at > 0:
                logger.warning(f"Agent '{agent_id}' crashed, restarting...")
                try:
                    self.restart_agent(agent_id)
                    restarted.append(agent_id)
                except Exception as e:
                    logger.error(f"Failed to restart crashed agent '{agent_id}': {e}")
        return restarted

    def list_agents(self) -> list[dict[str, Any]]:
        """List all configured agents with their status.

        Returns:
            List of agent info dicts.
        """
        result = []
        for agent_id, agent_cfg in self.config.get("agents", {}).items():
            handle = self.agents.get(agent_id)
            info = {
                "id": agent_id,
                "name": agent_cfg.get("name", "Turtle"),
                "model": agent_cfg.get("model", ""),
                "sandbox": agent_cfg.get("sandbox", "confined"),
                "alive": handle.is_alive if handle else False,
                "pid": handle.pid if handle else None,
                "uptime": handle.uptime if handle else 0,
                "restart_count": handle.restart_count if handle else 0,
            }
            result.append(info)
        return result
