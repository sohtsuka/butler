"""Isolated task CLI regressions: no real herdr or operator database access."""

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys


SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "task"
loader = importlib.machinery.SourceFileLoader("task_under_test", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
task = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = task
loader.exec_module(task)


class TaskTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.enterContext(patch.object(task, "DB_PATH", self.root / "state" / "butler.db"))
        self.enterContext(patch.object(task.subprocess, "run", side_effect=AssertionError(
            "unexpected subprocess; real herdr must never run in these tests")))
        self.enterContext(patch.object(task, "herdr", side_effect=AssertionError(
            "herdr responses must be explicitly mocked")))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.db = task.connect()
        self.addCleanup(self.db.close)

    def seed(self):
        return self.db.execute(
            "INSERT INTO tasks (title, cwd, brief, created_at, pane_id, report, reported_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test", str(self.root), "brief", task.now(), "w999:p1", "old report", task.now()),
        ).lastrowid

    def report(self, task_id, text):
        # Use a separate connection, like the worker's `task report` process.
        with patch.object(task.sys, "stdin", io.StringIO(text)):
            task.cmd_report(argparse.Namespace(id=task_id))


class SendTests(TaskTest):
    def send(self, task_id, prompt):
        with patch.object(task, "live_pane", return_value="w999:p1"), \
                patch.object(task, "herdr", side_effect=prompt):
            task.cmd_send(argparse.Namespace(id=task_id, text="follow up"))

    def test_report_arriving_during_prompt_is_kept(self):
        task_id = self.seed()
        self.send(task_id, lambda *args: self.report(task_id, "new report"))
        self.assertEqual(task.load(self.db, task_id).report, "new report")

    def test_identical_report_in_same_second_is_kept(self):
        task_id = self.seed()
        timestamp = task.load(self.db, task_id).reported_at
        with patch.object(task, "now", return_value=timestamp):
            self.send(task_id, lambda *args: self.report(task_id, "old report"))
        self.assertEqual(task.load(self.db, task_id).report, "old report")

    def test_accepted_prompt_clears_previous_report(self):
        task_id = self.seed()
        self.send(task_id, lambda *args: {})
        self.assertIsNone(task.load(self.db, task_id).report)

    def test_rejected_prompt_restores_previous_report(self):
        task_id = self.seed()
        before = task.load(self.db, task_id)
        def reject(*args):
            raise task.HerdrError("agent_blocked", "dialog")
        with self.assertRaises(SystemExit):
            self.send(task_id, reject)
        self.assertEqual(task.load(self.db, task_id), before)

    def test_error_does_not_restore_over_a_new_report(self):
        task_id = self.seed()
        def fail_after_report(*args):
            self.report(task_id, "new report")
            raise task.HerdrError("timeout", "lost reply")
        with self.assertRaises(task.HerdrError):
            self.send(task_id, fail_after_report)
        self.assertEqual(task.load(self.db, task_id).report, "new report")


if __name__ == "__main__":
    unittest.main()
