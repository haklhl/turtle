from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import discord
import httpx
import yaml
from discord import app_commands


ROOT = Path("/home/tuantuanxiaobu/DarwinApex")


@dataclass(slots=True)
class DarwinApexSettings:
    db_path: Path
    runtime_root: Path
    meme_harpoon_base_url: str
    meme_harpoon_timeout_seconds: float
    help_channel_id: int | None
    default_model: str
    default_reasoning: str


def load_settings() -> DarwinApexSettings:
    paths_raw = yaml.safe_load((ROOT / "configs" / "paths.yaml").read_text(encoding="utf-8"))["paths"]
    app_raw = yaml.safe_load((ROOT / "configs" / "app.yaml").read_text(encoding="utf-8"))
    discord_raw = yaml.safe_load((ROOT / "configs" / "discord.yaml").read_text(encoding="utf-8"))["discord"]
    codex_raw = yaml.safe_load((ROOT / "configs" / "codex.yaml").read_text(encoding="utf-8"))["codex"]
    return DarwinApexSettings(
        db_path=Path(paths_raw["db_path"]).expanduser(),
        runtime_root=Path(paths_raw["runtime_root"]).expanduser(),
        meme_harpoon_base_url=str(app_raw["meme_harpoon_api"]["base_url"]),
        meme_harpoon_timeout_seconds=float(app_raw["meme_harpoon_api"]["timeout_seconds"]),
        help_channel_id=(
            int(discord_raw["help_channel_id"])
            if discord_raw.get("help_channel_id") not in (None, "", 0)
            else None
        ),
        default_model=str(codex_raw["default_model"]),
        default_reasoning=str(codex_raw["default_reasoning"]),
    )


