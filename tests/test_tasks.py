import json
import tempfile
import unittest
from pathlib import Path

from sea_turtle.core.tasks import (
    apply_task_updates,
    create_task,
    extract_task_report,
    list_actionable_tasks,
    list_recent_tasks,
    load_task_data,
    save_task_data,
)


class TaskStoreTests(unittest.TestCase):
    def test_legacy_task_md_is_migrated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "task.md").write_text(
                "# Tasks\n- [ ] first task\n- [x] finished task\n",
                encoding="utf-8",
            )

            data = load_task_data(str(workspace))

            self.assertEqual(len(data["tasks"]), 2)
            self.assertEqual(data["tasks"][0]["title"], "first task")
            self.assertEqual(data["tasks"][0]["status"], "pending")
            self.assertEqual(data["tasks"][1]["status"], "done")
            self.assertTrue((workspace / "task.json").exists())

    def test_list_actionable_tasks_filters_finished_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            save_task_data(str(workspace), {
                "tasks": [
                    {"id": "task-1", "title": "pending", "status": "pending"},
                    {"id": "task-2", "title": "working", "status": "in_progress"},
                    {"id": "task-3", "title": "done", "status": "done"},
                    {"id": "task-4", "title": "failed", "status": "failed"},
                ]
            })

            tasks = list_actionable_tasks(str(workspace))
            self.assertEqual([task["id"] for task in tasks], ["task-1", "task-2"])

    def test_apply_task_updates_writes_status_and_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            save_task_data(str(workspace), {
                "tasks": [
                    {"id": "task-1", "title": "pending", "status": "pending"},
                ]
            })

            applied = apply_task_updates(str(workspace), [
                {"id": "task-1", "status": "done", "result": "finished", "notes": "ok"},
            ])

            self.assertEqual(applied[0]["status"], "done")
            saved = json.loads((workspace / "task.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["tasks"][0]["status"], "done")
            self.assertEqual(saved["tasks"][0]["result"], "finished")

    def test_extract_task_report_splits_summary_and_json(self):
        reply = (
            "SUMMARY:\n已完成 1 项。\n\n"
            "TASK_REPORT:\n```json\n"
            "{\"updates\":[{\"id\":\"task-1\",\"status\":\"done\",\"result\":\"ok\",\"notes\":\"\"}],"
            "\"summary\":\"已完成 1 项。\"}\n```"
        )

        summary, report = extract_task_report(reply)
        self.assertIn("已完成 1 项", summary)
        self.assertEqual(report["updates"][0]["id"], "task-1")

    def test_create_task_and_list_recent_tasks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            first = create_task(str(workspace), "first task")
            second = create_task(str(workspace), "second task", status="in_progress")
            apply_task_updates(str(workspace), [{"id": first["id"], "status": "done", "result": "ok"}])

            recent = list_recent_tasks(str(workspace), limit=2)
            self.assertEqual(len(recent), 2)
            self.assertEqual(recent[0]["id"], first["id"])
            self.assertEqual(recent[1]["id"], second["id"])


if __name__ == "__main__":
    unittest.main()
