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

    This provider does not use API-key chat completions. It shells out to the local
    `codex` CLI and optionally resumes a persisted Codex session per conversation.
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
        persist_sessions: bool = True,
        session_file: str | None = None,
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
        self.persist_sessions = persist_sessions
        self.session_file = Path(session_file).expanduser() if session_file else None
        self.extra_args = extra_args or []
        self._session_cache: dict[str, str] = self._load_session_cache()

    def _load_session_cache(self) -> dict[str, str]:
        """Load persisted conversation->session mappings from disk."""
        if not self.persist_sessions or not self.session_file or not self.session_file.exists():
            return {}
        try:
            data = json.loads(self.session_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _save_session_cache(self) -> None:
        """Persist conversation->session mappings to disk."""
        if not self.persist_sessions or not self.session_file:
            return
        try:
            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            self.session_file.write_text(
                json.dumps(self._session_cache, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

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
        session_id: str | None,
        image_paths: list[str] | None = None,
    ) -> list[str]:
        cmd = [self.command, "exec"]
        if session_id:
            cmd.extend(["resume", session_id])

        if self.use_oss:
            cmd.append("--oss")
        if self.local_provider:
            cmd.extend(["--local-provider", self.local_provider])
        if self.profile:
            cmd.extend(["--profile", self.profile])
        resolved_model = CODEX_MODEL_ALIASES.get(model, model)
        if resolved_model:
            cmd.extend(["--model", resolved_model])
        if self.sandbox:
            cmd.extend(["--sandbox", self.sandbox])
        for image_path in image_paths or []:
            cmd.extend(["--image", image_path])
        cmd.extend(["--skip-git-repo-check", "--json", "--output-last-message", output_file])

        if not self.persist_sessions:
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
        session_key = ""
        image_paths: list[str] = []
        if metadata:
            session_key = metadata.get("conversation_id", "")
            image_paths = metadata.get("image_paths", []) or []
        session_id = self._session_cache.get(session_key) if session_key else None

        with tempfile.NamedTemporaryFile(prefix="seaturtle-codex-", suffix=".txt", delete=False) as f:
            output_file = f.name

        cmd = self._build_command(prompt, model, output_file, session_id, image_paths=image_paths)
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")

        if process.returncode != 0:
            raise RuntimeError(stderr_text.strip() or f"Codex CLI exited with code {process.returncode}")

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
            if event_type in {"session_meta", "thread.started"} and session_key:
                session_id = (
                    payload.get("id")
                    or event.get("thread_id")
                    or payload.get("thread_id")
                    or session_id
                )
                if session_id:
                    self._session_cache[session_key] = session_id
                    self._save_session_cache()
            elif event_type == "token_count":
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
            raw_response={"stdout": stdout_text, "stderr": stderr_text, "session_id": session_id},
        )

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