class DarwinApexStore:
    def __init__(self, settings: DarwinApexSettings):
        self.settings = settings
        self.conn = sqlite3.connect(settings.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def get_override(self, scope: str, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value_json FROM settings_overrides WHERE scope = ? AND key = ?",
            (scope, key),
        ).fetchone()
        return None if row is None else row["value_json"]

    def current_model(self) -> str:
        raw = self.get_override("codex", "model")
        return json.loads(raw) if raw else self.settings.default_model

    def current_depth(self) -> str:
        raw = self.get_override("codex", "reasoning")
        return json.loads(raw) if raw else self.settings.default_reasoning

    def worker_status(self, worker_name: str) -> dict[str, Any] | None:
        raw = self.get_override("worker", worker_name)
        return json.loads(raw) if raw else None

    def latest_review_loop_event(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT event_type, trigger_type, reason, run_id, duration_seconds, created_at
            FROM review_loop_events
            ORDER BY rowid DESC
            LIMIT 1
            """
        ).fetchone()
        return None if row is None else dict(row)

    def review_failure_stats(self, limit: int = 20) -> dict[str, Any]:
        rows = self.conn.execute(
            """
            SELECT id, trigger_type, error_message, finished_at
            FROM runs
            WHERE run_type = 'review' AND status = 'failed'
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        failures = [dict(row) for row in rows]
        counts: dict[str, int] = {}
        for row in failures:
            reason = _normalize_failure_reason(row.get("error_message"))
            counts[reason] = counts.get(reason, 0) + 1
        top_reasons = [
            {"reason": reason, "count": count}
            for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        ]
        return {
            "window_size": limit,
            "failed_runs": len(failures),
            "top_reasons": top_reasons,
            "latest_failure": None if not failures else {
                **failures[0],
                "error_message": _normalize_failure_reason(failures[0].get("error_message")),
            },
        }

    def latest_release(self, repo_name: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT run_id, repo_name, branch, commit_sha, remote_url, pushed, pushed_ref,
                   replay_reason, validation_ok, replay_ok, created_at
            FROM release_events
            WHERE repo_name = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (repo_name,),
        ).fetchone()
        return None if row is None else dict(row)

    def latest_release_for_run(self, repo_name: str, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT run_id, repo_name, branch, commit_sha, remote_url, pushed, pushed_ref,
                   replay_reason, validation_ok, replay_ok, created_at
            FROM release_events
            WHERE repo_name = ? AND run_id = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (repo_name, run_id),
        ).fetchone()
        return None if row is None else dict(row)

    def latest_run(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id, run_type, status, trigger_type, started_at, finished_at, mh_version_before,
                   mh_version_after, summary, error_message, decision_summary_json
            FROM runs
            ORDER BY rowid DESC
            LIMIT 1
            """
        ).fetchone()
        return None if row is None else dict(row)

    def latest_completed_run(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id, run_type, status, trigger_type, started_at, finished_at, mh_version_before,
                   mh_version_after, summary, error_message, decision_summary_json
            FROM runs
            WHERE run_type = 'review' AND status != 'running'
            ORDER BY rowid DESC
            LIMIT 1
            """
        ).fetchone()
        return None if row is None else dict(row)

    def latest_cycle(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id, status, trigger_type, started_at, finished_at, summary, artifact_path, error_message, plan_json
            FROM strategic_cycles
            ORDER BY rowid DESC
            LIMIT 1
            """
        ).fetchone()
        return None if row is None else dict(row)

    def active_objectives(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT title, objective_type, priority, rationale, milestones_json, next_task,
                   target_files_json, success_signals_json, status, roadmap_status, progress_status, created_at, updated_at
            FROM strategic_objectives
            WHERE COALESCE(roadmap_status, status) IN ('active', 'carried_forward')
            ORDER BY
                CASE priority
                    WHEN 'high' THEN 0
                    WHEN 'medium' THEN 1
                    ELSE 2
                END,
                rowid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["milestones"] = json.loads(item.pop("milestones_json") or "[]")
            item["target_files"] = json.loads(item.pop("target_files_json") or "[]")
            item["success_signals"] = json.loads(item.pop("success_signals_json") or "[]")
            item["roadmap_status"] = item.get("roadmap_status") or item.get("status")
            item["progress_status"] = item.get("progress_status") or (
                "completed" if item.get("status") == "completed" else "not_started"
            )
            items.append(item)
        return items

    def iteration_artifact(self, run_id: str) -> dict[str, Any] | None:
        path = self.settings.runtime_root / "artifacts" / "iterations" / f"{run_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def open_help_requests(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, status, severity, category, title, blocking_reason, required_user_action,
                   verification_steps, resume_plan, created_by, related_run_id, dedupe_key,
                   notification_message, notified_at, last_reminded_at, reminder_count,
                   user_response_excerpt, outcome_summary, resolved_at, created_at, updated_at
            FROM assistance_requests
            WHERE status IN ('open', 'awaiting_user', 'user_replied', 'verifying')
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def help_request(self, request_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id, status, severity, category, title, blocking_reason, required_user_action,
                   verification_steps, resume_plan, created_by, related_run_id, dedupe_key,
                   notification_message, notified_at, last_reminded_at, reminder_count,
                   user_response_excerpt, outcome_summary, resolved_at, created_at, updated_at
            FROM assistance_requests
            WHERE id = ?
            LIMIT 1
            """,
            (request_id,),
        ).fetchone()
        return None if row is None else dict(row)


async def fetch_status_bundle() -> dict[str, Any]:
    settings = load_settings()
    store = DarwinApexStore(settings)
    async with httpx.AsyncClient(
        base_url=settings.meme_harpoon_base_url,
        timeout=settings.meme_harpoon_timeout_seconds,
    ) as client:
        status_resp = await client.get("/status")
        status_resp.raise_for_status()
        health_resp = await client.get("/health")
        health_resp.raise_for_status()
    status_payload = status_resp.json()
    health_payload = health_resp.json()
    return {
        "status": status_payload.get("data") or {},
        "health": health_payload.get("data") or {},
        "help_channel": f"<#{settings.help_channel_id}>" if settings.help_channel_id else "not_configured",
        "model": store.current_model(),
        "depth": store.current_depth(),
        "auto_evolution_enabled": json.loads(store.get_override("darwin", "auto_evolution_enabled"))
        if store.get_override("darwin", "auto_evolution_enabled") is not None
        else True,
        "latest_release": store.latest_release("MemeHarpoon"),
        "telegram_worker": store.worker_status("telegram_bot"),
        "discord_worker": store.worker_status("discord_bot"),
        "review_worker": store.worker_status("review_loop"),
        "latest_loop_event": store.latest_review_loop_event(),
        "failure_stats": store.review_failure_stats(),
    }


def load_roadmap_bundle() -> dict[str, Any]:
    store = DarwinApexStore(load_settings())
    return {
        "cycle": store.latest_cycle(),
        "objectives": store.active_objectives(limit=5),
    }


def load_goals_bundle() -> dict[str, Any]:
    store = DarwinApexStore(load_settings())
    return {
        "objectives": store.active_objectives(limit=10),
    }


def load_positions_bundle() -> dict[str, Any]:
    return {}


def load_last_iter_bundle() -> dict[str, Any]:
    store = DarwinApexStore(load_settings())
    running = store.latest_run()
    run = store.latest_completed_run() or running
    latest_release = None if run is None else store.latest_release_for_run("MemeHarpoon", run["id"])
    artifact = None if run is None else store.iteration_artifact(run["id"])
    return {
        "run": run,
        "running_run": running,
        "latest_release": latest_release,
        "artifact": artifact,
    }


def load_help_requests_bundle() -> dict[str, Any]:
    store = DarwinApexStore(load_settings())
    settings = load_settings()
    return {
        "requests": store.open_help_requests(limit=20),
        "help_channel": f"<#{settings.help_channel_id}>" if settings.help_channel_id else "not_configured",
    }


def load_help_request_bundle(request_id: str) -> dict[str, Any]:
    store = DarwinApexStore(load_settings())
    settings = load_settings()
    return {
        "request": store.help_request(request_id),
        "help_channel": f"<#{settings.help_channel_id}>" if settings.help_channel_id else "not_configured",
    }


def status_embed(bundle: dict[str, Any]) -> dict[str, Any]:
    status = bundle["status"]
    health = bundle["health"]
    service_up = bool((health or {}).get("alive"))
    live_broadcast = "on" if status.get("live_broadcast_enabled") else "off"
    latest_release = bundle.get("latest_release")
    latest_release_text = "n/a"
    if latest_release:
        latest_release_text = f"{(latest_release.get('commit_sha') or 'n/a')[:8]} @ {latest_release.get('pushed_ref') or 'local_only'}"
    latest_loop_event = bundle.get("latest_loop_event")
    latest_loop_text = "n/a"
    if latest_loop_event:
        latest_loop_text = (
            f"{latest_loop_event.get('event_type')} | "
            f"{latest_loop_event.get('trigger_type') or 'n/a'} | "
            f"{latest_loop_event.get('reason') or 'n/a'}"
        )
    failure_top = ", ".join(
        f"{item.get('reason')} x{item.get('count')}"
        for item in (bundle.get("failure_stats") or {}).get("top_reasons", [])[:3]
    ) or "none"
    color = 0x2ECC71 if service_up else 0xE67E22
    return {
        "title": "角都 状态面板",
        "color": color,
        "fields": [
            _field("MemeHarpoon 服务", "up" if service_up else "down"),
            _field("MemeHarpoon 交易态", str(status.get("state") or "n/a")),
            _field("实盘广播", live_broadcast),
            _field("Auto Evolution", "on" if bundle.get("auto_evolution_enabled") else "off"),
            _field("Help Channel", str(bundle.get("help_channel") or "n/a")),
            _field("Model", str(bundle.get("model") or "n/a")),
            _field("Depth", str(bundle.get("depth") or "n/a")),
            _field("MH Version", str(status.get("strategy_version") or "n/a")),
            _field("Latest Release", latest_release_text, inline=False),
            _field("Wallet Equity", f"{float(status.get('wallet_equity_usd', 0.0)):.2f} USD"),
            _field("Today PnL", f"{float(status.get('today_pnl_usd', 0.0)):.2f} USD ({float(status.get('today_pnl_pct', 0.0)) * 100:.2f}%)"),
            _field("Open Positions", str(status.get("open_positions") or 0)),
            _field("Risk State", str(status.get("risk_state") or "n/a")),
            _field("Workers", _worker_summary(bundle.get("telegram_worker"), bundle.get("discord_worker"), bundle.get("review_worker")), inline=False),
            _field("Last Loop", latest_loop_text, inline=False),
            _field("Recent Failures", failure_top, inline=False),
        ],
        "footer": {"text": f"Updated {status.get('updated_at') or 'n/a'} UTC"},
    }


def roadmap_embed(bundle: dict[str, Any]) -> dict[str, Any]:
    cycle = bundle.get("cycle")
    objectives = bundle.get("objectives") or []
    if cycle is None:
        return {
            "title": "角都 路线图",
            "color": 0x95A5A6,
            "description": "当前还没有长期路线图。",
        }
    fields = [
        _field("Cycle ID", cycle.get("id") or "n/a"),
        _field("Status", cycle.get("status") or "n/a"),
        _field("Trigger", cycle.get("trigger_type") or "n/a"),
        _field("Started", cycle.get("started_at") or "n/a", inline=False),
        _field("Summary", cycle.get("summary") or "n/a", inline=False),
    ]
    for index, item in enumerate(objectives[:3], start=1):
        fields.append(
            _field(
                f"Objective {index}",
                (
                    f"{item.get('title')}\n"
                    f"priority={item.get('priority')} | type={item.get('objective_type')}\n"
                    f"roadmap={item.get('roadmap_status') or item.get('status')} | progress={item.get('progress_status') or 'not_started'}\n"
                    f"next={item.get('next_task')}"
                ),
                inline=False,
            )
        )
    return {
        "title": "角都 长期路线图",
        "color": 0x3498DB,
        "fields": fields,
    }


def goals_embeds(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    objectives = bundle.get("objectives") or []
    if not objectives:
        return [{
            "title": "角都 长期目标",
            "color": 0x95A5A6,
            "description": "当前没有 active strategic objectives。",
        }]
    embeds: list[dict[str, Any]] = []
    total = len(objectives)
    for index, item in enumerate(objectives[:10], start=1):
        milestones = "\n".join(f"- {value}" for value in (item.get("milestones") or [])[:3]) or "n/a"
        embeds.append(
            {
                "title": f"角都 目标 {index}/{total}",
                "color": 0x9B59B6,
                "fields": [
                    _field("Title", item.get("title") or "n/a", inline=False),
                    _field("Priority", item.get("priority") or "n/a"),
                    _field("Type", item.get("objective_type") or "n/a"),
                    _field("Roadmap", item.get("roadmap_status") or item.get("status") or "n/a"),
                    _field("Progress", item.get("progress_status") or "not_started"),
                    _field("Rationale", item.get("rationale") or "n/a", inline=False),
                    _field("Next", item.get("next_task") or "n/a", inline=False),
                    _field("Milestones", milestones, inline=False),
                ],
            }
        )
    return embeds


def positions_embed(positions: list[dict[str, Any]]) -> dict[str, Any]:
    if not positions:
        return {
            "title": "角都 持仓",
            "color": 0x95A5A6,
            "description": "当前没有持仓。",
        }
    fields = []
    for item in positions[:10]:
        name = item.get("symbol") or item.get("mint") or "unknown"
        value = (
            f"entry={float(item.get('entry_notional_usd', 0.0)):.4f} USD\n"
            f"current={float(item.get('current_value_usd', 0.0)):.4f} USD\n"
            f"uPnL={float(item.get('unrealized_pnl_usd', 0.0)):.4f} USD"
        )
        fields.append(_field(name, value))
    return {
        "title": "角都 持仓",
        "color": 0xF1C40F,
        "fields": fields,
    }


def help_requests_embed(bundle: dict[str, Any]) -> dict[str, Any]:
    requests = bundle.get("requests") or []
    help_channel = str(bundle.get("help_channel") or "not_configured")
    if not requests:
        return {
            "title": "角都 外部求助",
            "color": 0x95A5A6,
            "description": f"当前没有待处理外部求助。\nHelp Channel: {help_channel}",
        }
    fields = [_field("Help Channel", help_channel, inline=False)]
    fields.extend(
        _field(
            item.get("id") or "n/a",
            f"{item.get('title')}\nstatus={item.get('status')} | category={item.get('category')} | severity={item.get('severity')}",
            inline=False,
        )
        for item in requests[:10]
    )
    return {
        "title": "角都 外部求助",
        "color": 0xE67E22,
        "fields": fields,
    }


def help_request_embed(bundle: dict[str, Any]) -> dict[str, Any]:
    request = bundle.get("request")
    help_channel = str(bundle.get("help_channel") or "not_configured")
    if request is None:
        return {
            "title": "角都 外部求助",
            "color": 0x95A5A6,
            "description": f"没查到这条求助。\nHelp Channel: {help_channel}",
        }
    return {
        "title": "角都 求助详情",
        "color": 0xE74C3C if request.get("severity") == "high" else 0xE67E22,
        "fields": [
            _field("Help Channel", help_channel, inline=False),
            _field("ID", request.get("id") or "n/a", inline=False),
            _field("Status", request.get("status") or "n/a"),
            _field("Severity", request.get("severity") or "n/a"),
            _field("Category", request.get("category") or "n/a"),
            _field("Title", request.get("title") or "n/a", inline=False),
            _field("Why", request.get("blocking_reason") or "n/a", inline=False),
            _field("Action", request.get("required_user_action") or "n/a", inline=False),
            _field("Verify", request.get("verification_steps") or "n/a", inline=False),
            _field("Resume", request.get("resume_plan") or "n/a", inline=False),
            _field("Outcome", request.get("outcome_summary") or "n/a", inline=False),
            _field("User Reply", request.get("user_response_excerpt") or "n/a", inline=False),
        ],
    }


def last_iter_embeds(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    run = bundle.get("run")
    running_run = bundle.get("running_run")
    latest_release = bundle.get("latest_release")
    artifact = bundle.get("artifact") or {}
    if run is None:
        return [{
            "title": "角都 最近一轮迭代",
            "color": 0x95A5A6,
            "description": "当前还没有迭代记录。",
        }]
    decision_summary = {}
    raw_decision = run.get("decision_summary_json")
    if isinstance(raw_decision, str):
        try:
            decision_summary = json.loads(raw_decision)
        except Exception:
            decision_summary = {}
    proposal = artifact.get("proposal") or {}
    gitops = artifact.get("gitops") or {}
    strategy = artifact.get("iteration_strategy") or {}
    focus_mode = artifact.get("focus_mode") or {}
    alignment = artifact.get("strategy_alignment") or {}
    diagnostics = artifact.get("diagnostics") or {}
    failure_stats = diagnostics.get("review_failure_stats") or {}
    risk_grade = proposal.get("risk_grade") or decision_summary.get("risk_grade") or "n/a"
    release_text = "n/a"
    if latest_release and latest_release.get("run_id") == run.get("id"):
        release_text = f"{(latest_release.get('commit_sha') or 'n/a')[:8]} @ {latest_release.get('pushed_ref') or 'local_only'}"
    running_text = "none"
    if running_run and running_run.get("status") == "running" and running_run.get("id") != run.get("id"):
        running_text = f"{running_run.get('id')} | {running_run.get('trigger_type')} | {running_run.get('started_at')}"
    top_failures = ", ".join(
        f"{item.get('reason')} x{item.get('count')}" for item in failure_stats.get("top_reasons", [])[:3]
    ) or "none"
    quality = proposal.get("quality_score")
    quality_text = f"{quality:.2f}" if isinstance(quality, (int, float)) else "n/a"
    commit_message = proposal.get("commit_message") or decision_summary.get("commit_message") or "n/a"
    apply_gate = decision_summary.get("apply_gate") or "n/a"
    embeds = [
        {
            "title": "角都 最近一轮迭代",
            "color": 0x2ECC71 if run.get("status") == "completed" else 0xE74C3C,
            "fields": [
                _field("Run ID", run.get("id") or "n/a", inline=False),
                _field("Status", run.get("status") or "n/a"),
                _field("Trigger", run.get("trigger_type") or "n/a"),
                _field("Started", run.get("started_at") or "n/a", inline=False),
                _field("Finished", run.get("finished_at") or "n/a", inline=False),
                _field("Release", release_text, inline=False),
                _field("Strategy", f"{strategy.get('mode', 'n/a')} | {strategy.get('reason', 'n/a')}", inline=False),
                _field("Focus Mode", f"{focus_mode.get('mode', 'n/a')} | {focus_mode.get('reason', focus_mode.get('primary_goal', 'n/a'))}", inline=False),
                _field("Apply Gate", apply_gate, inline=False),
            ],
        },
        {
            "title": "角都 迭代细节",
            "color": 0x34495E,
            "fields": [
                _field("Proposal", proposal.get("proposal_title") or decision_summary.get("proposal_title") or "n/a", inline=False),
                _field("Commit", commit_message, inline=False),
                _field("Quality", quality_text),
                _field("Risk Grade", str(risk_grade)),
                _field("Strategy Alignment", f"{alignment.get('aligned', 'n/a')} | {alignment.get('reason', 'n/a')}", inline=False),
                _field(
                    "GitOps",
                    (
                        f"commit={(gitops.get('commit_sha') or 'n/a')[:8] if gitops.get('commit_sha') else 'n/a'} | "
                        f"merged={gitops.get('merged_to_main')} | "
                        f"pushed={gitops.get('pushed')} | "
                        f"reason={gitops.get('push_skipped_reason') or gitops.get('merge_error') or 'n/a'}"
                    ),
                    inline=False,
                ),
                _field("Recent Failures", top_failures, inline=False),
                _field("Running Review", running_text, inline=False),
                _field("Error", run.get("error_message") or "n/a", inline=False),
            ],
        },
    ]
    summary_text = _normalize_summary_text(run.get("summary") or "n/a")
    for index, chunk in enumerate(_split_description(summary_text), start=1):
        embeds.append(
            {
                "title": "角都 迭代摘要" if index == 1 else f"角都 迭代摘要 {index}",
                "color": 0x16A085,
                "description": chunk,
            }
        )
    return embeds


def _normalize_failure_reason(raw: str | None) -> str:
    if not raw:
        return "unknown_failure"
    line = raw.strip().splitlines()[0].strip()
    lowered = raw.lower()
    if "codex_timeout" in lowered or "timeoutexpired" in lowered:
        return "codex_timeout"
    if line.startswith("local replay validation failed:"):
        return "local_replay_validation_failed"
    if "PermissionError" in line and "/runtime" in raw:
        return "runtime_path_permission_error"
    if len(line) > 120:
        line = line[:117] + "..."
    return line


def _field(name: str, value: str, inline: bool = True) -> dict[str, Any]:
    return {"name": name, "value": _truncate(value), "inline": inline}


def _truncate(value: str | None, limit: int = 1024) -> str:
    text = (value or "n/a").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _worker_summary(telegram_worker: dict | None, discord_worker: dict | None, review_worker: dict | None) -> str:
    parts = []
    for name, worker in (("telegram", telegram_worker), ("discord", discord_worker), ("review", review_worker)):
        if not worker:
            parts.append(f"{name}=n/a")
            continue
        parts.append(f"{name}={worker.get('state', 'unknown')}")
    return " | ".join(parts)


def _split_description(text: str, limit: int = 3800) -> list[str]:
    value = (text or "n/a").strip()
    if len(value) <= limit:
        return [value]
    parts: list[str] = []
    start = 0
    while start < len(value):
        end = min(start + limit, len(value))
        if end < len(value):
            split_at = value.rfind(" | ", start, end)
            if split_at > start:
                end = split_at + 1
        parts.append(value[start:end].strip())
        start = end
    return parts or ["n/a"]


def _normalize_summary_text(text: str) -> str:
    value = (text or "n/a").strip()
    if " | " not in value:
        return value
    parts = [part.strip() for part in value.split(" | ") if part.strip()]
    return "\n".join(f"- {part}" for part in parts) if parts else value


def register_discord_commands(bot, channel, agent_id: str) -> None:
    if agent_id != "kakuzu":
        return

    async def ensure_allowed(interaction: discord.Interaction) -> bool:
        if not channel._is_user_allowed(interaction.user.id, agent_id, "discord"):
            await interaction.response.send_message("⛔ Unauthorized.", ephemeral=True)
            return False
        return True

    @bot.tree.command(name="apex_status", description="查看 DarwinApex 与 MemeHarpoon 状态")
    async def cmd_apex_status(interaction: discord.Interaction):
        if not await ensure_allowed(interaction):
            return
        bundle = await fetch_status_bundle()
        await interaction.response.send_message(
            embed=discord.Embed.from_dict(status_embed(bundle)),
            ephemeral=True,
        )

    @bot.tree.command(name="roadmap", description="查看 DarwinApex 长期路线图")
    async def cmd_roadmap(interaction: discord.Interaction):
        if not await ensure_allowed(interaction):
            return
        bundle = load_roadmap_bundle()
        await interaction.response.send_message(
            embed=discord.Embed.from_dict(roadmap_embed(bundle)),
            ephemeral=True,
        )

    @bot.tree.command(name="goals", description="查看 DarwinApex 长期目标")
    async def cmd_goals(interaction: discord.Interaction):
        if not await ensure_allowed(interaction):
            return
        bundle = load_goals_bundle()
        embeds = [discord.Embed.from_dict(item) for item in goals_embeds(bundle)]
        await interaction.response.send_message(embeds=embeds[:10], ephemeral=True)

    @bot.tree.command(name="last_iter", description="查看最近一轮 DarwinApex 迭代")
    async def cmd_last_iter(interaction: discord.Interaction):
        if not await ensure_allowed(interaction):
            return
        bundle = load_last_iter_bundle()
        embeds = [discord.Embed.from_dict(item) for item in last_iter_embeds(bundle)]
        await interaction.response.send_message(embeds=embeds[:10], ephemeral=True)

    @bot.tree.command(name="positions", description="查看当前持仓")
    async def cmd_positions(interaction: discord.Interaction):
        if not await ensure_allowed(interaction):
            return
        settings = load_settings()
        async with httpx.AsyncClient(
            base_url=settings.meme_harpoon_base_url,
            timeout=settings.meme_harpoon_timeout_seconds,
        ) as client:
            resp = await client.get("/positions")
            resp.raise_for_status()
        payload = resp.json().get("data") or {}
        items = payload.get("items", [])
        await interaction.response.send_message(
            embed=discord.Embed.from_dict(positions_embed(items)),
            ephemeral=True,
        )

    @bot.tree.command(name="help_requests", description="查看当前待处理外部求助")
    async def cmd_help_requests(interaction: discord.Interaction):
        if not await ensure_allowed(interaction):
            return
        bundle = load_help_requests_bundle()
        await interaction.response.send_message(
            embed=discord.Embed.from_dict(help_requests_embed(bundle)),
            ephemeral=True,
        )

    @bot.tree.command(name="help_request", description="查看某条外部求助详情")
    @app_commands.describe(request_id="求助 ID")
    async def cmd_help_request(interaction: discord.Interaction, request_id: str):
        if not await ensure_allowed(interaction):
            return
        bundle = load_help_request_bundle(request_id.strip())
        await interaction.response.send_message(
            embed=discord.Embed.from_dict(help_request_embed(bundle)),
            ephemeral=True,
        )
