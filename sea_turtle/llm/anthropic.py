"""Anthropic Claude LLM provider implementation."""

import json
from typing import Any

from anthropic import AsyncAnthropic

from sea_turtle.llm.base import BaseLLMProvider, LLMResponse, ToolDefinition


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude API provider using the official anthropic SDK."""

    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key, **kwargs)
        self.client = AsyncAnthropic(api_key=api_key)

    def _build_tools(self, tools: list[ToolDefinition] | None) -> list[dict] | None:
        """Convert ToolDefinition list to Anthropic's tool format."""
        if not tools:
            return None
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]

    def _extract_messages(self, messages: list[dict[str, str]]) -> tuple[str, list[dict]]:
        """Separate system message from conversation messages.

        Returns:
            Tuple of (system_prompt, conversation_messages).
        """
        system_prompt = ""
        conversation = []

        for msg in messages:
            if msg["role"] == "system":
                system_prompt = msg["content"]
            else:
                role = msg["role"]
                if role == "tool":
                    conversation.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.get("tool_use_id", ""),
                                "content": msg["content"],
                            }
                        ],
                    })
                else:
                    conversation.append({"role": role, "content": msg["content"]})

        return system_prompt, conversation

    def _extract_tool_calls(self, response) -> list[dict[str, Any]]:
        """Extract tool calls from Anthropic response."""
        tool_calls = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input if isinstance(block.input, dict) else {},
                })
        return tool_calls

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
        tools: list[ToolDefinition] | None = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        system_prompt, conversation = self._extract_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": conversation,
            "max_tokens": max_output_tokens,
            "temperature": temperature,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        anthropic_tools = self._build_tools(tools)
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
            if tool_choice == "required":
                kwargs["tool_choice"] = {"type": "any"}
            elif tool_choice == "none":
                pass  # Don't send tools
            else:
                kwargs["tool_choice"] = {"type": "auto"}

        response = await self.client.messages.create(**kwargs)

        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text

        tool_calls = self._extract_tool_calls(response)

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=model,
            finish_reason=response.stop_reason or "",
            raw_response=response,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
    ):
        system_prompt, conversation = self._extract_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": conversation,
            "max_tokens": max_output_tokens,
            "temperature": temperature,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        async with self.client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
