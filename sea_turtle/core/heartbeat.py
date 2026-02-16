"""Heartbeat system for periodic task.md checking."""

import asyncio
import logging
from typing import Callable, Awaitable

from sea_turtle.core.rules import get_pending_tasks

logger = logging.getLogger("sea_turtle.heartbeat")


class Heartbeat:
    """Periodically check agent task.md for pending tasks.

    When pending tasks are found, notifies the agent to process them.
    When no tasks exist, the heartbeat sleeps to conserve resources.
    """

    def __init__(
        self,
        agent_id: str,
        workspace: str,
        interval: int = 300,
        on_tasks_found: Callable[[str, list[str]], Awaitable[None]] | None = None,
    ):
        """Initialize heartbeat.

        Args:
            agent_id: Agent identifier.
            workspace: Path to agent workspace.
            interval: Check interval in seconds.
            on_tasks_found: Async callback when pending tasks are found.
                            Receives (agent_id, list_of_tasks).
        """
        self.agent_id = agent_id
        self.workspace = workspace
        self.interval = interval
        self.on_tasks_found = on_tasks_found
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the heartbeat loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"Heartbeat started for agent '{self.agent_id}' (interval: {self.interval}s)")

    async def stop(self) -> None:
        """Stop the heartbeat loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info(f"Heartbeat stopped for agent '{self.agent_id}'")

    async def _loop(self) -> None:
        """Main heartbeat loop."""
        while self._running:
            try:
                await self._check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error for agent '{self.agent_id}': {e}")

            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break

    async def _check(self) -> None:
        """Check for pending tasks."""
        pending = get_pending_tasks(self.workspace)
        if pending:
            logger.info(f"Agent '{self.agent_id}' has {len(pending)} pending task(s)")
            if self.on_tasks_found:
                await self.on_tasks_found(self.agent_id, pending)
        else:
            logger.debug(f"Agent '{self.agent_id}' has no pending tasks, resting.")

    @property
    def is_running(self) -> bool:
        return self._running
