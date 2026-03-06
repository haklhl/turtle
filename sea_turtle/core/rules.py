"""Rules and skills loader for agent configuration files."""

import os
from pathlib import Path

from sea_turtle.core.tasks import init_task_store, list_actionable_tasks, render_task_file


def load_rules(workspace: str) -> str:
    """Load rules.md content from agent workspace.

    Args:
        workspace: Path to agent workspace directory.

    Returns:
        Rules content string, or empty string if not found.
    """
    rules_file = os.path.join(workspace, "rules.md")
    try:
        if os.path.exists(rules_file):
            with open(rules_file, "r", encoding="utf-8") as f:
                return f.read()
    except Exception:
        pass
    return ""


def load_skills(workspace: str) -> str:
    """Load skills.md content from agent workspace.

    Args:
        workspace: Path to agent workspace directory.

    Returns:
        Skills content string, or empty string if not found.
    """
    skills_file = os.path.join(workspace, "skills.md")
    try:
        if os.path.exists(skills_file):
            with open(skills_file, "r", encoding="utf-8") as f:
                return f.read()
    except Exception:
        pass
    return ""


def load_task(workspace: str) -> str:
    """Load structured task content from agent workspace.

    Args:
        workspace: Path to agent workspace directory.

    Returns:
        JSON task content string.
    """
    try:
        return render_task_file(workspace)
    except Exception:
        pass
    return ""


def get_pending_tasks(workspace: str) -> list[str]:
    """Return list of actionable task titles from structured task store.

    Args:
        workspace: Path to agent workspace directory.

    Returns:
        List of pending task description strings.
    """
    return [task["title"] for task in list_actionable_tasks(workspace) if task.get("title")]


def init_agent_workspace(workspace: str, agent_name: str = "Turtle", human_name: str = "Human") -> None:
    """Initialize a new agent workspace with default files.

    Args:
        workspace: Path to agent workspace directory.
        agent_name: Name for the agent.
        human_name: Name for the human user.
    """
    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)

    rules_file = ws / "rules.md"
    if not rules_file.exists():
        rules_file.write_text(
            f"# Agent Rules\n\n"
            f"## Identity\n\n"
            f"- You are **{agent_name}**, a helpful personal AI assistant.\n"
            f"- You refer to the user as **{human_name}**.\n\n"
            f"## Behavior\n\n"
            f"- Be concise and direct in your responses.\n"
            f"- When executing shell commands, explain what you're doing before running them.\n"
            f"- Always ask for confirmation before performing destructive operations.\n"
            f"- Use the user's preferred language for communication.\n",
            encoding="utf-8",
        )

    skills_file = ws / "skills.md"
    if not skills_file.exists():
        skills_file.write_text(
            "# Skills\n\n"
            "<!-- Define agent-specific skills and workflows here. -->\n"
            "<!-- The agent will load these skills as reference during conversations. -->\n",
            encoding="utf-8",
        )

    memory_file = ws / "memory.md"
    if not memory_file.exists():
        memory_file.write_text("", encoding="utf-8")

    init_task_store(workspace)
