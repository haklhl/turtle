"""JSON configuration loader with validation and default merging."""

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "version": "1.0",
    "global": {
        "log_level": "info",
        "log_file": "~/.sea_turtle/logs/daemon.log",
        "data_dir": "~/.sea_turtle",
        "default_agent": "default",
        "pid_file": "~/.sea_turtle/daemon.pid",
        "socket_path": "~/.sea_turtle/daemon.sock",
    },
    "llm": {
        "default_provider": "google",
        "default_model": "gemini-2.5-flash",
        "temperature": 0.7,
        "max_output_tokens": 8192,
        "providers": {
            "google": {"api_key": "", "api_key_env": "GOOGLE_API_KEY"},
            "openai": {"api_key": "", "api_key_env": "OPENAI_API_KEY"},
            "anthropic": {"api_key": "", "api_key_env": "ANTHROPIC_API_KEY"},
            "openrouter": {"api_key": "", "api_key_env": "OPENROUTER_API_KEY"},
            "xai": {"api_key": "", "api_key_env": "XAI_API_KEY"},
        },
    },
    "context": {
        "max_tokens": 200000,
        "compress_threshold_ratio": 0.7,
        "compress_target_ratio": 0.3,
        "compress_model": "gemini-2.0-flash",
    },
    "shell": {
        "enabled": True,
        "timeout_seconds": 30,
        "max_output_chars": 10000,
        "dangerous_commands": [
            "rm", "rmdir", "chmod", "chown", "sudo",
            "shutdown", "reboot", "kill", "mkfs", "dd",
        ],
        "blocked_commands": ["rm -rf /", "rm -rf ~", ":(){ :|:& };:"],
        "history_max_entries": 10000,
        "history_max_file_size_mb": 50,
        "history_record_output": True,
        "history_output_max_chars": 500,
    },
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "bot_token_env": "TELEGRAM_BOT_TOKEN",
        "allowed_user_ids": [],
    },
    "discord": {
        "enabled": False,
        "bot_token": "",
        "bot_token_env": "DISCORD_BOT_TOKEN",
        "allowed_user_ids": [],
    },
    "heartbeat": {
        "enabled": True,
        "interval_seconds": 300,
    },
    "token_billing": {
        "enabled": True,
        "log_file": "token_usage.json",
    },
    "logging": {
        "level": "info",
        "max_file_size_mb": 10,
        "backup_count": 3,
        "format": "[{asctime}] [{levelname}] [{name}] {message}",
    },
    "agents": {
        "default": {
            "name": "Turtle",
            "human_name": "Human",
            "workspace": "./agents/default",
            "model": "gemini-2.5-flash",
            "tools": ["shell", "memory", "task"],
            "sandbox": "confined",
            "telegram": {
                "bot_token": "",
                "bot_token_env": "TELEGRAM_BOT_TOKEN",
                "allowed_user_ids": [],
            },
            "discord": {
                "bot_token": "",
                "bot_token_env": "DISCORD_BOT_TOKEN",
                "allowed_user_ids": [],
            },
        }
    },
}

CONFIG_SEARCH_PATHS = [
    "config.json",
    "~/.sea_turtle/config.json",
    "/etc/sea_turtle/config.json",
]


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base dict."""
    result = deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _expand_paths(config: dict) -> dict:
    """Expand ~ in path values."""
    path_keys = {"log_file", "data_dir", "pid_file", "socket_path", "workspace"}
    for key, value in config.items():
        if isinstance(value, dict):
            config[key] = _expand_paths(value)
        elif isinstance(value, str) and key in path_keys:
            config[key] = str(Path(value).expanduser())
    return config


def find_config_file(explicit_path: str | None = None) -> str | None:
    """Find the config file from explicit path or search paths."""
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if path.exists():
            return str(path)
        return None

    for search_path in CONFIG_SEARCH_PATHS:
        path = Path(search_path).expanduser()
        if path.exists():
            return str(path)
    return None


def load_config(config_path: str | None = None) -> dict:
    """Load and validate configuration.

    Merges user config on top of defaults. Returns fully resolved config.

    Args:
        config_path: Explicit path to config file. If None, searches default locations.

    Returns:
        Merged configuration dict.

    Raises:
        FileNotFoundError: If explicit config_path doesn't exist.
        json.JSONDecodeError: If config file is invalid JSON.
    """
    config = deepcopy(DEFAULT_CONFIG)

    file_path = find_config_file(config_path)
    if config_path and not file_path:
        raise FileNotFoundError(f"Config file not found: {config_path}")

    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        config = _deep_merge(config, user_config)

    config = _expand_paths(config)
    return config


def save_config(config: dict, config_path: str) -> None:
    """Save configuration to a JSON file.

    Args:
        config: Configuration dict to save.
        config_path: Path to write the config file.
    """
    path = Path(config_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")


def validate_config(config: dict) -> list[str]:
    """Validate configuration and return list of warnings/errors.

    Returns:
        List of warning/error messages. Empty list means config is valid.
    """
    issues: list[str] = []

    if "agents" not in config or not config["agents"]:
        issues.append("ERROR: No agents configured.")

    default_agent = config.get("global", {}).get("default_agent", "default")
    if default_agent not in config.get("agents", {}):
        issues.append(f"ERROR: Default agent '{default_agent}' not found in agents config.")

    for agent_id, agent_cfg in config.get("agents", {}).items():
        workspace = agent_cfg.get("workspace", "")
        if not workspace:
            issues.append(f"ERROR: Agent '{agent_id}' has no workspace configured.")

        sandbox = agent_cfg.get("sandbox", "confined")
        if sandbox not in ("normal", "confined", "restricted"):
            issues.append(
                f"WARNING: Agent '{agent_id}' has unknown sandbox mode '{sandbox}'. "
                "Valid: normal, confined, restricted."
            )

    llm_cfg = config.get("llm", {})
    default_provider = llm_cfg.get("default_provider", "google")
    providers = llm_cfg.get("providers", {})
    if default_provider not in providers:
        issues.append(f"WARNING: Default LLM provider '{default_provider}' not configured.")

    for provider_name, provider_cfg in providers.items():
        resolved = resolve_secret(provider_cfg, "api_key", "api_key_env")
        if not resolved:
            issues.append(
                f"WARNING: Provider '{provider_name}' has no API key configured "
                f"(set 'api_key' in config or env var '{provider_cfg.get('api_key_env', '')}')."
            )

    return issues


def resolve_secret(cfg: dict, key: str = "api_key", env_key: str = "api_key_env") -> str:
    """Resolve a secret value: prefer direct value in config, fallback to env var.

    Args:
        cfg: Config dict containing the key and env_key fields.
        key: Field name for the direct value (e.g. 'api_key', 'bot_token').
        env_key: Field name for the env variable name (e.g. 'api_key_env', 'bot_token_env').

    Returns:
        Resolved secret string, or empty string if not found.
    """
    # Priority 1: direct value in config
    direct = cfg.get(key, "")
    if direct:
        return direct

    # Priority 2: environment variable
    env_name = cfg.get(env_key, "")
    if env_name:
        return os.environ.get(env_name, "")

    return ""


def get_agent_config(config: dict, agent_id: str) -> dict | None:
    """Get configuration for a specific agent.

    Args:
        config: Full configuration dict.
        agent_id: Agent identifier.

    Returns:
        Agent config dict, or None if not found.
    """
    return config.get("agents", {}).get(agent_id)
