"""Shell command execution with safety checks and history recording."""

import asyncio
import os
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sea_turtle.core.sandbox import SandboxEnforcer


@dataclass
class ShellResult:
    """Result of a shell command execution."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    blocked: bool = False
    needs_confirmation: bool = False


class ShellExecutor:
    """Execute shell commands with safety checks, sandbox enforcement, and history."""

    def __init__(self, config: dict, agent_id: str, workspace: str, sandbox_mode: str = "confined"):
        self.config = config.get("shell", {})
        self.agent_id = agent_id
        self.workspace = str(Path(workspace).resolve())
        self.sandbox_mode = sandbox_mode
        self.timeout = self.config.get("timeout_seconds", 30)
        self.max_output = self.config.get("max_output_chars", 10000)
        self.dangerous_commands = set(self.config.get("dangerous_commands", []))
        self.blocked_commands = self.config.get("blocked_commands", [])
        self.history_max_entries = self.config.get("history_max_entries", 10000)
        self.history_max_size = self.config.get("history_max_file_size_mb", 50) * 1024 * 1024
        self.history_record_output = self.config.get("history_record_output", True)
        self.history_output_max = self.config.get("history_output_max_chars", 500)
        self.history_file = os.path.join(workspace, ".shell_history")
        self.sandbox = SandboxEnforcer(sandbox_mode, self.workspace)

    def check_command(self, command: str) -> ShellResult | None:
        """Check if a command is safe to execute.

        Returns:
            ShellResult if command is blocked or needs confirmation, None if safe.
        """
        # Check blocked commands
        for blocked in self.blocked_commands:
            if blocked in command:
                return ShellResult(
                    command=command, exit_code=-1,
                    stdout="", stderr=f"Command blocked: contains '{blocked}'",
                    blocked=True,
                )

        # Check dangerous commands
        if self._is_dangerous(command):
            return ShellResult(
                command=command, exit_code=-1,
                stdout="", stderr="This command requires user confirmation before execution.",
                needs_confirmation=True,
            )

        violation = self.sandbox.check_command(command)
        if violation:
            return ShellResult(
                command=command, exit_code=-1,
                stdout="", stderr=f"Sandbox violation: {violation}",
                blocked=True,
            )

        return None

    def _is_dangerous(self, command: str) -> bool:
        """Check if command contains dangerous commands."""
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()

        for token in tokens:
            base_cmd = os.path.basename(token)
            if base_cmd in self.dangerous_commands:
                return True
        return False

    async def execute(self, command: str) -> ShellResult:
        """Execute a shell command asynchronously.

        Args:
            command: Shell command string to execute.

        Returns:
            ShellResult with output and exit code.
        """
        # Safety check first
        check = self.check_command(command)
        if check:
            self._record_history(check)
            return check

        # Determine working directory
        cwd = self.sandbox.get_cwd()

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=os.environ.copy(),
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout
                )
                timed_out = False
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                stdout_bytes = b""
                stderr_bytes = f"Command timed out after {self.timeout} seconds.".encode()
                timed_out = True

            stdout = stdout_bytes.decode("utf-8", errors="replace")[:self.max_output]
            stderr = stderr_bytes.decode("utf-8", errors="replace")[:self.max_output]

            result = ShellResult(
                command=command,
                exit_code=process.returncode or -1 if timed_out else process.returncode or 0,
                stdout=stdout,
                stderr=stderr,
                timed_out=timed_out,
            )

        except Exception as e:
            result = ShellResult(
                command=command, exit_code=-1,
                stdout="", stderr=f"Execution error: {e}",
            )

        self._record_history(result)
        return result

    def _record_history(self, result: ShellResult) -> None:
        """Record command execution to .shell_history file."""
        try:
            Path(self.history_file).parent.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            entry_lines = [f"[{timestamp}] $ {result.command}"]
            entry_lines.append(f"exit_code: {result.exit_code}")

            if result.blocked:
                entry_lines.append(f"blocked: {result.stderr}")
            elif result.needs_confirmation:
                entry_lines.append("status: needs_confirmation")
            elif self.history_record_output:
                if result.stdout:
                    truncated = result.stdout[:self.history_output_max]
                    entry_lines.append(f"stdout: {truncated}")
                if result.stderr:
                    truncated = result.stderr[:self.history_output_max]
                    entry_lines.append(f"stderr: {truncated}")

            entry_lines.append("---")
            entry = "\n".join(entry_lines) + "\n"

            # Append to history file
            with open(self.history_file, "a", encoding="utf-8") as f:
                f.write(entry)

            # Truncate if file too large
            self._truncate_history_if_needed()

        except Exception:
            pass  # Don't let history recording break command execution

    def _truncate_history_if_needed(self) -> None:
        """Truncate history file if it exceeds size limit."""
        try:
            file_size = os.path.getsize(self.history_file)
            if file_size > self.history_max_size:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                # Keep the last 2/3 of lines
                keep_from = len(lines) // 3
                with open(self.history_file, "w", encoding="utf-8") as f:
                    f.writelines(lines[keep_from:])
        except Exception:
            pass
