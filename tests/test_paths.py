"""Where state lives and how workers are told to reach it."""

import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from butler import paths, task  # noqa: E402


class HomeTests(unittest.TestCase):
    def test_butler_home_wins(self):
        with patch.dict(os.environ, {"BUTLER_HOME": "/tmp/x/../bh", "XDG_STATE_HOME": "/xdg"}):
            self.assertEqual(paths.home(), Path("/tmp/bh"))

    def test_xdg_state_home_is_the_default(self):
        env = {k: v for k, v in os.environ.items() if k != "BUTLER_HOME"}
        with patch.dict(os.environ, {**env, "XDG_STATE_HOME": "/xdg"}, clear=True):
            self.assertEqual(paths.home(), Path("/xdg/butler"))

    def test_worker_command_names_the_exe_and_home_quoted(self):
        with patch.dict(os.environ, {"BUTLER_TASK_EXE": "/opt/my tools/butler-task"}):
            cmd = paths.task_cmd(Path("/home/o/butler state"))
        self.assertEqual(shlex.split(cmd),
                         ["/opt/my tools/butler-task", "--home", "/home/o/butler state"])


class HomeFlagTests(unittest.TestCase):
    def test_home_flag_points_the_db_and_worker_command_at_it(self):
        with tempfile.TemporaryDirectory() as d, \
                patch.dict(os.environ, {"BUTLER_TASK_EXE": "/bin/butler-task"}), \
                patch.object(sys, "argv", ["butler-task", "--home", d, "list"]), \
                patch.object(task, "derive_state"), \
                patch.object(task, "DB_PATH"), patch.object(task, "TASK_CMD"):
            task.main()
            home = Path(d).resolve()
            self.assertEqual(task.DB_PATH, home / "db" / "butler.db")
            self.assertTrue(task.DB_PATH.is_file())
            self.assertIn(f"--home {shlex.quote(str(home))}", task.TASK_CMD)
            # A Codex worker may write the DB's directory, never BUTLER_HOME itself.
            argv = task.agent_argv("codex", "n", 1, [])
            self.assertEqual(argv[argv.index("--add-dir") + 1], str(home / "db"))
            self.assertNotIn(str(home), argv)


if __name__ == "__main__":
    unittest.main()
