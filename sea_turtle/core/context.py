"""Context management with automatic compression for conversation history."""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("sea_turtle.context")


class ContextManager:
    """Manage conversation history with automatic compression.

    When token count approaches the configured threshold, older messages
    are compressed into a summary using the LLM.
    """

    def __init__(self, config: dict, persistence_path: str | None = None):
        ctx_cfg = config.get("context", {})
        self.max_tokens = ctx_cfg.get("max_tokens", 200000)
        self.compress_threshold_ratio = ctx_cfg.get("compress_threshold_ratio", 0.7)
        self.compress_target_ratio = ctx_cfg.get("compress_target_ratio", 0.3)
        self.compress_model = ctx_cfg.get("compress_model", "gemini-2.0-flash")
        persistence_cfg = config.get("conversation_persistence", {})
        self.persistence_enabled = persistence_cfg.get("enabled", True) and bool(persistence_path)
        self.persistence_path = Path(persistence_path).expanduser() if persistence_path else None
        self.messages: list[dict[str, str]] = []
        self.system_prompt: str = ""
        self._estimated_tokens: int = 0
        self._compression_count: int = 0
        self._request_count: int = 0
        self._total_response_time_ms: int = 0
        self._last_response_time_ms: int = 0
        self._load()

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
        self._save()

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
                self._save()
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
        self._compression_count = 0
        self._save()

    def get_stats(self) -> dict[str, Any]:
        """Get context statistics.

        Returns:
            Dict with token count, message count, capacity info.
        """
        system_tokens = self._estimate_tokens(self.system_prompt)
        total_tokens = self._estimated_tokens + system_tokens
        return {
            "message_count": len(self.messages),
            "system_prompt_tokens": system_tokens,
            "message_tokens": self._estimated_tokens,
            "estimated_tokens": total_tokens,
            "max_tokens": self.max_tokens,
            "usage_ratio": total_tokens / self.max_tokens if self.max_tokens > 0 else 0,
            "compression_count": self._compression_count,
            "needs_compression": self.needs_compression(),
            "request_count": self._request_count,
            "last_response_time_ms": self._last_response_time_ms,
            "avg_response_time_ms": (
                self._total_response_time_ms / self._request_count
                if self._request_count > 0 else 0
            ),
        }

    def record_response_time(self, elapsed_ms: int) -> None:
        """Record end-to-end processing time for one completed user request."""
        if elapsed_ms < 0:
            elapsed_ms = 0
        self._request_count += 1
        self._last_response_time_ms = elapsed_ms
        self._total_response_time_ms += elapsed_ms
        self._save()

    def _load(self) -> None:
        """Load persisted context state from disk."""
        if not self.persistence_enabled or not self.persistence_path or not self.persistence_path.exists():
            return
        try:
            data = json.loads(self.persistence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        messages = data.get("messages", [])
        if isinstance(messages, list):
            self.messages = [msg for msg in messages if isinstance(msg, dict)]
        compression_count = data.get("compression_count", 0)
        if isinstance(compression_count, int) and compression_count >= 0:
            self._compression_count = compression_count
        request_count = data.get("request_count", 0)
        if isinstance(request_count, int) and request_count >= 0:
            self._request_count = request_count
        total_response_time_ms = data.get("total_response_time_ms", 0)
        if isinstance(total_response_time_ms, int) and total_response_time_ms >= 0:
            self._total_response_time_ms = total_response_time_ms
        last_response_time_ms = data.get("last_response_time_ms", 0)
        if isinstance(last_response_time_ms, int) and last_response_time_ms >= 0:
            self._last_response_time_ms = last_response_time_ms
        self._estimated_tokens = sum(self._estimate_tokens(msg.get("content", "")) for msg in self.messages)

    def _save(self) -> None:
        """Persist context state to disk."""
        if not self.persistence_enabled or not self.persistence_path:
            return
        payload = {
            "messages": self.messages,
            "compression_count": self._compression_count,
            "request_count": self._request_count,
            "total_response_time_ms": self._total_response_time_ms,
            "last_response_time_ms": self._last_response_time_ms,
        }
        try:
            self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
            self.persistence_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(f"Failed to persist context to {self.persistence_path}: {e}")

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimation (1 token ≈ 4 chars for English, 2 chars for CJK)."""
        if not text:
            return 0
        ascii_chars = sum(1 for c in text if ord(c) < 128)
        non_ascii = len(text) - ascii_chars
        return (ascii_chars // 4) + (non_ascii // 2) + 1
