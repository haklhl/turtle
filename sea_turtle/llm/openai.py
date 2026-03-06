"""OpenAI LLM provider implementation."""

import json
from typing import Any

from openai import AsyncOpenAI

from sea_turtle.llm.base import BaseLLMProvider, LLMResponse, ToolDefinition


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API provider using the official openai SDK."""

    def __init__(self, api_key: str, base_url: str | None = None, **kwargs):
        super().__init__(api_key, **kwargs)
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    def _build_tools(self, tools: list[ToolDefinition] | None) -> list[dict] | None:
        """Convert ToolDefinition list to OpenAI's tool format."""
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    def _extract_tool_calls(self, message) -> list[dict[str, Any]]:
        """Extract tool calls from OpenAI response message."""
        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                args = {}
                if tc.function.arguments:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {"raw": tc.function.arguments}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args,
                })
        return tool_calls

    def _convert_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert internal messages to OpenAI chat-completions format."""
        converted = []
        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")
            if role == "assistant" and msg.get("tool_calls"):
                converted.append({
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc.get("arguments", {}), ensure_ascii=False),
                            },
                        }
                        for tc in msg["tool_calls"]
                    ],
                })
            elif role == "tool":
                converted.append({
                    "role": "tool",
                    "content": content,
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "name": msg.get("name", "tool"),
                })
            else:
                converted.append({"role": role, "content": content})
        return converted

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
        tools: list[ToolDefinition] | None = None,
        tool_choice: str = "auto",
        metadata: dict[str, Any] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": self._convert_messages(messages),
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }

        openai_tools = self._build_tools(tools)
        if openai_tools:
            kwargs["tools"] = openai_tools
            kwargs["tool_choice"] = tool_choice

        response = await self.client.chat.completions.create(**kwargs)

        message = response.choices[0].message
        content = message.content or ""
        tool_calls = self._extract_tool_calls(message)

        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            finish_reason=response.choices[0].finish_reason or "",
            raw_response=response,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
        metadata: dict[str, Any] | None = None,
    ):
        stream = await self.client.chat.completions.create(
            model=model,
            messages=self._convert_messages(messages),
            temperature=temperature,
            max_tokens=max_output_tokens,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
