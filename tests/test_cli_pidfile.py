from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import argparse
import unittest

from sea_turtle import cli


class CliPidfileTests(unittest.TestCase):
    def test_clear_stale_pid_file_removes_dead_pid(self):
        with TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "daemon.pid"
            pid_path.write_text("999999", encoding="utf-8")
            with mock.patch.object(cli, "_load_cfg", return_value={"global": {"pid_file": str(pid_path)}}):
                with mock.patch("os.kill", side_effect=OSError("dead")):
                    stale_pid = cli._clear_stale_pid_file(None)
            self.assertEqual(stale_pid, 999999)
            self.assertFalse(pid_path.exists())

    def test_clear_stale_pid_file_keeps_live_pid(self):
        with TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "daemon.pid"
            pid_path.write_text("123", encoding="utf-8")
            with mock.patch.object(cli, "_load_cfg", return_value={"global": {"pid_file": str(pid_path)}}):
                with mock.patch("os.kill", return_value=None):
                    stale_pid = cli._clear_stale_pid_file(None)
            self.assertIsNone(stale_pid)
            self.assertTrue(pid_path.exists())


if __name__ == "__main__":
    unittest.main()
