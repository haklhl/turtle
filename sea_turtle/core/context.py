"""Context management with automatic compression for conversation history."""

import json
import logging
from typing import Any

logger = logging.getLogger("sea_turtle.context")


class ContextManager:
    """Manage conversation history with automatic compression.

    When token count approaches the configured threshold, older messages
    are compressed into a summary using the LLM.
    """

    def __init__(self, config: dict):
        ctx_cfg = config.get("context", {})
        self.max_tokens = ctx_cfg.get("max_tokens", 200000)
        self.compress_threshold_ratio = ctx_cfg.get("compress_threshold_ratio", 0.7)
        self.compress_target_ratio = ctx_cfg.get("compress_target_ratio", 0.3)
        self.compress_model = ctx_cfg.get("compress_model", "gemini-2.0-flash")
        self.messages: list[dict[str, str]] = []
        self.system_prompt: str = ""
        self._estimated_tokens: int = 0
        self._compression_count: int = 0

    def set_system_prompt(self, prompt: str) -> None:
        """Set the system prompt (not counted toward compression)."""
        self.system_prompt = prompt

    def add_message(self, role: str, content: str, **extra) -> None:
        """Add a message to the conversation history.

        Args:
            role: Message role ('user', 'assistant', 'tool').
            content: Message content.
            **extra: Additional fields (e.g., name, tool_use_id).
        """
        msg = {"role": role, "content": content}
        msg.update(extra)
        self.messages.append(msg)
        self._estimated_tokens += self._estimate_tokens(content)

    def get_messages(self) -> list[dict[str, str]]:
        """Get the full message list including system prompt.

        Returns:
            List of message dicts ready for LLM API call.
        """
        result = []
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        result.extend(self.messages)
        return result

    def needs_compression(self) -> bool:
        """Check if context needs compression based on token threshold."""
        threshold = int(self.max_tokens * self.compress_threshold_ratio)
        return self._estimated_tokens >= threshold

    async def compress(self, llm_provider) -> bool:
        """Compress older messages into a summary.

        Keeps the most recent messages and summarizes older ones.

        Args:
            llm_provider: LLM provider instance for generating summary.

        Returns:
            True if compression was performed.
        """
        if not self.needs_compression():
            return False

        if len(self.messages) < 4:
            return False

        target_tokens = int(self.max_tokens * self.compress_target_ratio)
        split_point = len(self.messages) // 2

        old_messages = self.messages[:split_point]
        recent_messages = self.messages[split_point:]

        summary_prompt = (
            "Summarize the following conversation concisely, preserving key facts, "
            "decisions, and context that would be needed to continue the conversation. "
            "Focus on: user requests, important results, pending items, and any commitments made.\n\n"
        )
        for msg in old_messages:
            summary_prompt += f"**{msg['role']}**: {msg['content'][:500]}\n\n"

        try:
            from sea_turtle.llm.base import LLMResponse
            response = await llm_provider.chat(
                messages=[{"role": "user", "content": summary_prompt}],
                model=self.compress_model,
                temperature=0.3,
                max_output_tokens=2000,
            )

            summary = response.content
            if summary:
                self.messages = [
                    {"role": "system", "content": f"[Compressed context summary]\n{summary}"},
                ] + recent_messages
                self._estimated_tokens = self._estimate_tokens(summary) + sum(
                    self._estimate_tokens(m["content"]) for m in recent_messages
                )
                self._compression_count += 1
                logger.info(
                    f"Context compressed (#{self._compression_count}): "
                    f"{len(old_messages)} messages summarized, "
                    f"{len(recent_messages)} recent kept, "
                    f"~{self._estimated_tokens} tokens"
                )
                return True
        except Exception as e:
            logger.error(f"Context compression failed: {e}")

        return False

    def reset(self) -> None:
        """Clear all conversation history."""
        self.messages.clear()
        self._estimated_tokens = 0

    def get_stats(self) -> dict[str, Any]:
        """Get context statistics.

        Returns:
            Dict with token count, message count, capacity info.
        """
        return {
            "message_count": len(self.messages),
            "estimated_tokens": self._estimated_tokens,
            "max_tokens": self.max_tokens,
            "usage_ratio": self._estimated_tokens / self.max_tokens if self.max_tokens > 0 else 0,
            "compression_count": self._compression_count,
            "needs_compression": self.needs_compression(),
        }

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimation (1 token ≈ 4 chars for English, 2 chars for CJK)."""
        if not text:
            return 0
        ascii_chars = sum(1 for c in text if ord(c) < 128)
        non_ascii = len(text) - ascii_chars
        return (ascii_chars // 4) + (non_ascii // 2) + 1
