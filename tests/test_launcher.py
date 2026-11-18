"""The launcher finds its task CLI however it was invoked."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class TaskExeTests(unittest.TestCase):
    def test_relative_launcher_finds_its_sibling_after_chdir(self):
        """`bin/butler` from a checkout chdirs to BUTLER_HOME before it looks up butler-task."""
        with tempfile.TemporaryDirectory() as home:
            code = ("import os, runpy, sys; sys.argv = ['bin/butler'];"
                    "sys.path.insert(0, 'src'); from butler import launcher;"
                    f"os.chdir({home!r}); print(launcher.task_exe())")
            env = {**os.environ, "BUTLER_HOME": home, "PATH": "/usr/bin:/bin"}
            out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                                 capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, str(REPO / "bin" / "butler-task"))


if __name__ == "__main__":
    unittest.main()
