"""Agent-scoped scheduler storage and execution logging."""

from __future__ import annotations

import json
import re
import shlex
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEDULE_FILE_NAME = "schedule.json"
SCHEDULE_RUN_LOG_FILE_NAME = "schedule_runs.jsonl"
HEARTBEAT_FILE_NAME = "heartbeat.json"
HEARTBEAT_RUN_LOG_FILE_NAME = "heartbeat_runs.jsonl"
LEGACY_TASK_FILE_NAME = "task.json"
LEGACY_TASK_MD_FILE_NAME = "task.md"

SCHEDULE_STATUSES = {"enabled", "disabled"}
EXECUTION_TYPES = {"script", "llm_prompt"}
TRIGGER_TYPES = {"interval", "daily"}
RUN_OUTCOMES = {"success", "noop", "error"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def schedule_file_path(workspace: str) -> Path:
    return Path(workspace) / SCHEDULE_FILE_NAME


def schedule_run_log_path(workspace: str) -> Path:
    return Path(workspace) / SCHEDULE_RUN_LOG_FILE_NAME


def heartbeat_file_path(workspace: str) -> Path:
    return Path(workspace) / HEARTBEAT_FILE_NAME


def heartbeat_run_log_path(workspace: str) -> Path:
    return Path(workspace) / HEARTBEAT_RUN_LOG_FILE_NAME


def _legacy_task_json_path(workspace: str) -> Path:
    return Path(workspace) / LEGACY_TASK_FILE_NAME


def _legacy_task_md_path(workspace: str) -> Path:
    return Path(workspace) / LEGACY_TASK_MD_FILE_NAME


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_timezone(value: str | None) -> timezone:
    text = (value or "UTC").strip().upper()
    if text in {"UTC", "Z"}:
        return timezone.utc
    match = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", text)
    if not match:
        return timezone.utc
    sign, hours, minutes = match.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes))
    if sign == "-":
        offset = -offset
    return timezone(offset)


def _normalize_interval_seconds(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = 300
    return max(60, seconds)


def _normalize_daily_time(value: Any) -> str:
    text = str(value or "09:00").strip()
    if re.fullmatch(r"\d{2}:\d{2}", text):
        return text
    return "09:00"


def default_schedule_data() -> dict[str, Any]:
    return {"version": 1, "schedules": []}


def default_heartbeat_data() -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "version": 1,
        "enabled": False,
        "interval_minutes": 60,
        "created_at": now,
        "updated_at": now,
        "is_running": False,
        "run_count": 0,
        "last_started_at": "",
        "last_run_at": "",
        "last_finished_at": "",
        "last_outcome": "",
        "last_result": "",
        "last_error": "",
    }


def _normalize_trigger(trigger: dict[str, Any] | None) -> dict[str, Any]:
    trigger = trigger or {}
    trigger_type = str(trigger.get("type") or "interval").strip().lower()
    if trigger_type not in TRIGGER_TYPES:
        trigger_type = "interval"

    if trigger_type == "daily":
        return {
            "type": "daily",
            "time": _normalize_daily_time(trigger.get("time")),
            "timezone": str(trigger.get("timezone") or "UTC").strip() or "UTC",
        }

    return {
        "type": "interval",
        "seconds": _normalize_interval_seconds(trigger.get("seconds")),
    }


def _normalize_target(execution_type: str, target: dict[str, Any] | None) -> dict[str, Any]:
    target = target or {}
    if execution_type == "script":
        return {
            "command": str(target.get("command") or "").strip(),
        }
    return {
        "prompt": str(target.get("prompt") or "").strip(),
    }


def _normalize_schedule(schedule: dict[str, Any], index: int) -> dict[str, Any]:
    schedule_id = str(schedule.get("id") or f"schedule-{index}").strip() or f"schedule-{index}"
    execution_type = str(schedule.get("execution_type") or "llm_prompt").strip().lower()
    if execution_type not in EXECUTION_TYPES:
        execution_type = "llm_prompt"

    status = str(schedule.get("status") or "enabled").strip().lower()
    if status not in SCHEDULE_STATUSES:
        status = "enabled"

    created_at = str(schedule.get("created_at") or utc_now_iso())
    updated_at = str(schedule.get("updated_at") or created_at)

    normalized = {
        "id": schedule_id,
        "created_at": created_at,
        "updated_at": updated_at,
        "author": str(schedule.get("author") or "").strip(),
        "description": str(schedule.get("description") or schedule.get("title") or "").strip(),
        "execution_type": execution_type,
        "trigger": _normalize_trigger(schedule.get("trigger")),
        "target": _normalize_target(execution_type, schedule.get("target")),
        "status": status,
        "run_count": max(0, int(schedule.get("run_count") or 0)),
        "is_running": bool(schedule.get("is_running") or False),
        "last_started_at": str(schedule.get("last_started_at") or ""),
        "last_run_at": str(schedule.get("last_run_at") or ""),
        "last_finished_at": str(schedule.get("last_finished_at") or ""),
        "last_outcome": str(schedule.get("last_outcome") or ""),
        "last_result": str(schedule.get("last_result") or ""),
        "last_error": str(schedule.get("last_error") or ""),
    }
    return normalized


