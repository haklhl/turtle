"""Google Gemini LLM provider implementation."""

import json
from typing import Any

from google import genai
from google.genai import types

from sea_turtle.llm.base import BaseLLMProvider, LLMResponse, ToolDefinition


class GoogleProvider(BaseLLMProvider):
    """Google Gemini API provider using the official google-genai SDK."""

    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key, **kwargs)
        self.client = genai.Client(api_key=api_key)

    def _build_tools(self, tools: list[ToolDefinition] | None) -> list[types.Tool] | None:
        """Convert ToolDefinition list to Google's tool format."""
        if not tools:
            return None

        function_declarations = []
        for tool in tools:
            func_decl = types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
            )
            function_declarations.append(func_decl)

        return [types.Tool(function_declarations=function_declarations)]

    def _convert_messages(self, messages: list[dict[str, str]]) -> tuple[str | None, list[types.Content]]:
        """Convert standard messages to Google format.

        Returns:
            Tuple of (system_instruction, contents).
        """
        system_instruction = None
        contents = []

        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            if role == "system":
                system_instruction = content
            elif role == "user":
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=content)]))
            elif role == "assistant":
                contents.append(types.Content(role="model", parts=[types.Part.from_text(text=content)]))
            elif role == "tool":
                # Tool result message
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(
                        name=msg.get("name", "tool"),
                        response={"result": content},
                    )],
                ))

        return system_instruction, contents

    def _extract_tool_calls(self, response) -> list[dict[str, Any]]:
        """Extract tool calls from Google response."""
        tool_calls = []
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.function_call:
                    fc = part.function_call
                    tool_calls.append({
                        "name": fc.name,
                        "arguments": dict(fc.args) if fc.args else {},
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
        system_instruction, contents = self._convert_messages(messages)
        google_tools = self._build_tools(tools)

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        if system_instruction:
            config.system_instruction = system_instruction
        if google_tools:
            config.tools = google_tools

        response = await self.client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

        content = ""
        if response.text:
            content = response.text

        tool_calls = self._extract_tool_calls(response)

        input_tokens = 0
        output_tokens = 0
        if response.usage_metadata:
            input_tokens = response.usage_metadata.prompt_token_count or 0
            output_tokens = response.usage_metadata.candidates_token_count or 0

        finish_reason = ""
        if response.candidates and response.candidates[0].finish_reason:
            finish_reason = str(response.candidates[0].finish_reason)

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            finish_reason=finish_reason,
            raw_response=response,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
    ):
        system_instruction, contents = self._convert_messages(messages)

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        if system_instruction:
            config.system_instruction = system_instruction

        async for chunk in self.client.aio.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config,
        ):
            if chunk.text:
                yield chunk.text
