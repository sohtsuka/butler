"""`--version` names the package version and, when it can be found, the commit."""

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from butler import __version__, version  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"


class FakeDist:
    def __init__(self, root: Path, direct_url: dict | None):
        self.root, self.direct_url = root, direct_url

    def locate_file(self, name):
        return self.root / name

    def read_text(self, name):
        assert name == "direct_url.json"
        return None if self.direct_url is None else json.dumps(self.direct_url)


class DescribeTests(unittest.TestCase):
    def test_with_commit(self):
        with mock.patch.object(version, "commit", return_value=SHA):
            self.assertEqual(version.describe("butler"), f"butler {__version__} (0123456)")

    def test_dirty_checkout(self):
        with mock.patch.object(version, "commit", return_value=SHA + "-dirty"):
            self.assertEqual(version.describe("butler-task"),
                             f"butler-task {__version__} (0123456-dirty)")

    def test_without_commit(self):
        with mock.patch.object(version, "commit", return_value=None):
            self.assertEqual(version.describe("butler-board"), f"butler-board {__version__}")


class InstalledCommitTests(unittest.TestCase):
    def installed(self, dist):
        with mock.patch.object(version.metadata, "distribution", return_value=dist):
            return version.installed_commit()

    def test_git_install_records_its_commit(self):
        dist = FakeDist(version.PACKAGE_DIR.parent, {"url": "https://github.com/sohtsuka/butler",
                                                     "vcs_info": {"vcs": "git", "commit_id": SHA}})
        self.assertEqual(self.installed(dist), SHA)

    def test_install_from_a_directory_has_no_commit(self):
        dist = FakeDist(version.PACKAGE_DIR.parent, {"url": "file:///x", "dir_info": {}})
        self.assertIsNone(self.installed(dist))

    def test_metadata_of_another_copy_is_ignored(self):
        """A checkout run through bin/ must not report the commit of an unrelated install."""
        dist = FakeDist(Path("/somewhere/else"), {"vcs_info": {"vcs": "git", "commit_id": SHA}})
        self.assertIsNone(self.installed(dist))

    def test_not_installed(self):
        with mock.patch.object(version.metadata, "distribution",
                               side_effect=version.metadata.PackageNotFoundError):
            self.assertIsNone(version.installed_commit())


class CommandLineTests(unittest.TestCase):
    def test_every_command_answers_version(self):
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short=7", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        for script, prog in (("butler", "butler"), ("butler-task", "butler-task"),
                             ("board", "butler-board")):
            with self.subTest(prog=prog):
                out = subprocess.run([sys.executable, str(REPO / "bin" / script), "--version"],
                                     capture_output=True, text=True, check=True).stdout.strip()
                self.assertTrue(out.startswith(f"{prog} {__version__}"), out)
                if head:  # running from a git checkout
                    self.assertIn(f"({head}", out)


if __name__ == "__main__":
    unittest.main()
