"""Token usage tracking and billing."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sea_turtle.llm.registry import get_pricing


class TokenCounter:
    """Track token usage and calculate costs per agent."""

    def __init__(self, config: dict, agent_id: str):
        billing_cfg = config.get("token_billing", {})
        self.enabled = billing_cfg.get("enabled", True)
        data_dir = config.get("global", {}).get("data_dir", "~/.sea_turtle")
        self.log_file = os.path.join(
            str(Path(data_dir).expanduser()), "agents", agent_id, "token_usage.json"
        )
        self.agent_id = agent_id
        self._session_usage: dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "requests": 0,
        }

    def record(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Record token usage for a single API call.

        Args:
            model: Model name used.
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.

        Returns:
            Cost in USD for this call.
        """
        if not self.enabled:
            return 0.0

        cost = 0.0
        pricing = get_pricing(model)
        if pricing:
            input_price, output_price = pricing
            cost = (input_tokens / 1_000_000 * input_price) + (output_tokens / 1_000_000 * output_price)

        self._session_usage["input_tokens"] += input_tokens
        self._session_usage["output_tokens"] += output_tokens
        self._session_usage["cost_usd"] += cost
        self._session_usage["requests"] += 1

        self._append_to_log(model, input_tokens, output_tokens, cost)
        return cost

    def get_session_usage(self) -> dict[str, Any]:
        """Get usage stats for the current session."""
        return dict(self._session_usage)

    def get_total_usage(self) -> dict[str, Any]:
        """Get total usage from the log file."""
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "requests": 0,
            "by_model": {},
        }

        try:
            if os.path.exists(self.log_file):
                with open(self.log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            totals["input_tokens"] += entry.get("input_tokens", 0)
                            totals["output_tokens"] += entry.get("output_tokens", 0)
                            totals["cost_usd"] += entry.get("cost_usd", 0.0)
                            totals["requests"] += 1

                            model = entry.get("model", "unknown")
                            if model not in totals["by_model"]:
                                totals["by_model"][model] = {
                                    "input_tokens": 0, "output_tokens": 0,
                                    "cost_usd": 0.0, "requests": 0,
                                }
                            totals["by_model"][model]["input_tokens"] += entry.get("input_tokens", 0)
                            totals["by_model"][model]["output_tokens"] += entry.get("output_tokens", 0)
                            totals["by_model"][model]["cost_usd"] += entry.get("cost_usd", 0.0)
                            totals["by_model"][model]["requests"] += 1
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass

        return totals

    def format_usage(self, usage: dict[str, Any]) -> str:
        """Format usage stats for display.

        Args:
            usage: Usage dict from get_session_usage() or get_total_usage().

        Returns:
            Formatted string.
        """
        lines = [
            f"📊 Token Usage (Agent: {self.agent_id})",
            f"  Requests: {usage['requests']}",
            f"  Input tokens: {usage['input_tokens']:,}",
            f"  Output tokens: {usage['output_tokens']:,}",
            f"  Total cost: ${usage['cost_usd']:.4f}",
        ]

        by_model = usage.get("by_model", {})
        if by_model:
            lines.append("  By model:")
            for model, stats in by_model.items():
                lines.append(
                    f"    {model}: {stats['requests']} calls, "
                    f"{stats['input_tokens']:,}+{stats['output_tokens']:,} tokens, "
                    f"${stats['cost_usd']:.4f}"
                )

        return "\n".join(lines)

    def _append_to_log(self, model: str, input_tokens: int, output_tokens: int, cost: float) -> None:
        """Append a usage entry to the JSONL log file."""
        try:
            Path(self.log_file).parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent_id": self.agent_id,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost,
            }
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass
