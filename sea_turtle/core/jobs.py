"""Agent-scoped asynchronous job storage and step result handling."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

JOB_FILE_NAME = "jobs.json"
JOB_RUN_LOG_FILE_NAME = "job_runs.jsonl"

JOB_STATUSES = {
    "queued",
    "running",
    "waiting",
    "completed",
    "failed",
    "cancel_requested",
    "cancelled",
}
ACTIVE_JOB_STATUSES = {"queued", "running", "waiting", "cancel_requested"}
FINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
JOB_ERROR_TYPES = {"timeout", "provider_error", "tool_error", "parse_error", "runtime_error", ""}

DEFAULT_JOB_COOLDOWN_SECONDS = 30
MAX_CONSECUTIVE_FAILURES = 6
MAX_CONSECUTIVE_TIMEOUTS = 6
MAX_JOB_STEPS = 24
MAX_JOB_RUNTIME_MINUTES = 360


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def utc_now_iso() -> str:
    return utc_now().isoformat()


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


def job_file_path(workspace: str) -> Path:
    return Path(workspace) / JOB_FILE_NAME


def job_run_log_path(workspace: str) -> Path:
    return Path(workspace) / JOB_RUN_LOG_FILE_NAME


def default_job_data() -> dict[str, Any]:
    return {"version": 1, "jobs": []}


def _normalize_notes(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _normalize_artifacts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_job(job: dict[str, Any], index: int) -> dict[str, Any]:
    job_id = str(job.get("id") or f"job-{index}").strip() or f"job-{index}"
    status = str(job.get("status") or "queued").strip().lower()
    if status not in JOB_STATUSES:
        status = "queued"
    error_type = str(job.get("last_error_type") or "").strip().lower()
    if error_type not in JOB_ERROR_TYPES:
        error_type = "runtime_error" if error_type else ""

    created_at = str(job.get("created_at") or utc_now_iso())
    cooldown_seconds = job.get("cooldown_seconds", DEFAULT_JOB_COOLDOWN_SECONDS)
    try:
        cooldown_seconds = max(30, int(cooldown_seconds))
    except (TypeError, ValueError):
        cooldown_seconds = DEFAULT_JOB_COOLDOWN_SECONDS

    max_steps = job.get("max_steps", MAX_JOB_STEPS)
    try:
        max_steps = max(1, int(max_steps))
    except (TypeError, ValueError):
        max_steps = MAX_JOB_STEPS

    return {
        "id": job_id,
        "created_at": created_at,
        "updated_at": str(job.get("updated_at") or created_at),
        "source": str(job.get("source") or "telegram").strip() or "telegram",
        "chat_id": job.get("chat_id"),
        "user_id": job.get("user_id"),
        "title": str(job.get("title") or "").strip(),
        "user_request": str(job.get("user_request") or "").strip(),
        "status": status,
        "step_count": max(0, int(job.get("step_count") or 0)),
        "max_steps": max_steps,
        "cooldown_seconds": cooldown_seconds,
        "started_at": str(job.get("started_at") or ""),
        "finished_at": str(job.get("finished_at") or ""),
        "last_step_at": str(job.get("last_step_at") or ""),
        "last_started_at": str(job.get("last_started_at") or ""),
        "next_run_at": str(job.get("next_run_at") or created_at),
        "deadline_at": str(job.get("deadline_at") or ""),
        "progress_text": str(job.get("progress_text") or "").strip(),
        "current_phase": str(job.get("current_phase") or "queued").strip(),
        "result_summary": str(job.get("result_summary") or "").strip(),
        "result_file": str(job.get("result_file") or "").strip(),
        "working_notes": _normalize_notes(job.get("working_notes")),
        "artifacts": _normalize_artifacts(job.get("artifacts")),
        "consecutive_failures": max(0, int(job.get("consecutive_failures") or 0)),
        "consecutive_timeouts": max(0, int(job.get("consecutive_timeouts") or 0)),
        "retry_count": max(0, int(job.get("retry_count") or 0)),
        "recovery_mode": str(job.get("recovery_mode") or "none").strip() or "none",
        "last_error_type": error_type,
        "last_error": str(job.get("last_error") or "").strip(),
        "cancel_requested_at": str(job.get("cancel_requested_at") or ""),
    }


def load_job_data(workspace: str) -> dict[str, Any]:
    path = job_file_path(workspace)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = default_job_data()
    else:
        data = default_job_data()
        save_job_data(workspace, data)
    jobs = data.get("jobs", [])
    if not isinstance(jobs, list):
        jobs = []
    normalized = [_normalize_job(job, index) for index, job in enumerate(jobs, start=1)]
    return {"version": 1, "jobs": normalized}


def save_job_data(workspace: str, data: dict[str, Any]) -> None:
    path = job_file_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = {
        "version": 1,
        "jobs": [_normalize_job(job, index) for index, job in enumerate(data.get("jobs", []), start=1)],
    }
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def init_job_store(workspace: str) -> None:
    path = job_file_path(workspace)
    if not path.exists():
        save_job_data(workspace, default_job_data())


def _next_job_id(jobs: list[dict[str, Any]]) -> str:
    max_index = 0
    for job in jobs:
        text = str(job.get("id") or "").strip()
        if text.startswith("job-"):
            try:
                max_index = max(max_index, int(text.split("-", 1)[1]))
            except ValueError:
                continue
    return f"job-{max_index + 1}"


def list_recent_jobs(workspace: str, limit: int = 20) -> list[dict[str, Any]]:
    jobs = load_job_data(workspace)["jobs"]
    jobs.sort(key=lambda item: (item.get("updated_at") or "", item.get("created_at") or ""), reverse=True)
    return jobs[:limit]


def get_active_job(workspace: str) -> dict[str, Any] | None:
    jobs = load_job_data(workspace)["jobs"]
    active = [job for job in jobs if job.get("status") in ACTIVE_JOB_STATUSES]
    if not active:
        return None
    active.sort(key=lambda item: (item.get("updated_at") or "", item.get("created_at") or ""), reverse=True)
    return active[0]


def get_job(workspace: str, job_id: str) -> dict[str, Any] | None:
    for job in load_job_data(workspace)["jobs"]:
        if job.get("id") == job_id:
            return job
    return None


def create_job(
    workspace: str,
    *,
    source: str,
    chat_id: Any,
    user_id: Any,
    title: str,
    user_request: str,
    cooldown_seconds: int = DEFAULT_JOB_COOLDOWN_SECONDS,
    max_steps: int = MAX_JOB_STEPS,
    max_runtime_minutes: int = MAX_JOB_RUNTIME_MINUTES,
) -> dict[str, Any]:
    data = load_job_data(workspace)
    now = utc_now()
    deadline_at = now + timedelta(minutes=max(10, int(max_runtime_minutes)))
    job = _normalize_job({
        "id": _next_job_id(data["jobs"]),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "source": source,
        "chat_id": chat_id,
        "user_id": user_id,
        "title": title,
        "user_request": user_request,
        "status": "queued",
        "step_count": 0,
        "max_steps": max_steps,
        "cooldown_seconds": cooldown_seconds,
        "next_run_at": now.isoformat(),
        "deadline_at": deadline_at.isoformat(),
        "progress_text": "已接单，等待后台开始第一步处理。",
        "current_phase": "queued",
        "working_notes": [],
        "artifacts": [],
        "recovery_mode": "none",
    }, len(data["jobs"]) + 1)
    data["jobs"].append(job)
    save_job_data(workspace, data)
    return job


def request_job_cancel(workspace: str, job_id: str) -> dict[str, Any] | None:
    data = load_job_data(workspace)
    now = utc_now_iso()
    for index, job in enumerate(data["jobs"], start=1):
        if job.get("id") != job_id:
            continue
        status = job.get("status")
        if status in FINAL_JOB_STATUSES:
            return job
        if status in {"queued", "waiting"}:
            job["status"] = "cancelled"
            job["finished_at"] = now
            job["progress_text"] = "任务已取消。"
        else:
            job["status"] = "cancel_requested"
            job["cancel_requested_at"] = now
            job["progress_text"] = "已收到取消请求，将在当前步骤结束后停止。"
        job["updated_at"] = now
        data["jobs"][index - 1] = _normalize_job(job, index)
        save_job_data(workspace, data)
        return data["jobs"][index - 1]
    return None


def expire_job_if_needed(workspace: str, job_id: str) -> dict[str, Any] | None:
    data = load_job_data(workspace)
    now = utc_now()
    now_iso = now.isoformat()
    for index, job in enumerate(data["jobs"], start=1):
        if job.get("id") != job_id:
            continue
        deadline_at = _parse_iso_datetime(job.get("deadline_at"))
        if not deadline_at or now <= deadline_at or job.get("status") in FINAL_JOB_STATUSES:
            return job
        job["status"] = "failed"
        job["finished_at"] = now_iso
        job["updated_at"] = now_iso
        job["last_error_type"] = "runtime_error"
        job["last_error"] = "Maximum job runtime exceeded."
        job["progress_text"] = "后台任务因总时长达到上限而停止。"
        data["jobs"][index - 1] = _normalize_job(job, index)
        save_job_data(workspace, data)
        append_job_run_log(workspace, {
            "job_id": job_id,
            "step_index": data["jobs"][index - 1]["step_count"],
            "started_at": "",
            "finished_at": now_iso,
            "outcome": "runtime_error",
            "summary": "Maximum job runtime exceeded.",
            "output": "",
            "phase_after": data["jobs"][index - 1]["current_phase"],
            "artifacts_added": [],
            "error_type": "runtime_error",
        })
        return data["jobs"][index - 1]
    return None


def is_job_due(job: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or utc_now()
    if job.get("status") not in {"queued", "waiting"}:
        return False
    next_run_at = _parse_iso_datetime(job.get("next_run_at")) or now
    deadline_at = _parse_iso_datetime(job.get("deadline_at"))
    if deadline_at and now > deadline_at:
        return False
    return now >= next_run_at


def mark_job_started(workspace: str, job_id: str, started_at: str | None = None) -> dict[str, Any] | None:
    data = load_job_data(workspace)
    started = started_at or utc_now_iso()
    for index, job in enumerate(data["jobs"], start=1):
        if job.get("id") != job_id:
            continue
        job["status"] = "running"
        job["started_at"] = job.get("started_at") or started
        job["last_started_at"] = started
        job["updated_at"] = started
        data["jobs"][index - 1] = _normalize_job(job, index)
        save_job_data(workspace, data)
        return data["jobs"][index - 1]
    return None


def append_job_run_log(workspace: str, record: dict[str, Any]) -> None:
    path = job_run_log_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def list_job_runs(workspace: str, job_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    path = job_run_log_path(workspace)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    results: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if job_id and record.get("job_id") != job_id:
            continue
        results.append(record)
        if len(results) >= limit:
            break
    return results


def render_job_file(workspace: str, include_recent_runs: bool = True, run_limit: int = 10) -> str:
    payload = load_job_data(workspace)
    if include_recent_runs:
        payload["recent_runs"] = list_job_runs(workspace, limit=run_limit)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _append_unique(items: list[str], additions: list[str]) -> list[str]:
    result = [str(item).strip() for item in items if str(item).strip()]
    for item in additions:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def apply_job_step_result(
    workspace: str,
    job_id: str,
    *,
    summary: str,
    output: str,
    started_at: str | None,
    phase_after: str,
    progress_text: str,
    working_notes: list[str],
    artifacts_added: list[str],
    status: str,
    cooldown_seconds: int | None = None,
    result_summary: str = "",
    result_file: str = "",
) -> dict[str, Any] | None:
    data = load_job_data(workspace)
    finished_at = utc_now_iso()
    for index, job in enumerate(data["jobs"], start=1):
        if job.get("id") != job_id:
            continue
        new_status = status if status in {"waiting", "completed", "failed"} else "waiting"
        if job.get("status") == "cancel_requested":
            new_status = "cancelled"

        cooldown = max(30, int(cooldown_seconds or job.get("cooldown_seconds") or DEFAULT_JOB_COOLDOWN_SECONDS))
        job["step_count"] = int(job.get("step_count") or 0) + 1
        job["last_step_at"] = finished_at
        job["updated_at"] = finished_at
        job["last_error_type"] = ""
        job["last_error"] = ""
        job["consecutive_failures"] = 0
        job["consecutive_timeouts"] = 0
        job["retry_count"] = 0
        job["recovery_mode"] = "none"
        job["current_phase"] = phase_after or job.get("current_phase") or "working"
        job["progress_text"] = progress_text or summary or job.get("progress_text") or ""
        job["working_notes"] = _append_unique(job.get("working_notes") or [], working_notes)
        job["artifacts"] = _append_unique(job.get("artifacts") or [], artifacts_added)
        if result_summary:
            job["result_summary"] = result_summary.strip()
        if result_file:
            job["result_file"] = result_file.strip()
        job["status"] = new_status
        if new_status in FINAL_JOB_STATUSES:
            job["finished_at"] = finished_at
            job["next_run_at"] = finished_at
        else:
            job["cooldown_seconds"] = cooldown
            job["next_run_at"] = (utc_now() + timedelta(seconds=cooldown)).isoformat()
        if job["step_count"] >= int(job.get("max_steps") or MAX_JOB_STEPS) and new_status not in FINAL_JOB_STATUSES:
            job["status"] = "failed"
            job["finished_at"] = finished_at
            job["last_error_type"] = "runtime_error"
            job["last_error"] = "Maximum job steps reached."
            job["progress_text"] = "后台任务因步数达到上限而停止。"
        data["jobs"][index - 1] = _normalize_job(job, index)
        save_job_data(workspace, data)
        append_job_run_log(workspace, {
            "job_id": job_id,
            "step_index": data["jobs"][index - 1]["step_count"],
            "started_at": started_at or "",
            "finished_at": finished_at,
            "outcome": "success" if data["jobs"][index - 1]["status"] in {"waiting", "completed"} else data["jobs"][index - 1]["status"],
            "summary": summary.strip(),
            "output": output.strip(),
            "phase_after": data["jobs"][index - 1]["current_phase"],
            "artifacts_added": artifacts_added,
        })
        return data["jobs"][index - 1]
    return None


def record_job_failure(
    workspace: str,
    job_id: str,
    *,
    error_type: str,
    error_text: str,
    started_at: str | None = None,
) -> dict[str, Any] | None:
    data = load_job_data(workspace)
    finished_at = utc_now_iso()
    for index, job in enumerate(data["jobs"], start=1):
        if job.get("id") != job_id:
            continue
        normalized_type = error_type if error_type in JOB_ERROR_TYPES else "runtime_error"
        job["step_count"] = int(job.get("step_count") or 0) + 1
        job["updated_at"] = finished_at
        job["last_step_at"] = finished_at
        job["last_error_type"] = normalized_type
        job["last_error"] = error_text.strip()
        job["consecutive_failures"] = int(job.get("consecutive_failures") or 0) + 1
        job["retry_count"] = int(job.get("retry_count") or 0) + 1
        if normalized_type == "timeout":
            job["consecutive_timeouts"] = int(job.get("consecutive_timeouts") or 0) + 1
            job["recovery_mode"] = "narrow_scope"
        else:
            job["consecutive_timeouts"] = 0
        cooldown = max(
            int(job.get("cooldown_seconds") or DEFAULT_JOB_COOLDOWN_SECONDS),
            DEFAULT_JOB_COOLDOWN_SECONDS,
        )
        if normalized_type == "timeout":
            cooldown = min(cooldown * 2, 60)
        elif normalized_type == "provider_error":
            cooldown = min(max(cooldown, 60), 120)
        job["cooldown_seconds"] = cooldown
        job["progress_text"] = (
            "上一步执行超时，已自动缩小范围等待重试。"
            if normalized_type == "timeout"
            else f"上一步执行失败：{error_text.strip()}"
        )
        if job.get("status") == "cancel_requested":
            job["status"] = "cancelled"
            job["finished_at"] = finished_at
        elif (
            job["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES
            or job["consecutive_timeouts"] >= MAX_CONSECUTIVE_TIMEOUTS
            or job["step_count"] >= int(job.get("max_steps") or MAX_JOB_STEPS)
            or (_parse_iso_datetime(job.get("deadline_at")) and utc_now() > _parse_iso_datetime(job.get("deadline_at")))
        ):
            job["status"] = "failed"
            job["finished_at"] = finished_at
            if normalized_type == "timeout":
                job["progress_text"] = "后台任务连续超时过多，已停止。"
        else:
            job["status"] = "waiting"
            job["next_run_at"] = (utc_now() + timedelta(seconds=cooldown)).isoformat()
        data["jobs"][index - 1] = _normalize_job(job, index)
        save_job_data(workspace, data)
        append_job_run_log(workspace, {
            "job_id": job_id,
            "step_index": data["jobs"][index - 1]["step_count"],
            "started_at": started_at or "",
            "finished_at": finished_at,
            "outcome": normalized_type or "error",
            "summary": error_text.strip(),
            "output": "",
            "phase_after": data["jobs"][index - 1]["current_phase"],
            "artifacts_added": [],
            "error_type": normalized_type,
        })
        return data["jobs"][index - 1]
    return None


def extract_job_step_report(reply: str) -> tuple[str, dict[str, Any] | None]:
    marker = "JOB_STEP:"
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
