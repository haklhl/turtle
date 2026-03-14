#!/usr/bin/env python3
"""CLI wrapper for Discord history tools, suitable for Codex shell use."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sea_turtle.config.loader import load_config  # noqa: E402
from sea_turtle.core.discord_api import (  # noqa: E402
    fetch_channel_info,
    read_messages,
    search_messages,
)


def _default_config_path() -> str:
    return str((ROOT / "config.json").resolve())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discord history helper for Sea Turtle agents.",
    )
    parser.add_argument(
        "--config",
        default=_default_config_path(),
        help="Path to Sea Turtle config.json. Defaults to the turtle repo config.",
    )
    parser.add_argument(
        "--agent-id",
        default="default",
        help="Agent id whose Discord token/config should be used.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    channel_info = subparsers.add_parser(
        "channel-info",
        help="Fetch Discord metadata for one channel or thread.",
    )
    channel_info.add_argument("--channel-id", required=True, help="Discord channel/thread id.")

    read = subparsers.add_parser(
        "read",
        help="Read recent Discord messages from one channel or thread.",
    )
    read.add_argument("--channel-id", required=True, help="Discord channel/thread id.")
    read.add_argument("--limit", type=int, default=20, help="How many messages to return (1-100).")
    read.add_argument("--before", help="Only messages before this Discord message id.")
    read.add_argument("--after", help="Only messages after this Discord message id.")
    read.add_argument("--around", help="Messages around this Discord message id.")

    search = subparsers.add_parser(
        "search",
        help="Search Discord messages in one guild using Discord's search endpoint.",
    )
    search.add_argument("--guild-id", required=True, help="Discord guild id.")
    search.add_argument("--query", required=True, help="Search keyword query.")
    search.add_argument(
        "--channel-id",
        action="append",
        dest="channel_ids",
        help="Optional channel id filter. Repeat for multiple channels.",
    )
    search.add_argument(
        "--author-id",
        action="append",
        dest="author_ids",
        help="Optional author id filter. Repeat for multiple authors.",
    )
    search.add_argument("--limit", type=int, default=10, help="How many hits to return (1-25).")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(args.config)

    try:
        if args.command == "channel-info":
            payload = fetch_channel_info(config, args.agent_id, args.channel_id)
        elif args.command == "read":
            payload = read_messages(
                config,
                args.agent_id,
                args.channel_id,
                limit=args.limit,
                before=args.before,
                after=args.after,
                around=args.around,
            )
        elif args.command == "search":
            payload = search_messages(
                config,
                args.agent_id,
                guild_id=args.guild_id,
                content=args.query,
                channel_ids=args.channel_ids,
                author_ids=args.author_ids,
                limit=args.limit,
            )
        else:
            parser.error(f"unknown command: {args.command}")
            return 2
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({"ok": True, "data": payload}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
