"""Memory system for reading/writing agent memory.md files."""

import os
from datetime import datetime, timezone
from pathlib import Path


class MemoryManager:
    """Manage an agent's memory.md file.

    Memory is a simple markdown file that the agent can read and write to
    persist information across conversations.
    """

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.memory_file = os.path.join(workspace, "memory.md")

    def read(self) -> str:
        """Read the entire memory file content.

        Returns:
            Memory content string, or empty string if file doesn't exist.
        """
        try:
            if os.path.exists(self.memory_file):
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception:
            pass
        return ""

    def write(self, content: str) -> bool:
        """Overwrite the entire memory file.

        Args:
            content: New memory content.

        Returns:
            True if successful.
        """
        try:
            Path(self.memory_file).parent.mkdir(parents=True, exist_ok=True)
            with open(self.memory_file, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception:
            return False

    def append(self, entry: str) -> bool:
        """Append an entry to the memory file with timestamp.

        Args:
            entry: Memory entry text to append.

        Returns:
            True if successful.
        """
        try:
            Path(self.memory_file).parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            with open(self.memory_file, "a", encoding="utf-8") as f:
                f.write(f"\n### [{timestamp}]\n{entry}\n")
            return True
        except Exception:
            return False

    def search(self, keyword: str) -> list[str]:
        """Search memory for lines containing a keyword.

        Args:
            keyword: Search term (case-insensitive).

        Returns:
            List of matching lines.
        """
        content = self.read()
        if not content:
            return []
        keyword_lower = keyword.lower()
        return [line for line in content.splitlines() if keyword_lower in line.lower()]

    def clear(self) -> bool:
        """Clear all memory content.

        Returns:
            True if successful.
        """
        return self.write("")
