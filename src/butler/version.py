"""What `--version` prints: the package version, and the commit it was built from when known.

Installs are pinned to a git SHA (`uv tool install git+…@<SHA>`), so the commit says more than the
version number does. uv and pip record it in the install's direct_url.json; a checkout run through
bin/ asks git instead."""

from __future__ import annotations

import argparse
import json
import subprocess
from importlib import metadata
from pathlib import Path

from butler import __version__

PACKAGE_DIR = Path(__file__).resolve().parent
CHECKOUT = PACKAGE_DIR.parent.parent  # src/butler -> repository root, when run from a checkout


def installed_commit() -> str | None:
    """The VCS commit this installed copy came from, or None (not from git, or not installed)."""
    try:
        dist = metadata.distribution("butler")
    except metadata.PackageNotFoundError:
        return None
    # Only trust the metadata if it describes this very copy, not some other installed butler.
    if Path(dist.locate_file("butler")).resolve() != PACKAGE_DIR:
        return None
    try:
        info = json.loads(dist.read_text("direct_url.json") or "{}")
    except json.JSONDecodeError:
        return None
    return info.get("vcs_info", {}).get("commit_id")


def checkout_commit() -> str | None:
    """HEAD of the checkout this copy runs from, with "-dirty" if it has local changes."""
    if not (CHECKOUT / ".git").exists():
        return None

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(CHECKOUT), *args], capture_output=True, text=True)

    try:
        head = git("rev-parse", "HEAD")
        if head.returncode != 0:
            return None
        dirty = git("status", "--porcelain", "--untracked-files=no").stdout.strip()
    except OSError:  # no git on PATH
        return None
    return head.stdout.strip() + ("-dirty" if dirty else "")


def commit() -> str | None:
    return installed_commit() or checkout_commit()


def describe(prog: str) -> str:
    """e.g. "butler 0.1.0 (4871fd6)"; the commit is left out when it cannot be found."""
    sha = commit()
    if not sha:
        return f"{prog} {__version__}"
    short = sha[:7] + ("-dirty" if sha.endswith("-dirty") else "")
    return f"{prog} {__version__} ({short})"


class _VersionAction(argparse.Action):
    """argparse's own "version" action needs the text up front; this asks git only when asked."""

    def __init__(self, option_strings, dest, prog: str, **kwargs):
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS,
                         help="show the version (and the commit) and exit", **kwargs)
        self.prog = prog

    def __call__(self, parser, namespace, values, option_string=None):
        print(describe(self.prog))
        parser.exit()


def add_flag(parser: argparse.ArgumentParser, prog: str) -> None:
    parser.add_argument("--version", action=_VersionAction, prog=prog)