def _load_legacy_schedule_data(workspace: str) -> dict[str, Any]:
    """Best-effort migration path from old task files.

    Old task queues do not contain scheduling metadata, so they become disabled
    LLM jobs purely as preserved records.
    """
    candidates: list[dict[str, Any]] = []
    task_json = _legacy_task_json_path(workspace)
    if task_json.exists():
        try:
            raw = json.loads(task_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        for index, task in enumerate(raw.get("tasks", []) or [], start=1):
            title = str(task.get("title") or task.get("description") or "").strip()
            if not title:
                continue
            candidates.append({
                "id": f"schedule-{len(candidates) + 1}",
                "created_at": task.get("created_at") or utc_now_iso(),
                "updated_at": task.get("updated_at") or task.get("created_at") or utc_now_iso(),
                "author": "legacy",
                "description": f"[legacy task] {title}",
                "execution_type": "llm_prompt",
                "trigger": {"type": "interval", "seconds": 86400},
                "target": {"prompt": str(task.get("notes") or task.get("result") or title).strip()},
                "status": "disabled",
                "run_count": 0,
                "last_result": str(task.get("result") or "").strip(),
            })

    if not candidates:
        task_md = _legacy_task_md_path(workspace)
        if task_md.exists():
            lines = task_md.read_text(encoding="utf-8").splitlines()
            for raw_line in lines:
                stripped = raw_line.strip()
                if not stripped.startswith("- ["):
                    continue
                match = re.match(r"- \[( |x)\]\s*(.+)$", stripped, flags=re.IGNORECASE)
                if not match:
                    continue
                _, title = match.groups()
                candidates.append({
                    "id": f"schedule-{len(candidates) + 1}",
                    "created_at": utc_now_iso(),
                    "updated_at": utc_now_iso(),
                    "author": "legacy",
                    "description": f"[legacy task] {title.strip()}",
                    "execution_type": "llm_prompt",
                    "trigger": {"type": "interval", "seconds": 86400},
                    "target": {"prompt": title.strip()},
                    "status": "disabled",
                    "run_count": 0,
                })

    return {"version": 1, "schedules": candidates}


def load_schedule_data(workspace: str) -> dict[str, Any]:
    path = schedule_file_path(workspace)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = default_schedule_data()
    else:
        data = _load_legacy_schedule_data(workspace)
        save_schedule_data(workspace, data)

    schedules = data.get("schedules", [])
    if not isinstance(schedules, list):
        schedules = []
    normalized = [_normalize_schedule(item, idx) for idx, item in enumerate(schedules, start=1)]
    return {"version": 1, "schedules": normalized}


def save_schedule_data(workspace: str, data: dict[str, Any]) -> None:
    path = schedule_file_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = {
        "version": 1,
        "schedules": [_normalize_schedule(item, idx) for idx, item in enumerate(data.get("schedules", []), start=1)],
    }
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def init_task_store(workspace: str) -> None:
    init_schedule_store(workspace)
    init_heartbeat_store(workspace)


def init_schedule_store(workspace: str) -> None:
    path = schedule_file_path(workspace)
    if path.exists():
        return
    save_schedule_data(workspace, _load_legacy_schedule_data(workspace))


def _normalize_heartbeat_data(data: dict[str, Any] | None) -> dict[str, Any]:
    payload = default_heartbeat_data()
    data = data or {}
    payload["enabled"] = bool(data.get("enabled", payload["enabled"]))
    try:
        interval_minutes = int(data.get("interval_minutes", payload["interval_minutes"]))
    except (TypeError, ValueError):
        interval_minutes = payload["interval_minutes"]
    payload["interval_minutes"] = max(5, interval_minutes)
    for key in (
        "created_at",
        "updated_at",
        "last_started_at",
        "last_run_at",
        "last_finished_at",
        "last_outcome",
        "last_result",
        "last_error",
    ):
        payload[key] = str(data.get(key) or payload.get(key) or "")
    payload["is_running"] = bool(data.get("is_running") or False)
    payload["run_count"] = max(0, int(data.get("run_count") or 0))
    return payload


def load_heartbeat_data(workspace: str) -> dict[str, Any]:
    path = heartbeat_file_path(workspace)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = default_heartbeat_data()
    else:
        raw = default_heartbeat_data()
        save_heartbeat_data(workspace, raw)
    return _normalize_heartbeat_data(raw)


def save_heartbeat_data(workspace: str, data: dict[str, Any]) -> None:
    path = heartbeat_file_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_heartbeat_data(data)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def init_heartbeat_store(workspace: str) -> None:
    path = heartbeat_file_path(workspace)
    if path.exists():
        return
    save_heartbeat_data(workspace, default_heartbeat_data())


def _next_schedule_id(schedules: list[dict[str, Any]]) -> str:
    max_index = 0
    for schedule in schedules:
        match = re.fullmatch(r"schedule-(\d+)", str(schedule.get("id") or "").strip())
        if match:
            max_index = max(max_index, int(match.group(1)))
    return f"schedule-{max_index + 1}"


def _resolve_next_run_at(schedule: dict[str, Any], now: datetime | None = None) -> datetime | None:
    now = now or utc_now()
    trigger = schedule.get("trigger", {})
    trigger_type = trigger.get("type")

    if trigger_type == "interval":
        anchor = _parse_iso_datetime(schedule.get("last_run_at")) or _parse_iso_datetime(schedule.get("created_at")) or now
        return anchor + timedelta(seconds=_normalize_interval_seconds(trigger.get("seconds")))

    if trigger_type == "daily":
        daily_text = _normalize_daily_time(trigger.get("time"))
        hours, minutes = [int(part) for part in daily_text.split(":", 1)]
        tz = _parse_timezone(trigger.get("timezone"))
        now_local = now.astimezone(tz)
        candidate_local = datetime.combine(now_local.date(), time(hour=hours, minute=minutes), tzinfo=tz)
        candidate = candidate_local.astimezone(timezone.utc)
        if candidate <= now:
            candidate = (candidate_local + timedelta(days=1)).astimezone(timezone.utc)
        return candidate

    return None


def is_schedule_due(schedule: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or utc_now()
    if schedule.get("status") != "enabled":
        return False
    if schedule.get("is_running"):
        return False

    trigger = schedule.get("trigger", {})
    trigger_type = trigger.get("type")

    if trigger_type == "interval":
        next_run_at = _resolve_next_run_at(schedule, now=now)
        return bool(next_run_at and now >= next_run_at)

    if trigger_type == "daily":
        daily_text = _normalize_daily_time(trigger.get("time"))
        hours, minutes = [int(part) for part in daily_text.split(":", 1)]
        tz = _parse_timezone(trigger.get("timezone"))
        now_local = now.astimezone(tz)
        due_local = datetime.combine(now_local.date(), time(hour=hours, minute=minutes), tzinfo=tz)
        due_utc = due_local.astimezone(timezone.utc)
        if now < due_utc:
            return False
        last_run_at = _parse_iso_datetime(schedule.get("last_run_at"))
        return last_run_at is None or last_run_at < due_utc

    return False


def create_schedule(
    workspace: str,
    author: str,
    description: str,
    execution_type: str,
    trigger: dict[str, Any],
    target: dict[str, Any],
    status: str = "enabled",
) -> dict[str, Any]:
    data = load_schedule_data(workspace)
    now = utc_now_iso()
    schedule = _normalize_schedule({
        "id": _next_schedule_id(data["schedules"]),
        "created_at": now,
        "updated_at": now,
        "author": author,
        "description": description,
        "execution_type": execution_type,
        "trigger": trigger,
        "target": target,
        "status": status,
        "run_count": 0,
    }, len(data["schedules"]) + 1)
    data["schedules"].append(schedule)
    save_schedule_data(workspace, data)
    return schedule


def update_schedule(
    workspace: str,
    schedule_id: str,
    *,
    description: str | None = None,
    status: str | None = None,
    trigger: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    data = load_schedule_data(workspace)
    now = utc_now_iso()
    for index, schedule in enumerate(data["schedules"], start=1):
        if schedule.get("id") != schedule_id:
            continue
        if description is not None:
            schedule["description"] = str(description).strip()
        if status is not None:
            normalized_status = str(status).strip().lower()
            if normalized_status in SCHEDULE_STATUSES:
                schedule["status"] = normalized_status
                if normalized_status == "disabled":
                    schedule["is_running"] = False
        if trigger is not None:
            schedule["trigger"] = _normalize_trigger(trigger)
        if target is not None:
            schedule["target"] = _normalize_target(schedule.get("execution_type", "llm_prompt"), target)
        schedule["updated_at"] = now
        data["schedules"][index - 1] = _normalize_schedule(schedule, index)
        save_schedule_data(workspace, data)
        return data["schedules"][index - 1]
    return None


def list_schedules(workspace: str, include_disabled: bool = True) -> list[dict[str, Any]]:
    schedules = load_schedule_data(workspace)["schedules"]
    if include_disabled:
        return schedules
    return [item for item in schedules if item.get("status") == "enabled"]


def list_recent_schedules(workspace: str, limit: int = 20) -> list[dict[str, Any]]:
    schedules = list_schedules(workspace, include_disabled=True)
    schedules.sort(key=lambda item: (item.get("updated_at") or "", item.get("created_at") or ""), reverse=True)
    return schedules[:limit]


def list_due_schedules(workspace: str, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or utc_now()
    schedules = list_schedules(workspace, include_disabled=False)
    due = [item for item in schedules if is_schedule_due(item, now=now)]
    due.sort(key=lambda item: (_resolve_next_run_at(item, now=now) or now, item.get("created_at") or ""))
    return due


def mark_schedules_started(workspace: str, schedule_ids: list[str], started_at: str | None = None) -> list[dict[str, Any]]:
    if not schedule_ids:
        return []
    data = load_schedule_data(workspace)
    now = started_at or utc_now_iso()
    changed: list[dict[str, Any]] = []
    for index, schedule in enumerate(data["schedules"], start=1):
        if schedule.get("id") not in schedule_ids:
            continue
        schedule["is_running"] = True
        schedule["last_started_at"] = now
        schedule["updated_at"] = now
        data["schedules"][index - 1] = _normalize_schedule(schedule, index)
        changed.append(data["schedules"][index - 1])
    if changed:
        save_schedule_data(workspace, data)
    return changed


def append_schedule_run(
    workspace: str,
    schedule_id: str,
    *,
    outcome: str,
    summary: str,
    output: str = "",
    error: str = "",
    started_at: str | None = None,
    finished_at: str | None = None,
) -> dict[str, Any] | None:
    if outcome not in RUN_OUTCOMES:
        outcome = "error"
    data = load_schedule_data(workspace)
    schedule = None
    schedule_index = -1
    for index, item in enumerate(data["schedules"]):
        if item.get("id") == schedule_id:
            schedule = item
            schedule_index = index
            break
    if schedule is None:
        return None

    started_at = started_at or schedule.get("last_started_at") or utc_now_iso()
    finished_at = finished_at or utc_now_iso()
    schedule["is_running"] = False
    schedule["run_count"] = int(schedule.get("run_count") or 0) + 1
    schedule["last_started_at"] = started_at
    schedule["last_run_at"] = finished_at
    schedule["last_finished_at"] = finished_at
    schedule["last_outcome"] = outcome
    schedule["last_result"] = summary.strip()
    schedule["last_error"] = error.strip()
    schedule["updated_at"] = finished_at
    data["schedules"][schedule_index] = _normalize_schedule(schedule, schedule_index + 1)
    save_schedule_data(workspace, data)

    run_record = {
        "schedule_id": schedule_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "outcome": outcome,
        "summary": summary.strip(),
        "output": output.strip(),
        "error": error.strip(),
        "run_count": data["schedules"][schedule_index]["run_count"],
    }
    run_path = schedule_run_log_path(workspace)
    run_path.parent.mkdir(parents=True, exist_ok=True)
    with run_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(run_record, ensure_ascii=False) + "\n")
    return run_record


def mark_schedule_failed(
    workspace: str,
    schedule_id: str,
    *,
    error: str,
    started_at: str | None = None,
) -> dict[str, Any] | None:
    return append_schedule_run(
        workspace,
        schedule_id,
        outcome="error",
        summary=error.strip() or "Scheduled run failed.",
        error=error,
        started_at=started_at,
    )


def list_schedule_runs(workspace: str, schedule_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    path = schedule_run_log_path(workspace)
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if schedule_id and record.get("schedule_id") != schedule_id:
            continue
        items.append(record)
        if len(items) >= limit:
            break
    return items


def render_schedule_file(workspace: str, include_recent_runs: bool = True, run_limit: int = 10) -> str:
    payload = load_schedule_data(workspace)
    if include_recent_runs:
        payload["recent_runs"] = list_schedule_runs(workspace, limit=run_limit)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def format_schedule_snapshot(schedules: list[dict[str, Any]]) -> str:
    if not schedules:
        return "[]"
    return json.dumps(
        [
            {
                "id": item["id"],
                "description": item["description"],
                "status": item["status"],
                "execution_type": item["execution_type"],
                "trigger": item["trigger"],
                "target": item["target"],
                "run_count": item["run_count"],
                "last_run_at": item["last_run_at"],
                "last_result": item["last_result"],
            }
            for item in schedules
        ],
        ensure_ascii=False,
        indent=2,
    )


def validate_script_command(workspace: str, command: str) -> tuple[bool, str]:
    text = str(command or "").strip()
    if not text:
        return False, "command is required for script schedules."
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        return False, f"invalid command: {exc}"
    if not tokens:
        return False, "command is required for script schedules."

    script_token = tokens[0]
    workspace_path = Path(workspace).resolve()
    script_path = Path(script_token)
    if not script_path.is_absolute():
        script_path = (workspace_path / script_path).resolve()
    else:
        script_path = script_path.resolve()

    try:
        script_path.relative_to(workspace_path)
    except ValueError:
        return False, "script command must start with a path inside the agent workspace."
    if not script_path.exists():
        return False, f"script path does not exist: {script_path}"
    if not script_path.is_file():
        return False, f"script path is not a file: {script_path}"
    return True, str(script_path)


def update_heartbeat(
    workspace: str,
    *,
    enabled: bool | None = None,
    interval_minutes: int | None = None,
) -> dict[str, Any]:
    data = load_heartbeat_data(workspace)
    if enabled is not None:
        data["enabled"] = bool(enabled)
        if not data["enabled"]:
            data["is_running"] = False
    if interval_minutes is not None:
        data["interval_minutes"] = max(5, int(interval_minutes))
    data["updated_at"] = utc_now_iso()
    save_heartbeat_data(workspace, data)
    return load_heartbeat_data(workspace)


def is_heartbeat_due(workspace: str, now: datetime | None = None) -> bool:
    now = now or utc_now()
    data = load_heartbeat_data(workspace)
    if not data.get("enabled"):
        return False
    if data.get("is_running"):
        return False
    interval = max(5, int(data.get("interval_minutes") or 60))
    anchor = _parse_iso_datetime(data.get("last_run_at")) or _parse_iso_datetime(data.get("created_at")) or now
    return now >= (anchor + timedelta(minutes=interval))


def mark_heartbeat_started(workspace: str, started_at: str | None = None) -> dict[str, Any]:
    data = load_heartbeat_data(workspace)
    now = started_at or utc_now_iso()
    data["is_running"] = True
    data["last_started_at"] = now
    data["updated_at"] = now
    save_heartbeat_data(workspace, data)
    return load_heartbeat_data(workspace)


def append_heartbeat_run(
    workspace: str,
    *,
    outcome: str,
    summary: str,
    output: str = "",
    error: str = "",
    started_at: str | None = None,
    finished_at: str | None = None,
) -> dict[str, Any]:
    if outcome not in RUN_OUTCOMES:
        outcome = "error"
    data = load_heartbeat_data(workspace)
    started_at = started_at or data.get("last_started_at") or utc_now_iso()
    finished_at = finished_at or utc_now_iso()
    data["is_running"] = False
    data["run_count"] = int(data.get("run_count") or 0) + 1
    data["last_started_at"] = started_at
    data["last_run_at"] = finished_at
    data["last_finished_at"] = finished_at
    data["last_outcome"] = outcome
    data["last_result"] = summary.strip()
    data["last_error"] = error.strip()
    data["updated_at"] = finished_at
    save_heartbeat_data(workspace, data)

    record = {
        "started_at": started_at,
        "finished_at": finished_at,
        "outcome": outcome,
        "summary": summary.strip(),
        "output": output.strip(),
        "error": error.strip(),
        "run_count": data["run_count"],
    }
    path = heartbeat_run_log_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def list_heartbeat_runs(workspace: str, limit: int = 20) -> list[dict[str, Any]]:
    path = heartbeat_run_log_path(workspace)
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        items.append(record)
        if len(items) >= limit:
            break
    return items


def render_heartbeat_file(workspace: str, include_recent_runs: bool = True, run_limit: int = 20) -> str:
    payload = load_heartbeat_data(workspace)
    if include_recent_runs:
        payload["recent_runs"] = list_heartbeat_runs(workspace, limit=run_limit)
    return json.dumps(payload, ensure_ascii=False, indent=2)
