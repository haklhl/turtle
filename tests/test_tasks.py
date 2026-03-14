import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sea_turtle.core.jobs import (
    apply_job_step_result,
    create_job,
    expire_job_if_needed,
    extract_job_step_report,
    get_active_job,
    is_job_due,
    list_job_runs,
    load_job_data,
    record_job_failure,
    request_job_cancel,
)
from sea_turtle.core.tasks import (
    append_heartbeat_run,
    append_schedule_run,
    create_schedule,
    is_heartbeat_due,
    is_schedule_due,
    list_due_schedules,
    list_heartbeat_runs,
    list_recent_schedules,
    list_schedule_runs,
    load_heartbeat_data,
    load_schedule_data,
    mark_heartbeat_started,
    mark_schedules_started,
    render_heartbeat_file,
    render_schedule_file,
    save_schedule_data,
    update_heartbeat,
    update_schedule,
)


class ScheduleStoreTests(unittest.TestCase):
    def test_legacy_task_json_is_migrated_to_disabled_schedules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "task.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {"id": "task-1", "title": "old pending", "created_at": "2026-03-01T00:00:00+00:00"},
                            {"id": "task-2", "title": "old done", "result": "ok"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            data = load_schedule_data(str(workspace))

            self.assertEqual(len(data["schedules"]), 2)
            self.assertEqual(data["schedules"][0]["status"], "disabled")
            self.assertTrue((workspace / "schedule.json").exists())

    def test_interval_schedule_becomes_due_after_interval(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            schedule = create_schedule(
                str(workspace),
                author="default",
                description="poll feed",
                execution_type="script",
                trigger={"type": "interval", "seconds": 300},
                target={"command": "./scripts/check.sh"},
            )
            data = load_schedule_data(str(workspace))
            data["schedules"][0]["created_at"] = "2026-03-07T00:00:00+00:00"
            data["schedules"][0]["updated_at"] = "2026-03-07T00:00:00+00:00"
            save_schedule_data(str(workspace), data)
            schedule = load_schedule_data(str(workspace))["schedules"][0]

            before_due = datetime(2026, 3, 7, 0, 4, 0, tzinfo=timezone.utc)
            at_due = datetime(2026, 3, 7, 0, 5, 0, tzinfo=timezone.utc)

            self.assertFalse(is_schedule_due(schedule, now=before_due))
            self.assertTrue(is_schedule_due(schedule, now=at_due))
            due = list_due_schedules(str(workspace), now=at_due)
            self.assertEqual([item["id"] for item in due], [schedule["id"]])

    def test_daily_schedule_runs_once_per_day(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            schedule = create_schedule(
                str(workspace),
                author="default",
                description="daily check",
                execution_type="script",
                trigger={"type": "daily", "time": "09:30", "timezone": "UTC"},
                target={"command": "./scripts/daily.sh"},
            )

            due_time = datetime(2026, 3, 7, 9, 30, 0, tzinfo=timezone.utc)
            self.assertTrue(is_schedule_due(schedule, now=due_time))

            mark_schedules_started(str(workspace), [schedule["id"]], started_at="2026-03-07T09:30:01+00:00")
            append_schedule_run(
                str(workspace),
                schedule["id"],
                outcome="success",
                summary="done",
                started_at="2026-03-07T09:30:01+00:00",
                finished_at="2026-03-07T09:30:10+00:00",
            )

            updated = load_schedule_data(str(workspace))["schedules"][0]
            self.assertFalse(is_schedule_due(updated, now=datetime(2026, 3, 7, 10, 0, 0, tzinfo=timezone.utc)))
            self.assertTrue(is_schedule_due(updated, now=datetime(2026, 3, 8, 9, 30, 0, tzinfo=timezone.utc)))

    def test_mark_started_and_append_run_updates_counters_and_logs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            schedule = create_schedule(
                str(workspace),
                author="default",
                description="script monitor",
                execution_type="script",
                trigger={"type": "interval", "seconds": 600},
                target={"command": "./scripts/monitor.sh"},
            )

            started = mark_schedules_started(str(workspace), [schedule["id"]], started_at="2026-03-07T00:10:00+00:00")
            self.assertTrue(started[0]["is_running"])

            append_schedule_run(
                str(workspace),
                schedule["id"],
                outcome="noop",
                summary="nothing changed",
                output="stdout line",
                started_at="2026-03-07T00:10:00+00:00",
                finished_at="2026-03-07T00:10:03+00:00",
            )

            saved = load_schedule_data(str(workspace))["schedules"][0]
            self.assertEqual(saved["run_count"], 1)
            self.assertFalse(saved["is_running"])
            self.assertEqual(saved["last_outcome"], "noop")

            runs = list_schedule_runs(str(workspace), schedule_id=schedule["id"], limit=5)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["summary"], "nothing changed")

    def test_update_schedule_can_disable_without_deleting_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            schedule = create_schedule(
                str(workspace),
                author="default",
                description="watch mempool",
                execution_type="script",
                trigger={"type": "interval", "seconds": 120},
                target={"command": "./scripts/watch.sh"},
            )

            updated = update_schedule(str(workspace), schedule["id"], status="disabled", description="watch mempool less often")
            self.assertIsNotNone(updated)
            self.assertEqual(updated["status"], "disabled")
            self.assertEqual(updated["description"], "watch mempool less often")

            recent = list_recent_schedules(str(workspace), limit=5)
            self.assertEqual(recent[0]["id"], schedule["id"])

    def test_render_schedule_file_includes_recent_runs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            schedule = create_schedule(
                str(workspace),
                author="default",
                description="daily brief",
                execution_type="script",
                trigger={"type": "daily", "time": "08:00", "timezone": "UTC"},
                target={"command": "./scripts/brief.sh"},
            )
            mark_schedules_started(str(workspace), [schedule["id"]], started_at="2026-03-07T08:00:00+00:00")
            append_schedule_run(
                str(workspace),
                schedule["id"],
                outcome="success",
                summary="brief sent",
                finished_at="2026-03-07T08:00:05+00:00",
            )

            rendered = json.loads(render_schedule_file(str(workspace)))
            self.assertEqual(rendered["schedules"][0]["id"], schedule["id"])
            self.assertEqual(rendered["recent_runs"][0]["summary"], "brief sent")


class HeartbeatStoreTests(unittest.TestCase):
    def test_heartbeat_defaults_to_disabled_60_minutes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            heartbeat = load_heartbeat_data(tmpdir)
            self.assertFalse(heartbeat["enabled"])
            self.assertEqual(heartbeat["interval_minutes"], 60)

    def test_heartbeat_interval_is_persisted_and_minimum_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            heartbeat = update_heartbeat(tmpdir, enabled=True, interval_minutes=3)
            self.assertTrue(heartbeat["enabled"])
            self.assertEqual(heartbeat["interval_minutes"], 5)

    def test_heartbeat_due_uses_persisted_last_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            update_heartbeat(tmpdir, enabled=True, interval_minutes=60)
            heartbeat = load_heartbeat_data(tmpdir)
            heartbeat["created_at"] = "2026-03-07T00:00:00+00:00"
            heartbeat["updated_at"] = "2026-03-07T00:00:00+00:00"
            from sea_turtle.core.tasks import save_heartbeat_data

            save_heartbeat_data(tmpdir, heartbeat)

            self.assertFalse(is_heartbeat_due(tmpdir, now=datetime(2026, 3, 7, 0, 59, 0, tzinfo=timezone.utc)))
            self.assertTrue(is_heartbeat_due(tmpdir, now=datetime(2026, 3, 7, 1, 0, 0, tzinfo=timezone.utc)))

    def test_heartbeat_runs_are_logged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            update_heartbeat(tmpdir, enabled=True, interval_minutes=60)
            mark_heartbeat_started(tmpdir, started_at="2026-03-07T01:00:00+00:00")
            append_heartbeat_run(
                tmpdir,
                outcome="noop",
                summary="nothing to do",
                finished_at="2026-03-07T01:00:05+00:00",
            )

            heartbeat = load_heartbeat_data(tmpdir)
            self.assertEqual(heartbeat["run_count"], 1)
            self.assertEqual(heartbeat["last_result"], "nothing to do")

            runs = list_heartbeat_runs(tmpdir, limit=20)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["summary"], "nothing to do")

            rendered = json.loads(render_heartbeat_file(tmpdir))
            self.assertEqual(rendered["recent_runs"][0]["summary"], "nothing to do")


class JobStoreTests(unittest.TestCase):
    def test_create_job_starts_as_active_and_due(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job = create_job(
                tmpdir,
                source="telegram",
                chat_id=1,
                user_id=2,
                title="alphaTON",
                user_request="给你个任务，整理资料",
            )

            active = get_active_job(tmpdir)
            self.assertEqual(active["id"], job["id"])
            self.assertTrue(is_job_due(active, now=datetime.now(timezone.utc)))

    def test_apply_job_step_result_persists_progress(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job = create_job(
                tmpdir,
                source="telegram",
                chat_id=1,
                user_id=2,
                title="alphaTON",
                user_request="整理资料",
            )

            updated = apply_job_step_result(
                tmpdir,
                job["id"],
                summary="done rewards",
                output="summary text",
                started_at="2026-03-12T00:00:00+00:00",
                phase_after="collect_growth",
                progress_text="已完成奖励数据抓取",
                working_notes=["奖励数据已保存"],
                artifacts_added=["/tmp/rewards.md"],
                status="waiting",
                cooldown_seconds=30,
            )

            self.assertEqual(updated["status"], "waiting")
            self.assertEqual(updated["current_phase"], "collect_growth")
            self.assertIn("奖励数据已保存", updated["working_notes"])
            self.assertEqual(updated["step_count"], 1)
            runs = list_job_runs(tmpdir, job_id=job["id"], limit=5)
            self.assertEqual(runs[0]["summary"], "done rewards")

    def test_record_timeout_does_not_fail_job_immediately(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job = create_job(
                tmpdir,
                source="telegram",
                chat_id=1,
                user_id=2,
                title="alphaTON",
                user_request="整理资料",
            )

            updated = record_job_failure(
                tmpdir,
                job["id"],
                error_type="timeout",
                error_text="命令超时（300秒）",
                started_at="2026-03-12T00:00:00+00:00",
            )

            self.assertEqual(updated["status"], "waiting")
            self.assertEqual(updated["consecutive_timeouts"], 1)
            self.assertEqual(updated["recovery_mode"], "narrow_scope")

    def test_cancel_waiting_job_becomes_cancelled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job = create_job(
                tmpdir,
                source="telegram",
                chat_id=1,
                user_id=2,
                title="alphaTON",
                user_request="整理资料",
            )
            updated = request_job_cancel(tmpdir, job["id"])
            self.assertEqual(updated["status"], "cancelled")

    def test_expire_job_marks_it_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            job = create_job(
                tmpdir,
                source="telegram",
                chat_id=1,
                user_id=2,
                title="alphaTON",
                user_request="整理资料",
            )
            data = load_job_data(tmpdir)
            data["jobs"][0]["deadline_at"] = "2026-03-11T00:00:00+00:00"
            from sea_turtle.core.jobs import save_job_data

            save_job_data(tmpdir, data)
            updated = expire_job_if_needed(tmpdir, job["id"])
            self.assertEqual(updated["status"], "failed")

    def test_extract_job_step_report_parses_json(self):
        reply = (
            "SUMMARY:\n已完成一步。\n\n"
            "JOB_STEP:\n```json\n"
            "{\"status\":\"waiting\",\"progress_text\":\"已完成一步\",\"current_phase\":\"next\","
            "\"working_notes\":[\"a\"],\"artifacts_added\":[],\"result_summary\":\"\",\"result_file\":\"\",\"cooldown_seconds\":30}\n```"
        )
        summary, report = extract_job_step_report(reply)
        self.assertIn("已完成一步", summary)
        self.assertEqual(report["current_phase"], "next")


if __name__ == "__main__":
    unittest.main()
