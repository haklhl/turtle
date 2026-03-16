"""Local Codex CLI provider implementation."""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from sea_turtle.llm.base import BaseLLMProvider, LLMResponse, ToolDefinition

CODEX_MODEL_ALIASES = {
    "codex-oss": None,
    "codex-cloud": None,
    "codex-5.4": "gpt-5.4",
    "codex-spark": "gpt-5.3-codex-spark",
}


class CodexProvider(BaseLLMProvider):
    """Run Codex CLI as the backing model provider.

    This provider does not use API-key chat completions. Sea Turtle owns the
    conversation state; each Codex CLI invocation is stateless.
    """

    def __init__(
        self,
        api_key: str = "",
        command: str = "codex",
        workdir: str | None = None,
        use_oss: bool = False,
        local_provider: str | None = None,
        sandbox: str = "workspace-write",
        approval_policy: str | None = None,
        profile: str | None = None,
        reasoning_effort: str | None = None,
        timeout_seconds: int = 600,
        extra_args: list[str] | None = None,
        **kwargs,
    ):
        super().__init__(api_key, **kwargs)
        self.command = command
        self.workdir = workdir
        self.use_oss = use_oss
        self.local_provider = local_provider
        self.sandbox = sandbox
        self.approval_policy = approval_policy
        self.profile = profile
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.extra_args = extra_args or []

    def _build_prompt(self, messages: list[dict[str, Any]], tools: list[ToolDefinition] | None) -> str:
        sections = [
            "You are running inside Sea Turtle as the active model backend.",
            "Use the working directory directly. Reply normally to the user.",
        ]
        if tools:
            tool_names = ", ".join(tool.name for tool in tools)
            sections.append(
                f"Sea Turtle advertised tools: {tool_names}. "
                "Codex CLI already has native shell/file abilities, so use the workspace directly instead of emitting function-call markers."
            )

        transcript = []
        for msg in messages:
            role = msg["role"].upper()
            content = msg.get("content", "")
            attachments = msg.get("attachments", []) or []
            if msg.get("tool_calls"):
                calls = ", ".join(tc["name"] for tc in msg["tool_calls"])
                transcript.append(f"{role}: {content}\nTOOL_CALLS: {calls}")
            else:
                attachment_suffix = ""
                if attachments:
                    attachment_suffix = "\nATTACHMENTS: " + ", ".join(str(path) for path in attachments)
                transcript.append(f"{role}: {content}{attachment_suffix}")

        sections.append("Conversation transcript:\n" + "\n\n".join(transcript))
        sections.append("Answer the latest user request. Do not mention internal implementation details unless asked.")
        return "\n\n".join(sections)

    def _build_command(
        self,
        prompt: str,
        model: str,
        output_file: str,
        image_paths: list[str] | None = None,
    ) -> list[str]:
        resolved_model = CODEX_MODEL_ALIASES.get(model, model)
        cmd = [self.command, "exec"]
        if self.workdir:
            cmd.extend(["--cd", self.workdir])

        if self.use_oss:
            cmd.append("--oss")
        if self.local_provider:
            cmd.extend(["--local-provider", self.local_provider])
        if self.profile:
            cmd.extend(["--profile", self.profile])
        if self.reasoning_effort:
            cmd.extend(["-c", f'model_reasoning_effort="{self.reasoning_effort}"'])
        if resolved_model:
            cmd.extend(["--model", resolved_model])
        if self.sandbox:
            cmd.extend(["--sandbox", self.sandbox])
        for image_path in image_paths or []:
            cmd.extend(["--image", image_path])
        cmd.extend(["--skip-git-repo-check", "--json", "--output-last-message", output_file])
        cmd.append("--ephemeral")
        cmd.extend(self.extra_args)
        cmd.append(prompt)
        return cmd

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
        prompt = self._build_prompt(messages, tools)
        image_paths: list[str] = []
        if metadata:
            image_paths = metadata.get("image_paths", []) or []

        with tempfile.NamedTemporaryFile(prefix="seaturtle-codex-", suffix=".txt", delete=False) as f:
            output_file = f.name

        stdout_text, stderr_text = await self._run_codex_command(
            prompt=prompt,
            model=model,
            output_file=output_file,
            image_paths=image_paths,
        )

        content = Path(output_file).read_text(encoding="utf-8").strip() if Path(output_file).exists() else ""

        input_tokens = 0
        output_tokens = 0
        for line in stdout_text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = event.get("payload", {})
            event_type = event.get("type")
            if event_type == "token_count":
                input_tokens = payload.get("input_tokens", input_tokens)
                output_tokens = payload.get("output_tokens", output_tokens)
            elif event_type == "turn.completed":
                usage = event.get("usage", {})
                input_tokens = usage.get("input_tokens", input_tokens)
                output_tokens = usage.get("output_tokens", output_tokens)

        return LLMResponse(
            content=content,
            tool_calls=[],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            finish_reason="stop",
            raw_response={"stdout": stdout_text, "stderr": stderr_text},
        )

    async def _run_codex_command(
        self,
        prompt: str,
        model: str,
        output_file: str,
        image_paths: list[str] | None = None,
    ) -> tuple[str, str]:
        cmd = self._build_command(prompt, model, output_file, image_paths=image_paths)
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise RuntimeError(
                f"命令超时（{self.timeout_seconds}秒）。建议切换到低思考深度，或改用 `codex-spark` 模型后重试。"
            ) from exc
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        if process.returncode != 0:
            raise RuntimeError(stderr_text.strip() or f"Codex CLI exited with code {process.returncode}")
        return stdout_text, stderr_text

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
        metadata: dict[str, Any] | None = None,
    ):
        response = await self.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            metadata=metadata,
        )
        if response.content:
            yield response.content
