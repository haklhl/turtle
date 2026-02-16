"""Abstract base class for communication channels."""

import abc
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from sea_turtle.daemon import Daemon


class BaseChannel(abc.ABC):
    """Abstract base class for communication channels (Telegram, Discord, etc.).

    Each channel:
    - Listens for incoming messages
    - Routes system commands to daemon
    - Forwards regular messages to the appropriate agent
    - Sends replies back to the user
    """

    def __init__(self, config: dict, daemon: "Daemon"):
        self.config = config
        self.daemon = daemon

    @abc.abstractmethod
    async def start(self) -> None:
        """Start the channel listener."""
        ...

    @abc.abstractmethod
    async def stop(self) -> None:
        """Stop the channel listener."""
        ...

    @abc.abstractmethod
    async def send_message(self, chat_id: Any, text: str) -> None:
        """Send a message to a specific chat/channel.

        Args:
            chat_id: Target chat identifier.
            text: Message text to send.
        """
        ...

    def _resolve_agent_id(self, bot_token_env: str) -> str:
        """Resolve which agent a bot token belongs to.

        Args:
            bot_token_env: Environment variable name for the bot token.

        Returns:
            Agent ID, or the default agent if not found.
        """
        for agent_id, agent_cfg in self.config.get("agents", {}).items():
            for channel_type in ("telegram", "discord"):
                channel_cfg = agent_cfg.get(channel_type, {})
                if channel_cfg.get("bot_token_env") == bot_token_env:
                    return agent_id
        return self.config.get("global", {}).get("default_agent", "default")

    def _is_user_allowed(self, user_id: int, agent_id: str, channel_type: str) -> bool:
        """Check if a user is allowed to interact with an agent.

        Args:
            user_id: User's numeric ID.
            agent_id: Agent identifier.
            channel_type: 'telegram' or 'discord'.

        Returns:
            True if allowed (empty allowlist = allow all).
        """
        agent_cfg = self.config.get("agents", {}).get(agent_id, {})
        channel_cfg = agent_cfg.get(channel_type, {})
        allowed = channel_cfg.get("allowed_user_ids", [])
        if not allowed:
            return True  # Empty list = allow all
        return user_id in allowed
