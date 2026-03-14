"""Rules and skills loader for agent configuration files."""

import os
from pathlib import Path

from sea_turtle.core.jobs import init_job_store
from sea_turtle.core.tasks import init_schedule_store, list_due_schedules, render_schedule_file


def _read_text_if_exists(path: Path) -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def _join_sections(parts: list[str]) -> str:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return "\n\n".join(cleaned)


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


def load_global_skills(source: str = "unknown") -> str:
    """Load project-wide skills, with optional channel-specific fragments."""

    repo_root = Path(__file__).resolve().parents[2]
    skills_dir = repo_root / "skills"
    parts = [
        _read_text_if_exists(skills_dir / "common.md"),
        _read_text_if_exists(skills_dir / f"{source}.md"),
    ]
    return _join_sections(parts)


def load_skills(workspace: str, source: str = "unknown") -> str:
    """Load global and agent skills content, with channel-specific fragments.

    Args:
        workspace: Path to agent workspace directory.
        source: Channel source, e.g. telegram/discord.

    Returns:
        Skills content string, or empty string if not found.
    """
    ws = Path(workspace)
    parts = [
        load_global_skills(source),
        _read_text_if_exists(ws / "skills.md"),
        _read_text_if_exists(ws / f"skills.{source}.md"),
    ]
    return _join_sections(parts)


def load_task(workspace: str) -> str:
    """Load structured scheduler content from agent workspace.

    Args:
        workspace: Path to agent workspace directory.

    Returns:
        JSON schedule content string.
    """
    try:
        return render_schedule_file(workspace)
    except Exception:
        pass
    return ""


def get_pending_tasks(workspace: str) -> list[str]:
    """Return list of due schedule descriptions.

    Args:
        workspace: Path to agent workspace directory.

    Returns:
        List of due schedule description strings.
    """
    return [item["description"] for item in list_due_schedules(workspace) if item.get("description")]


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

    init_schedule_store(workspace)
    init_job_store(workspace)
