"""Sticker registry helpers for Telegram emotion stickers."""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STICKER_FILE_NAME = "stickers.json"

EMOJI_TO_EMOTION = {
    "🙂": "warm",
    "😊": "warm",
    "☺️": "warm",
    "😘": "warm",
    "😍": "warm",
    "🥰": "warm",
    "🤗": "warm",
    "❤️": "warm",
    "♥️": "warm",
    "💖": "warm",
    "💕": "warm",
    "💗": "warm",
    "💘": "warm",
    "💝": "warm",
    "🌹": "warm",
    "🎀": "warm",
    "👋": "warm",
    "😊": "warm",
    "😄": "happy",
    "😃": "happy",
    "😀": "happy",
    "😁": "happy",
    "😆": "happy",
    "😂": "happy",
    "🤣": "happy",
    "😎": "happy",
    "🎉": "happy",
    "🥳": "happy",
    "🤪": "playful",
    "😛": "playful",
    "💸": "playful",
    "😳": "embarrassed",
    "😅": "embarrassed",
    "🙈": "embarrassed",
    "🙊": "embarrassed",
    "🫣": "embarrassed",
    "😬": "embarrassed",
    "☺": "embarrassed",
    "🙈": "embarrassed",
    "😠": "angry",
    "😡": "angry",
    "🤬": "angry",
    "💢": "angry",
    "😭": "sad",
    "😢": "sad",
    "🥺": "sad",
    "💔": "sad",
    "😞": "sad",
    "😔": "sad",
    "😿": "sad",
    "😨": "surprised",
    "😵": "surprised",
    "😱": "surprised",
    "😐": "calm",
    "😶": "calm",
    "🙂‍↕️": "calm",
    "👌": "calm",
    "🧘‍♂️": "calm",
    "🫡": "serious",
    "😌": "calm",
    "🤔": "serious",
    "🤫": "serious",
    "🧐": "serious",
    "😤": "serious",
    "😪": "tired",
    "💪": "supportive",
    "👍": "supportive",
    "👏": "supportive",
    "🙏": "supportive",
    "🙅‍♂️": "refuse",
    "🤷‍♂️": "refuse",
    "😴": "tired",
    "🥴": "embarrassed",
    "😣": "sad",
    "😒": "angry",
    "😉": "playful",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sticker_file_path(workspace: str) -> Path:
    return Path(workspace) / STICKER_FILE_NAME


def load_sticker_data(workspace: str) -> dict[str, Any]:
    path = sticker_file_path(workspace)
    if not path.exists():
        return {"version": 1, "stickers": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "stickers": []}
    stickers = data.get("stickers", [])
    if not isinstance(stickers, list):
        stickers = []
    normalized = []
    changed = False
    for sticker in stickers:
        if not isinstance(sticker, dict):
            continue
        if not sticker.get("emotion"):
            inferred = infer_emotion_from_emoji(sticker.get("emoji"))
            if inferred:
                sticker["emotion"] = inferred
                sticker["updated_at"] = utc_now_iso()
                changed = True
        normalized.append(sticker)
    payload = {"version": 1, "stickers": normalized}
    if changed:
        save_sticker_data(workspace, payload)
    return payload


def save_sticker_data(workspace: str, data: dict[str, Any]) -> None:
    path = sticker_file_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "stickers": data.get("stickers", [])}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def infer_emotion_from_emoji(emoji: str | None) -> str | None:
    if not emoji:
        return None
    return EMOJI_TO_EMOTION.get(emoji.strip())


def register_sticker(
    workspace: str,
    *,
    file_id: str,
    file_unique_id: str,
    emoji: str | None = None,
    set_name: str | None = None,
    emotion: str | None = None,
) -> dict[str, Any]:
    data = load_sticker_data(workspace)
    now = utc_now_iso()
    inferred = emotion or infer_emotion_from_emoji(emoji)
    for sticker in data["stickers"]:
        if sticker.get("file_unique_id") == file_unique_id:
            sticker["file_id"] = file_id
            sticker["emoji"] = emoji or sticker.get("emoji", "")
            sticker["set_name"] = set_name or sticker.get("set_name", "")
            if inferred:
                sticker["emotion"] = inferred
            sticker["updated_at"] = now
            save_sticker_data(workspace, data)
            return sticker

    sticker = {
        "id": f"sticker-{len(data['stickers']) + 1}",
        "file_id": file_id,
        "file_unique_id": file_unique_id,
        "emoji": emoji or "",
        "set_name": set_name or "",
        "emotion": inferred or "",
        "created_at": now,
        "updated_at": now,
    }
    data["stickers"].append(sticker)
    save_sticker_data(workspace, data)
    return sticker


def pick_sticker_for_emotion(workspace: str, emotion: str) -> dict[str, Any] | None:
    data = load_sticker_data(workspace)
    candidates = [s for s in data["stickers"] if s.get("emotion") == emotion]
    if not candidates:
        return None
    return random.choice(candidates)
