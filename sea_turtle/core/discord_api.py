"""Minimal Discord REST helpers for agent-side Discord tools."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, parse, request

from sea_turtle.config.loader import get_agent_config, resolve_secret

DISCORD_API_BASE = "https://discord.com/api/v10"


def _resolve_bot_token(config: dict, agent_id: str) -> str:
    agent_cfg = get_agent_config(config, agent_id) or {}
    agent_discord_cfg = agent_cfg.get("discord", {})
    token = resolve_secret(agent_discord_cfg, "bot_token", "bot_token_env")
    if token:
        return token
    token = resolve_secret(config.get("discord", {}), "bot_token", "bot_token_env")
    if token:
        return token
    raise RuntimeError("Discord bot token is not configured for this agent.")


def _discord_get(
    config: dict,
    agent_id: str,
    path: str,
    query: dict[str, Any] | None = None,
) -> Any:
    token = _resolve_bot_token(config, agent_id)
    url = f"{DISCORD_API_BASE}{path}"
    if query:
        filtered = {key: value for key, value in query.items() if value not in (None, "", [], ())}
        if filtered:
            url = f"{url}?{parse.urlencode(filtered, doseq=True)}"
    req = request.Request(
        url,
        headers={
            "Authorization": f"Bot {token}",
            "User-Agent": "SeaTurtle/1.0 (+https://github.com/haklhl/turtle)",
        },
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Discord API {exc.code}: {detail or exc.reason}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Discord API request failed: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Discord API returned invalid JSON.") from exc


def fetch_channel_info(config: dict, agent_id: str, channel_id: str) -> dict[str, Any]:
    return _discord_get(config, agent_id, f"/channels/{channel_id}")


def read_messages(
    config: dict,
    agent_id: str,
    channel_id: str,
    *,
    limit: int | None = None,
    before: str | None = None,
    after: str | None = None,
    around: str | None = None,
) -> list[dict[str, Any]]:
    bounded_limit = None
    if limit is not None:
        bounded_limit = max(1, min(int(limit), 100))
    payload = _discord_get(
        config,
        agent_id,
        f"/channels/{channel_id}/messages",
        {
            "limit": bounded_limit,
            "before": before,
            "after": after,
            "around": around,
        },
    )
    return payload if isinstance(payload, list) else []


def search_messages(
    config: dict,
    agent_id: str,
    *,
    guild_id: str,
    content: str,
    channel_ids: list[str] | None = None,
    author_ids: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    bounded_limit = None
    if limit is not None:
        bounded_limit = max(1, min(int(limit), 25))
    return _discord_get(
        config,
        agent_id,
        f"/guilds/{guild_id}/messages/search",
        {
            "content": content,
            "channel_id": channel_ids or None,
            "author_id": author_ids or None,
            "limit": bounded_limit,
        },
    )
