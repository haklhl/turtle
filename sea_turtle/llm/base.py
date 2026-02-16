"""Abstract base class for LLM providers."""

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    finish_reason: str = ""
    raw_response: Any = None


@dataclass
class ToolDefinition:
    """Definition of a tool/function that the LLM can call."""
    name: str
    description: str
    parameters: dict[str, Any]


class BaseLLMProvider(abc.ABC):
    """Abstract base class for LLM provider implementations.

    All providers must implement the `chat` method with a unified interface.
    """

    def __init__(self, api_key: str, **kwargs):
        """Initialize provider with API key.

        Args:
            api_key: API key for the provider.
            **kwargs: Additional provider-specific options.
        """
        self.api_key = api_key

    @abc.abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
        tools: list[ToolDefinition] | None = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            model: Model name string.
            temperature: Sampling temperature.
            max_output_tokens: Maximum tokens in response.
            tools: Optional list of tool definitions for function calling.
            tool_choice: Tool choice strategy ('auto', 'none', 'required').

        Returns:
            Standardized LLMResponse.
        """
        ...

    @abc.abstractmethod
    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
    ):
        """Send a streaming chat completion request.

        Yields:
            String chunks of the response.
        """
        ...
