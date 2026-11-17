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
REAL_RUN = task.subprocess.run


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


class FailedStartTests(TaskTest):
    def git(self, *args):
        return REAL_RUN(["git", "-C", str(self.root), *args], check=True,
                        capture_output=True, text=True).stdout.strip()

    def failed_start(self, commit, cleanup_fails=False):
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.root / "initial.txt").write_text("initial\n")
        self.git("add", "initial.txt")
        self.git("commit", "-m", "initial")
        worktree = self.root / "worker"
        branch = "butler/0001-startup"

        def fake_herdr(*args):
            if args[:2] == ("worktree", "create"):
                self.git("worktree", "add", "-b", branch, str(worktree))
                return {"root_pane": {"pane_id": "w999:p1", "tab_id": "w999:t1"},
                        "worktree": {"path": str(worktree)},
                        "workspace": {"workspace_id": "w999"}}
            if args[:2] == ("agent", "start"):
                (worktree / "worker.txt").write_text("completed worker output\n")
                if commit:
                    self.git("-C", str(worktree), "add", "worker.txt")
                    self.git("-C", str(worktree), "commit", "-m", "worker output")
                raise task.HerdrError("timeout", "worker did not become idle in time")
            if args[:2] == ("agent", "rename"):
                raise task.HerdrError("agent_not_found", "worker exited before rename")
            if args == ("workspace", "close", "w999"):
                if cleanup_fails:
                    raise task.HerdrError("unavailable", "cleanup could not reach herdr")
                return {}
            self.fail(f"unexpected herdr call: {args}")

        def only_fixture_git(argv, **kwargs):
            self.assertEqual(argv[:3], ["git", "-C", str(self.root)])
            return REAL_RUN(argv, **kwargs)

        args = argparse.Namespace(cwd=str(self.root), title="startup", agent="claude",
                                  no_worktree=False)
        with patch.object(task.sys, "stdin", io.StringIO("do work")), \
                patch.object(task, "git_toplevel", return_value=str(self.root)), \
                patch.object(task, "commit_dirs", return_value=[]), \
                patch.object(task, "herdr", side_effect=fake_herdr), \
                patch.object(task.subprocess, "run", side_effect=only_fixture_git):
            with self.assertRaises((SystemExit, task.HerdrError)):
                task.cmd_start(args)
        return worktree, branch

    def test_failed_rename_keeps_uncommitted_worker_output(self):
        worktree, _ = self.failed_start(commit=False)
        self.assertTrue((worktree / "worker.txt").is_file())

    def test_failed_rename_keeps_committed_worker_branch(self):
        _, branch = self.failed_start(commit=True)
        self.assertIn(branch, self.git("branch", "--list", branch))
        self.assertEqual(self.git("show", f"{branch}:worker.txt"), "completed worker output")

    def test_failed_cleanup_leaves_task_visible_for_retry(self):
        self.failed_start(commit=False, cleanup_fails=True)
        self.assertIsNone(task.load(self.db, 1).closed_at)
        self.assertEqual([t.id for t in task.all_tasks(self.db, include_closed=False)], [1])

    def test_successful_cleanup_closes_task(self):
        self.failed_start(commit=False)
        self.assertIsNotNone(task.load(self.db, 1).closed_at)


if __name__ == "__main__":
    unittest.main()
