"""Agent worker — runs inside a child process, handles LLM conversation loop."""

import asyncio
import json
import logging
import os
import signal
from multiprocessing import Queue
from pathlib import Path
from typing import Any

from sea_turtle.config.loader import load_config, get_agent_config
from sea_turtle.core.context import ContextManager
from sea_turtle.core.memory import MemoryManager
from sea_turtle.core.rules import load_rules, load_skills
from sea_turtle.core.shell import ShellExecutor, ShellResult
from sea_turtle.core.token_counter import TokenCounter
from sea_turtle.llm.base import BaseLLMProvider, LLMResponse, ToolDefinition
from sea_turtle.llm.registry import resolve_provider
from sea_turtle.security.system_prompt import build_system_prompt
from sea_turtle.utils.logger import get_agent_logger


# Tool definitions for function calling
SHELL_TOOL = ToolDefinition(
    name="execute_shell",
    description="Execute a shell command on the local system. Returns stdout, stderr, and exit code.",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
        },
        "required": ["command"],
    },
)

MEMORY_READ_TOOL = ToolDefinition(
    name="read_memory",
    description="Read the agent's persistent memory file.",
    parameters={
        "type": "object",
        "properties": {},
    },
)

MEMORY_WRITE_TOOL = ToolDefinition(
    name="write_memory",
    description="Write or append to the agent's persistent memory file.",
    parameters={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Content to write to memory.",
            },
            "mode": {
                "type": "string",
                "enum": ["overwrite", "append"],
                "description": "Write mode: 'overwrite' replaces all content, 'append' adds to the end.",
            },
        },
        "required": ["content"],
    },
)

TASK_READ_TOOL = ToolDefinition(
    name="read_tasks",
    description="Read the agent's task list from task.md.",
    parameters={
        "type": "object",
        "properties": {},
    },
)

ALL_TOOLS = {
    "shell": [SHELL_TOOL],
    "memory": [MEMORY_READ_TOOL, MEMORY_WRITE_TOOL],
    "task": [TASK_READ_TOOL],
}


def _create_llm_provider(config: dict, model: str) -> BaseLLMProvider:
    """Create the appropriate LLM provider based on model name and config."""
    from sea_turtle.config.loader import resolve_secret

    provider_name = resolve_provider(model, config.get("llm", {}).get("default_provider", "google"))
    providers_cfg = config.get("llm", {}).get("providers", {})
    provider_cfg = providers_cfg.get(provider_name, {})
    api_key = resolve_secret(provider_cfg, "api_key", "api_key_env")

    if not api_key:
        raise ValueError(
            f"API key not found for provider '{provider_name}'. "
            f"Set 'api_key' in config.json or env var '{provider_cfg.get('api_key_env', '')}'."
        )

    if provider_name == "google":
        from sea_turtle.llm.google import GoogleProvider
        return GoogleProvider(api_key=api_key)
    elif provider_name == "openai":
        from sea_turtle.llm.openai import OpenAIProvider
        return OpenAIProvider(api_key=api_key)
    elif provider_name == "anthropic":
        from sea_turtle.llm.anthropic import AnthropicProvider
        return AnthropicProvider(api_key=api_key)
    elif provider_name == "openrouter":
        from sea_turtle.llm.openrouter import OpenRouterProvider
        return OpenRouterProvider(api_key=api_key)
    elif provider_name == "xai":
        from sea_turtle.llm.xai import XAIProvider
        return XAIProvider(api_key=api_key)
    else:
        raise ValueError(f"Unknown provider: {provider_name}")


