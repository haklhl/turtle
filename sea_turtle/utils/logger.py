"""Lightweight logging setup for Sea Turtle."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logger(
    name: str,
    log_file: str | None = None,
    level: str = "info",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 3,
    fmt: str = "[{asctime}] [{levelname}] [{name}] {message}",
) -> logging.Logger:
    """Create and configure a logger with optional file rotation.

    Args:
        name: Logger name.
        log_file: Path to log file. If None, logs to stderr only.
        level: Log level string (debug/info/warning/error).
        max_bytes: Max log file size before rotation.
        backup_count: Number of rotated files to keep.
        fmt: Log format string (style='{').

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)
    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(log_level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(fmt, style="{", datefmt="%Y-%m-%d %H:%M:%S")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        log_path = Path(log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(log_path),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_daemon_logger(config: dict | None = None) -> logging.Logger:
    """Get the daemon (main process) logger."""
    if config:
        log_cfg = config.get("logging", {})
        global_cfg = config.get("global", {})
        return setup_logger(
            name="daemon",
            log_file=global_cfg.get("log_file", "~/.sea_turtle/logs/daemon.log"),
            level=log_cfg.get("level", "info"),
            max_bytes=log_cfg.get("max_file_size_mb", 10) * 1024 * 1024,
            backup_count=log_cfg.get("backup_count", 3),
            fmt=log_cfg.get("format", "[{asctime}] [{levelname}] [{name}] {message}"),
        )
    return setup_logger(name="daemon")


def get_agent_logger(agent_id: str, config: dict | None = None) -> logging.Logger:
    """Get an agent-specific logger."""
    data_dir = "~/.sea_turtle"
    if config:
        data_dir = config.get("global", {}).get("data_dir", data_dir)
    log_file = os.path.join(data_dir, "logs", "agents", agent_id, "agent.log")

    if config:
        log_cfg = config.get("logging", {})
        return setup_logger(
            name=f"agent.{agent_id}",
            log_file=log_file,
            level=log_cfg.get("level", "info"),
            max_bytes=log_cfg.get("max_file_size_mb", 10) * 1024 * 1024,
            backup_count=log_cfg.get("backup_count", 3),
            fmt=log_cfg.get("format", "[{asctime}] [{levelname}] [{name}] {message}"),
        )
    return setup_logger(name=f"agent.{agent_id}", log_file=log_file)
