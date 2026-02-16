"""Sandbox enforcement for agent processes.

Three sandbox levels:
- normal: No restrictions, agent has full user permissions.
- confined: Network allowed, filesystem restricted to workspace, no process management.
- restricted: No network, filesystem restricted to workspace, no process management.
"""

import os
import shlex
from pathlib import Path

SANDBOX_LEVELS = ("normal", "confined", "restricted")

# Commands blocked in confined mode (process management)
PROCESS_COMMANDS = {"kill", "killall", "pkill", "pgrep", "renice", "nice"}

# Commands blocked in restricted mode (network)
NETWORK_COMMANDS = {
    "curl", "wget", "nc", "ncat", "netcat", "ssh", "scp", "sftp",
    "ftp", "telnet", "ping", "traceroute", "nslookup", "dig", "host",
}

# Protected system paths (never writable in confined/restricted)
PROTECTED_PATHS = [
    "/etc/", "/sys/", "/proc/", "/boot/", "/sbin/",
    os.path.expanduser("~/.ssh/"),
    os.path.expanduser("~/.config/"),
    os.path.expanduser("~/.gnupg/"),
]


class SandboxEnforcer:
    """Enforce sandbox restrictions on shell commands and file access."""

    def __init__(self, mode: str, workspace: str):
        """Initialize sandbox enforcer.

        Args:
            mode: Sandbox mode ('normal', 'confined', 'restricted').
            workspace: Absolute path to agent workspace directory.
        """
        if mode not in SANDBOX_LEVELS:
            raise ValueError(f"Invalid sandbox mode: {mode}. Must be one of {SANDBOX_LEVELS}")
        self.mode = mode
        self.workspace = str(Path(workspace).resolve())

    def check_command(self, command: str) -> str | None:
        """Check if a command violates sandbox rules.

        Args:
            command: Shell command string.

        Returns:
            Violation description, or None if command is allowed.
        """
        if self.mode == "normal":
            return None

        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()

        if not tokens:
            return None

        base_cmds = {os.path.basename(t) for t in tokens}

        # Both confined and restricted: block process management
        blocked_procs = base_cmds & PROCESS_COMMANDS
        if blocked_procs:
            return f"Process management command not allowed in {self.mode} mode: {', '.join(blocked_procs)}"

        # Restricted only: block network commands
        if self.mode == "restricted":
            blocked_net = base_cmds & NETWORK_COMMANDS
            if blocked_net:
                return f"Network command not allowed in restricted mode: {', '.join(blocked_net)}"

        # Both confined and restricted: check path traversal
        if ".." in command:
            return "Path traversal (..) not allowed in sandbox mode."

        # Both: check protected path access
        for protected in PROTECTED_PATHS:
            if protected in command:
                return f"Access to protected path '{protected}' not allowed in sandbox mode."

        return None

    def check_file_access(self, file_path: str, write: bool = False) -> str | None:
        """Check if file access is allowed under sandbox rules.

        Args:
            file_path: Path to the file.
            write: Whether write access is requested.

        Returns:
            Violation description, or None if access is allowed.
        """
        if self.mode == "normal":
            return None

        resolved = str(Path(file_path).resolve())

        # Check if path is within workspace
        if not resolved.startswith(self.workspace):
            if write:
                return f"Write access outside workspace not allowed in {self.mode} mode: {file_path}"
            # Read access outside workspace is allowed in confined mode for non-protected paths
            if self.mode == "restricted":
                return f"File access outside workspace not allowed in restricted mode: {file_path}"

        # Check protected paths
        for protected in PROTECTED_PATHS:
            if resolved.startswith(str(Path(protected).resolve())):
                return f"Access to protected path not allowed: {file_path}"

        return None

    def get_cwd(self) -> str:
        """Get the working directory for command execution.

        Returns:
            Workspace path for confined/restricted, or current dir for normal.
        """
        if self.mode in ("confined", "restricted"):
            return self.workspace
        return os.getcwd()

    def describe(self) -> str:
        """Get a human-readable description of current sandbox restrictions."""
        if self.mode == "normal":
            return "No restrictions. Full user permissions."
        elif self.mode == "confined":
            return (
                "Confined mode:\n"
                "  ✅ Network: allowed\n"
                "  ⚠️ Filesystem: workspace only (write), read allowed outside\n"
                "  ❌ Process management: blocked\n"
                "  ❌ System files: protected"
            )
        else:
            return (
                "Restricted mode:\n"
                "  ❌ Network: blocked\n"
                "  ⚠️ Filesystem: workspace only\n"
                "  ❌ Process management: blocked\n"
                "  ❌ System files: protected"
            )
