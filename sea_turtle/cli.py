"""Sea Turtle CLI — `seaturtle` command-line interface."""

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from sea_turtle import __version__, __project__


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="seaturtle",
        description=f"🐢 {__project__} v{__version__} — Lightweight personal AI agent system",
    )
    parser.add_argument("--version", action="version", version=f"{__project__} {__version__}")
    parser.add_argument("--config", "-c", help="Path to config file", default=None)

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- Service management ---
    subparsers.add_parser("start", help="Start the daemon")
    subparsers.add_parser("stop", help="Stop the daemon")
    subparsers.add_parser("status", help="Show daemon and agent status")

    logs_parser = subparsers.add_parser("logs", help="View logs")
    logs_parser.add_argument("agent_id", nargs="?", help="Agent ID (optional)")
    logs_parser.add_argument("--follow", "-f", action="store_true", help="Follow log output")

    # --- Agent management ---
    agent_parser = subparsers.add_parser("agent", help="Agent management")
    agent_sub = agent_parser.add_subparsers(dest="agent_command")

    agent_sub.add_parser("list", help="List all agents")

    agent_add = agent_sub.add_parser("add", help="Create a new agent")
    agent_add.add_argument("id", help="Agent ID")
    agent_add.add_argument("--name", help="Agent display name", default=None)
    agent_add.add_argument("--model", help="LLM model", default=None)
    agent_add.add_argument("--sandbox", choices=["normal", "confined", "restricted"], default="confined")

    agent_del = agent_sub.add_parser("del", help="Delete an agent")
    agent_del.add_argument("id", help="Agent ID to delete")
    agent_del.add_argument("--force", action="store_true", help="Skip confirmation")

    agent_start = agent_sub.add_parser("start", help="Start an agent")
    agent_start.add_argument("id", help="Agent ID")

    agent_stop = agent_sub.add_parser("stop", help="Stop an agent")
    agent_stop.add_argument("id", help="Agent ID")

    agent_restart = agent_sub.add_parser("restart", help="Restart an agent")
    agent_restart.add_argument("id", help="Agent ID")

    agent_info = agent_sub.add_parser("info", help="Show agent details")
    agent_info.add_argument("id", help="Agent ID")

    # --- Model management ---
    model_parser = subparsers.add_parser("model", help="Model management")
    model_sub = model_parser.add_subparsers(dest="model_command")

    model_list = model_sub.add_parser("list", help="List available models")
    model_list.add_argument("provider", nargs="?", help="Filter by provider (google/openai/anthropic/xai)")

    model_set = model_sub.add_parser("set", help="Set agent model")
    model_set.add_argument("agent_id", help="Agent ID")
    model_set.add_argument("model_name", help="Model name")

    # --- Config ---
    config_parser = subparsers.add_parser("config", help="Configuration management")
    config_sub = config_parser.add_subparsers(dest="config_command")
    config_sub.add_parser("show", help="Show current config")
    config_sub.add_parser("edit", help="Open config in editor")
    config_sub.add_parser("validate", help="Validate config file")

    # --- Install & Update ---
    subparsers.add_parser("install-service", help="Register as system service")
    subparsers.add_parser("uninstall-service", help="Remove system service")
    subparsers.add_parser("doctor", help="Check environment and dependencies")
    subparsers.add_parser("onboard", help="Interactive setup wizard")

    update_parser = subparsers.add_parser("update", help="Check for and install updates")
    update_parser.add_argument("--check", action="store_true", help="Only check, don't install")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Dispatch commands
    try:
        if args.command == "start":
            cmd_start(args)
        elif args.command == "stop":
            cmd_stop(args)
        elif args.command == "status":
            cmd_status(args)
        elif args.command == "logs":
            cmd_logs(args)
        elif args.command == "agent":
            cmd_agent(args)
        elif args.command == "model":
            cmd_model(args)
        elif args.command == "config":
            cmd_config(args)
        elif args.command == "install-service":
            cmd_install_service(args)
        elif args.command == "uninstall-service":
            cmd_uninstall_service(args)
        elif args.command == "doctor":
            cmd_doctor(args)
        elif args.command == "onboard":
            cmd_onboard(args)
        elif args.command == "update":
            cmd_update(args)
        else:
            parser.print_help()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


# --- Command implementations ---

def _load_cfg(args):
    from sea_turtle.config.loader import load_config
    return load_config(getattr(args, "config", None))


