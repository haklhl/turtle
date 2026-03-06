"""Structured task storage and heartbeat task reporting."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TASK_FILE_NAME = "task.json"
LEGACY_TASK_FILE_NAME = "task.md"
ACTIVE_TASK_STATUSES = {"pending", "in_progress"}
FINAL_TASK_STATUSES = {"done", "cancelled"}
ALL_TASK_STATUSES = ACTIVE_TASK_STATUSES | FINAL_TASK_STATUSES


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def task_file_path(workspace: str) -> Path:
    return Path(workspace) / TASK_FILE_NAME


def _legacy_task_file_path(workspace: str) -> Path:
    return Path(workspace) / LEGACY_TASK_FILE_NAME


def default_task_data() -> dict[str, Any]:
    return {"version": 1, "tasks": []}


def _normalize_task(task: dict[str, Any], index: int) -> dict[str, Any]:
    task_id = str(task.get("id") or f"task-{index}")
    title = str(task.get("title") or task.get("description") or "").strip()
    status = str(task.get("status") or "pending").strip().lower()
    if status not in ALL_TASK_STATUSES:
        status = "pending"

    normalized = {
        "id": task_id,
        "title": title,
        "status": status,
        "result": str(task.get("result") or "").strip(),
        "notes": str(task.get("notes") or "").strip(),
        "created_at": str(task.get("created_at") or utc_now_iso()),
        "updated_at": str(task.get("updated_at") or utc_now_iso()),
        "last_heartbeat_at": str(task.get("last_heartbeat_at") or ""),
    }
    return normalized


def _load_legacy_task_md(workspace: str) -> dict[str, Any]:
    path = _legacy_task_file_path(workspace)
    if not path.exists():
        return default_task_data()

    tasks: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped.startswith("- ["):
            continue
        match = re.match(r"- \[( |x)\]\s*(.+)$", stripped, flags=re.IGNORECASE)
        if not match:
            continue
        done_mark, title = match.groups()
        tasks.append(_normalize_task({
            "id": f"task-{len(tasks) + 1}",
            "title": title,
            "status": "done" if done_mark.lower() == "x" else "pending",
            "notes": "Migrated from legacy task.md",
        }, index))
    return {"version": 1, "tasks": tasks}


def load_task_data(workspace: str) -> dict[str, Any]:
    path = task_file_path(workspace)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = default_task_data()
    else:
        data = _load_legacy_task_md(workspace)
        save_task_data(workspace, data)

    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        tasks = []
    normalized = [_normalize_task(task, idx) for idx, task in enumerate(tasks, start=1)]
    return {"version": 1, "tasks": normalized}


def save_task_data(workspace: str, data: dict[str, Any]) -> None:
    path = task_file_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = {"version": 1, "tasks": [_normalize_task(task, idx) for idx, task in enumerate(data.get("tasks", []), start=1)]}
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def init_task_store(workspace: str) -> None:
    path = task_file_path(workspace)
    if path.exists():
        return
    data = _load_legacy_task_md(workspace)
    save_task_data(workspace, data)


def list_actionable_tasks(workspace: str) -> list[dict[str, Any]]:
    data = load_task_data(workspace)
    return [task for task in data["tasks"] if task["status"] in ACTIVE_TASK_STATUSES]


def format_task_snapshot(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return "[]"
    return json.dumps(
        [
            {
                "id": task["id"],
                "title": task["title"],
                "status": task["status"],
                "result": task["result"],
                "notes": task["notes"],
                "updated_at": task["updated_at"],
            }
            for task in tasks
        ],
        ensure_ascii=False,
        indent=2,
    )


def touch_tasks_for_heartbeat(workspace: str, task_ids: list[str]) -> None:
    if not task_ids:
        return
    data = load_task_data(workspace)
    now = utc_now_iso()
    changed = False
    for task in data["tasks"]:
        if task["id"] in task_ids:
            task["last_heartbeat_at"] = now
            changed = True
    if changed:
        save_task_data(workspace, data)


def render_task_file(workspace: str) -> str:
    return json.dumps(load_task_data(workspace), ensure_ascii=False, indent=2)


def apply_task_updates(workspace: str, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not updates:
        return []
    data = load_task_data(workspace)
    tasks_by_id = {task["id"]: task for task in data["tasks"]}
    applied: list[dict[str, Any]] = []
    now = utc_now_iso()
    for update in updates:
        task_id = str(update.get("id") or "").strip()
        if not task_id or task_id not in tasks_by_id:
            continue
        task = tasks_by_id[task_id]
        status = str(update.get("status") or task["status"]).strip().lower()
        if status not in ALL_TASK_STATUSES:
            status = task["status"]
        result = str(update.get("result") or task.get("result") or "").strip()
        notes = str(update.get("notes") or task.get("notes") or "").strip()
        task["status"] = status
        task["result"] = result
        task["notes"] = notes
        task["updated_at"] = now
        applied.append({
            "id": task_id,
            "title": task["title"],
            "status": status,
            "result": result,
            "notes": notes,
        })
    if applied:
        save_task_data(workspace, data)
    return applied


def extract_task_report(reply: str) -> tuple[str, dict[str, Any] | None]:
    marker = "TASK_REPORT:"
    if marker not in (reply or ""):
        return (reply or "").strip(), None

    summary, report_part = reply.split(marker, 1)
    report_text = report_part.strip()
    if report_text.startswith("```"):
        lines = report_text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        report_text = "\n".join(lines).strip()
    try:
        report = json.loads(report_text)
    except json.JSONDecodeError:
        return summary.strip(), None
    return summary.strip(), report
