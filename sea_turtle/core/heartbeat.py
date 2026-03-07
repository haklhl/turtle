"""Scheduler tick loop for agent-scoped recurring jobs and heartbeat."""

import asyncio
import logging
from typing import Callable, Awaitable

logger = logging.getLogger("sea_turtle.heartbeat")


class Heartbeat:
    """Periodically check agent schedules and dispatch due jobs."""

    def __init__(
        self,
        agent_id: str,
        workspace: str,
        interval: int = 300,
        on_tasks_found: Callable[[str], Awaitable[None]] | None = None,
    ):
        """Initialize heartbeat.

        Args:
            agent_id: Agent identifier.
            workspace: Path to agent workspace.
            interval: Check interval in seconds.
            on_tasks_found: Async callback for each scheduler tick.
                            Receives (agent_id).
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
        """Run one scheduler tick."""
        if self.on_tasks_found:
            await self.on_tasks_found(self.agent_id)

    @property
    def is_running(self) -> bool:
        return self._running