def _get_pid() -> int | None:
    """Read daemon PID from file."""
    pid_file = Path("~/.sea_turtle/daemon.pid").expanduser()
    if pid_file.exists():
        try:
            return int(pid_file.read_text().strip())
        except (ValueError, OSError):
            pass
    return None


def _is_daemon_running() -> bool:
    pid = _get_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cmd_start(args):
    """Start the daemon process."""
    if _is_daemon_running():
        print("🐢 Daemon is already running (PID: {}).".format(_get_pid()))
        return

    print("🐢 Starting Sea Turtle daemon...")
    from sea_turtle.daemon import run_daemon
    config_path = getattr(args, "config", None)
    run_daemon(config_path)


def cmd_stop(args):
    """Stop the daemon process."""
    pid = _get_pid()
    if pid is None or not _is_daemon_running():
        print("🐢 Daemon is not running.")
        return

    print(f"🐢 Stopping daemon (PID: {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
        print("✅ Stop signal sent.")
    except OSError as e:
        print(f"❌ Failed to stop daemon: {e}")


def cmd_status(args):
    """Show daemon and agent status."""
    pid = _get_pid()
    running = _is_daemon_running()

    print(f"🐢 Sea Turtle v{__version__}")
    print(f"  Daemon: {'🟢 Running' if running else '🔴 Stopped'} (PID: {pid or 'N/A'})")

    config = _load_cfg(args)
    agents = config.get("agents", {})
    print(f"  Agents configured: {len(agents)}")
    for agent_id, agent_cfg in agents.items():
        name = agent_cfg.get("name", "Turtle")
        model = agent_cfg.get("model", "?")
        sandbox = agent_cfg.get("sandbox", "confined")
        print(f"    - {agent_id}: {name} (model: {model}, sandbox: {sandbox})")


def cmd_logs(args):
    """View logs."""
    config = _load_cfg(args)
    if args.agent_id:
        data_dir = config.get("global", {}).get("data_dir", "~/.sea_turtle")
        log_file = Path(data_dir).expanduser() / "logs" / "agents" / args.agent_id / "agent.log"
    else:
        log_file = Path(config.get("global", {}).get("log_file", "~/.sea_turtle/logs/daemon.log")).expanduser()

    if not log_file.exists():
        print(f"Log file not found: {log_file}")
        return

    if args.follow:
        subprocess.run(["tail", "-f", str(log_file)])
    else:
        subprocess.run(["tail", "-100", str(log_file)])


def cmd_agent(args):
    """Agent management commands."""
    if not args.agent_command:
        print("Usage: seaturtle agent {list|add|del|start|stop|restart|info}")
        return

    config = _load_cfg(args)

    if args.agent_command == "list":
        agents = config.get("agents", {})
        if not agents:
            print("No agents configured.")
            return
        print(f"{'ID':<15} {'Name':<15} {'Model':<25} {'Sandbox':<12}")
        print("-" * 67)
        for agent_id, cfg in agents.items():
            print(f"{agent_id:<15} {cfg.get('name', 'Turtle'):<15} {cfg.get('model', '?'):<25} {cfg.get('sandbox', 'confined'):<12}")

    elif args.agent_command == "add":
        _agent_add(args, config)

    elif args.agent_command == "del":
        _agent_del(args, config)

    elif args.agent_command == "info":
        agent_cfg = config.get("agents", {}).get(args.id)
        if not agent_cfg:
            print(f"Agent '{args.id}' not found.")
            return
        print(f"🐢 Agent: {args.id}")
        print(json.dumps(agent_cfg, indent=2, ensure_ascii=False))

    elif args.agent_command in ("start", "stop", "restart"):
        if not _is_daemon_running():
            print("⚠️ Daemon is not running. Start it first: seaturtle start")
            return
        print(f"ℹ️ Agent {args.agent_command} requires a running daemon. Use Telegram/Discord commands or restart the daemon.")

    else:
        print(f"Unknown agent command: {args.agent_command}")


def _agent_add(args, config):
    """Add a new agent to config."""
    agent_id = args.id
    if agent_id in config.get("agents", {}):
        print(f"Agent '{agent_id}' already exists.")
        return

    name = args.name or input(f"Agent name [{agent_id}]: ").strip() or agent_id
    model = args.model or config.get("llm", {}).get("default_model", "gemini-2.5-flash")
    sandbox = args.sandbox

    workspace = f"./agents/{agent_id}"

    agent_cfg = {
        "name": name,
        "human_name": "Human",
        "workspace": workspace,
        "model": model,
        "tools": ["shell", "memory", "task"],
        "sandbox": sandbox,
        "telegram": {"bot_token_env": f"TELEGRAM_BOT_TOKEN_{agent_id.upper()}", "allowed_user_ids": []},
        "discord": {"bot_token_env": f"DISCORD_BOT_TOKEN_{agent_id.upper()}", "allowed_user_ids": []},
    }

    config.setdefault("agents", {})[agent_id] = agent_cfg

    # Init workspace
    from sea_turtle.core.rules import init_agent_workspace
    init_agent_workspace(workspace, agent_name=name)

    # Save config
    from sea_turtle.config.loader import save_config, find_config_file
    config_file = find_config_file() or "~/.sea_turtle/config.json"
    save_config(config, config_file)

    print(f"✅ Agent '{agent_id}' created.")
    print(f"  Workspace: {workspace}")
    print(f"  Model: {model}")
    print(f"  Sandbox: {sandbox}")


def _agent_del(args, config):
    """Delete an agent from config."""
    agent_id = args.id
    if agent_id not in config.get("agents", {}):
        print(f"Agent '{agent_id}' not found.")
        return

    if not args.force:
        confirm = input(f"Delete agent '{agent_id}'? This removes the config entry. [y/N]: ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return

    del config["agents"][agent_id]

    from sea_turtle.config.loader import save_config, find_config_file
    config_file = find_config_file() or "~/.sea_turtle/config.json"
    save_config(config, config_file)

    print(f"✅ Agent '{agent_id}' removed from config.")
    print(f"  Note: Workspace files are NOT deleted. Remove manually if needed.")


def cmd_model(args):
    """Model management commands."""
    if not args.model_command:
        print("Usage: seaturtle model {list|set}")
        return

    if args.model_command == "list":
        from sea_turtle.llm.registry import list_models, format_model_list
        provider = getattr(args, "provider", None)
        models = list_models(provider)
        print(format_model_list(models))

    elif args.model_command == "set":
        config = _load_cfg(args)
        agent_id = args.agent_id
        model_name = args.model_name

        if agent_id not in config.get("agents", {}):
            print(f"Agent '{agent_id}' not found.")
            return

        config["agents"][agent_id]["model"] = model_name

        from sea_turtle.config.loader import save_config, find_config_file
        config_file = find_config_file() or "~/.sea_turtle/config.json"
        save_config(config, config_file)

        print(f"✅ Agent '{agent_id}' model set to: {model_name}")
        print("  Restart the agent for the change to take effect.")


def cmd_config(args):
    """Configuration commands."""
    if not args.config_command:
        print("Usage: seaturtle config {show|edit|validate}")
        return

    config = _load_cfg(args)

    if args.config_command == "show":
        # Mask sensitive env values
        print(json.dumps(config, indent=2, ensure_ascii=False))

    elif args.config_command == "edit":
        from sea_turtle.config.loader import find_config_file
        config_file = find_config_file() or "~/.sea_turtle/config.json"
        editor = os.environ.get("EDITOR", "vi")
        subprocess.run([editor, str(Path(config_file).expanduser())])

    elif args.config_command == "validate":
        from sea_turtle.config.loader import validate_config
        issues = validate_config(config)
        if not issues:
            print("✅ Configuration is valid.")
        else:
            for issue in issues:
                print(f"  {issue}")


def cmd_install_service(args):
    """Register as system service."""
    from sea_turtle.service.systemd import install_systemd_service
    from sea_turtle.service.launchd import install_launchd_service
    import platform

    if platform.system() == "Linux":
        install_systemd_service()
    elif platform.system() == "Darwin":
        install_launchd_service()
    else:
        print(f"⚠️ Unsupported platform: {platform.system()}")


def cmd_uninstall_service(args):
    """Remove system service."""
    from sea_turtle.service.systemd import uninstall_systemd_service
    from sea_turtle.service.launchd import uninstall_launchd_service
    import platform

    if platform.system() == "Linux":
        uninstall_systemd_service()
    elif platform.system() == "Darwin":
        uninstall_launchd_service()
    else:
        print(f"⚠️ Unsupported platform: {platform.system()}")


def cmd_doctor(args):
    """Check environment and dependencies."""
    print(f"🐢 Sea Turtle Doctor v{__version__}")
    print()

    # Python version
    py_ver = sys.version_info
    py_ok = py_ver >= (3, 11)
    print(f"  Python: {sys.version.split()[0]} {'✅' if py_ok else '❌ (need 3.11+)'}")

    # Dependencies
    deps = [
        ("google.genai", "google-genai"),
        ("openai", "openai"),
        ("anthropic", "anthropic"),
        ("telegram", "python-telegram-bot"),
        ("discord", "discord.py"),
    ]
    for module, package in deps:
        try:
            __import__(module)
            print(f"  {package}: ✅")
        except ImportError:
            print(f"  {package}: ❌ (not installed)")

    # Config
    from sea_turtle.config.loader import find_config_file, load_config, validate_config
    config_file = find_config_file()
    if config_file:
        print(f"  Config: ✅ ({config_file})")
        config = load_config()
        issues = validate_config(config)
        for issue in issues:
            print(f"    {issue}")
    else:
        print("  Config: ⚠️ Not found (run 'seaturtle onboard')")

    # Data directory
    data_dir = Path("~/.sea_turtle").expanduser()
    print(f"  Data dir: {'✅' if data_dir.exists() else '⚠️ Not created'} ({data_dir})")


def cmd_onboard(args):
    """Interactive setup wizard."""
    print(f"🐢 Welcome to Sea Turtle v{__version__} Setup!")
    print()

    data_dir = Path("~/.sea_turtle").expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)

    config_file = data_dir / "config.json"

    # LLM Provider
    print("Select your primary LLM provider:")
    print("  1. Google (Gemini)")
    print("  2. OpenAI (GPT)")
    print("  3. Anthropic (Claude)")
    print("  4. xAI (Grok)")
    print("  5. OpenRouter")
    choice = input("Choice [1]: ").strip() or "1"
    provider_map = {"1": "google", "2": "openai", "3": "anthropic", "4": "xai", "5": "openrouter"}
    provider = provider_map.get(choice, "google")

    env_map = {
        "google": "GOOGLE_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "xai": "XAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    api_key_env = env_map.get(provider, "GOOGLE_API_KEY")
    print(f"\nMake sure to set your API key: export {api_key_env}=your_key_here")

    # Model
    from sea_turtle.llm.registry import list_models
    models = list_models(provider)
    default_model = models[0].name if models else "gemini-2.5-flash"
    model = input(f"Default model [{default_model}]: ").strip() or default_model

    # Agent name
    agent_name = input("Agent name [Turtle]: ").strip() or "Turtle"
    human_name = input("Your name [Human]: ").strip() or "Human"

    # Telegram
    tg_enabled = input("Enable Telegram? [y/N]: ").strip().lower() == "y"

    # Discord
    dc_enabled = input("Enable Discord? [y/N]: ").strip().lower() == "y"

    # Build config
    from sea_turtle.config.loader import DEFAULT_CONFIG
    from copy import deepcopy
    config = deepcopy(DEFAULT_CONFIG)
    config["llm"]["default_provider"] = provider
    config["llm"]["default_model"] = model
    config["telegram"]["enabled"] = tg_enabled
    config["discord"]["enabled"] = dc_enabled
    config["agents"]["default"]["name"] = agent_name
    config["agents"]["default"]["human_name"] = human_name
    config["agents"]["default"]["model"] = model

    # Save
    from sea_turtle.config.loader import save_config
    save_config(config, str(config_file))
    print(f"\n✅ Config saved to: {config_file}")

    # Init workspace
    from sea_turtle.core.rules import init_agent_workspace
    workspace = Path("./agents/default")
    init_agent_workspace(str(workspace), agent_name=agent_name, human_name=human_name)
    print(f"✅ Agent workspace created: {workspace}")

    print(f"\n🐢 Setup complete! Start with: seaturtle start")
    if tg_enabled:
        print(f"  Don't forget: export TELEGRAM_BOT_TOKEN=your_token")
    if dc_enabled:
        print(f"  Don't forget: export DISCORD_BOT_TOKEN=your_token")


def cmd_update(args):
    """Check for and install updates."""
    from sea_turtle.updater.github import check_update, install_update

    print(f"🐢 Current version: {__version__}")
    print("Checking for updates...")

    latest = check_update()
    if latest is None:
        print("⚠️ Could not check for updates.")
        return

    if latest == __version__:
        print("✅ You are on the latest version.")
        return

    print(f"📦 New version available: {latest}")

    if args.check:
        print("Run 'seaturtle update' to install.")
        return

    confirm = input("Install update? [y/N]: ").strip().lower()
    if confirm == "y":
        success = install_update()
        if success:
            print("✅ Update installed. Restart the daemon: seaturtle stop && seaturtle start")
        else:
            print("❌ Update failed.")