class AgentWorker:
    """Agent worker that runs the LLM conversation loop.

    Communicates with the daemon via multiprocessing Queues.
    """

    def __init__(
        self,
        agent_id: str,
        config: dict,
        inbox: Queue,
        outbox: Queue,
    ):
        self.agent_id = agent_id
        self.config = config
        self.inbox = inbox    # Messages from daemon -> agent
        self.outbox = outbox  # Messages from agent -> daemon
        self.agent_config = get_agent_config(config, agent_id) or {}
        self.model = self.agent_config.get("model", config.get("llm", {}).get("default_model", "gemini-2.5-flash"))
        self.workspace = str(Path(self.agent_config.get("workspace", f"./agents/{agent_id}")).resolve())
        self.logger = get_agent_logger(agent_id, config)
        self.context = ContextManager(config)
        self.memory = MemoryManager(self.workspace)
        self.token_counter = TokenCounter(config, agent_id)
        self.shell = ShellExecutor(
            config, agent_id, self.workspace,
            sandbox_mode=self.agent_config.get("sandbox", "confined"),
        )
        self.llm: BaseLLMProvider | None = None
        self._running = False

    def _get_tools(self) -> list[ToolDefinition]:
        """Get tool definitions based on agent config."""
        enabled_tools = self.agent_config.get("tools", ["shell", "memory", "task"])
        tools = []
        for tool_name in enabled_tools:
            if tool_name in ALL_TOOLS:
                tools.extend(ALL_TOOLS[tool_name])
        return tools

    async def _handle_tool_call(self, name: str, arguments: dict) -> str:
        """Execute a tool call and return the result string."""
        self.logger.info(f"Tool call: {name}({json.dumps(arguments, ensure_ascii=False)[:200]})")

        if name == "execute_shell":
            command = arguments.get("command", "")
            result = await self.shell.execute(command)
            if result.needs_confirmation:
                return (
                    f"⚠️ This command requires user confirmation: `{command}`\n"
                    "Please ask the user to confirm before executing."
                )
            output = ""
            if result.stdout:
                output += f"stdout:\n{result.stdout}\n"
            if result.stderr:
                output += f"stderr:\n{result.stderr}\n"
            output += f"exit_code: {result.exit_code}"
            return output

        elif name == "read_memory":
            content = self.memory.read()
            return content if content else "(memory is empty)"

        elif name == "write_memory":
            content = arguments.get("content", "")
            mode = arguments.get("mode", "append")
            if mode == "overwrite":
                success = self.memory.write(content)
            else:
                success = self.memory.append(content)
            return "Memory updated." if success else "Failed to update memory."

        elif name == "read_tasks":
            from sea_turtle.core.rules import load_task
            content = load_task(self.workspace)
            return content if content else "(no tasks)"

        return f"Unknown tool: {name}"

    async def _process_message(self, user_message: str) -> str:
        """Process a user message through the LLM with tool calling loop."""
        if not self.llm:
            self.llm = _create_llm_provider(self.config, self.model)

        # Build system prompt
        rules_content = load_rules(self.workspace)
        skills_content = load_skills(self.workspace)
        memory_content = self.memory.read()
        system_prompt = build_system_prompt(
            agent_id=self.agent_id,
            agent_config=self.agent_config,
            shell_config=self.config.get("shell", {}),
            skills_content=skills_content,
            memory_content=memory_content,
            rules_content=rules_content,
        )
        self.context.set_system_prompt(system_prompt)
        self.context.add_message("user", user_message)

        # Check if compression needed
        if self.context.needs_compression():
            await self.context.compress(self.llm)

        tools = self._get_tools()
        max_tool_rounds = 10

        for _ in range(max_tool_rounds):
            messages = self.context.get_messages()
            response = await self.llm.chat(
                messages=messages,
                model=self.model,
                temperature=self.config.get("llm", {}).get("temperature", 0.7),
                max_output_tokens=self.config.get("llm", {}).get("max_output_tokens", 8192),
                tools=tools if tools else None,
            )

            # Record token usage
            self.token_counter.record(self.model, response.input_tokens, response.output_tokens)

            # If no tool calls, return the text response
            if not response.tool_calls:
                if response.content:
                    self.context.add_message("assistant", response.content)
                return response.content

            # Handle tool calls
            # First add the assistant message with tool calls indication
            tool_call_desc = ", ".join(tc["name"] for tc in response.tool_calls)
            assistant_msg = response.content or f"[Calling tools: {tool_call_desc}]"
            self.context.add_message("assistant", assistant_msg)

            for tc in response.tool_calls:
                result = await self._handle_tool_call(tc["name"], tc.get("arguments", {}))
                self.context.add_message("tool", result, name=tc["name"])

        return "Maximum tool call rounds reached. Please try again."

    async def run(self) -> None:
        """Main agent worker loop. Reads from inbox, processes, writes to outbox."""
        self._running = True
        self.logger.info(f"Agent worker '{self.agent_id}' started (model: {self.model})")

        while self._running:
            try:
                # Non-blocking check with timeout
                try:
                    msg = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: self.inbox.get(timeout=1.0)
                    )
                except Exception:
                    continue

                if msg is None:
                    # Poison pill — shutdown signal
                    self.logger.info(f"Agent worker '{self.agent_id}' received shutdown signal")
                    break

                msg_type = msg.get("type", "")
                if msg_type == "message":
                    user_text = msg.get("content", "")
                    source = msg.get("source", "unknown")
                    self.logger.info(f"Processing message from {source}: {user_text[:100]}...")

                    try:
                        self.logger.info(f"Calling LLM for message from {source}...")
                        reply = await self._process_message(user_text)
                        self.logger.info(f"LLM reply received ({len(reply) if reply else 0} chars), sending to outbox")
                        self.outbox.put({
                            "type": "reply",
                            "agent_id": self.agent_id,
                            "content": reply or "(empty response)",
                            "source": source,
                            "chat_id": msg.get("chat_id"),
                            "user_id": msg.get("user_id"),
                        })
                        self.logger.info(f"Reply queued to outbox for {source}:{msg.get('chat_id')}")
                    except Exception as e:
                        self.logger.error(f"Error processing message: {e}", exc_info=True)
                        self.outbox.put({
                            "type": "reply",
                            "agent_id": self.agent_id,
                            "content": f"❌ Error: {e}",
                            "source": source,
                            "chat_id": msg.get("chat_id"),
                            "user_id": msg.get("user_id"),
                        })

                elif msg_type == "set_model":
                    new_model = msg.get("model", "")
                    self.model = new_model
                    self.llm = None  # Force re-creation
                    self.context.add_message("system", f"[System] Model switched to: {new_model}. You are now running as {new_model}.")
                    self.logger.info(f"Model changed to: {new_model}")

                elif msg_type == "reset_context":
                    self.context.reset()
                    self.logger.info("Context reset")

                elif msg_type == "get_stats":
                    stats = {
                        "context": self.context.get_stats(),
                        "token_usage": self.token_counter.get_session_usage(),
                        "model": self.model,
                    }
                    self.outbox.put({
                        "type": "stats",
                        "agent_id": self.agent_id,
                        "data": stats,
                        "request_id": msg.get("request_id"),
                    })

            except Exception as e:
                self.logger.error(f"Agent worker error: {e}", exc_info=True)

        self._running = False
        self.logger.info(f"Agent worker '{self.agent_id}' stopped")

    def stop(self) -> None:
        """Signal the worker to stop."""
        self._running = False


def run_agent_worker(agent_id: str, config: dict, inbox: Queue, outbox: Queue) -> None:
    """Entry point for agent child process.

    This function is called by multiprocessing.Process.
    """
    worker = AgentWorker(agent_id, config, inbox, outbox)
    asyncio.run(worker.run())
